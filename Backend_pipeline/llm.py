"""
Script intelligence.

The hard problems left in this pipeline are linguistic, not visual. The
translator works one sentence at a time with no memory of the last one, and a
translation that does not fit its slot gets time-stretched, which is what makes
a dub sound robotic. Both are language problems, so this is where a language
model earns its place.

Provider-agnostic on purpose. Ollama is the default because it keeps the
property every other stage has - nothing leaves the machine - but a 7B model is
markedly weaker at Indian languages than at English, and this product is about
Indian languages. So a user can point it at their own Claude, Gemini or OpenAI
key instead.

What crosses the network differs enormously by choice, and it is worth being
precise: with Ollama, nothing. With a hosted key, ONE LINE of already-translated
text per rewrite. The video, the audio, the voice and the reference clip never
leave the machine under any setting.

Raw HTTP for every provider rather than four SDKs: this is one small JSON
request per provider, and a dependency per vendor would be a poor trade for a
tool whose whole pitch is that it runs on your own hardware.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time

import requests


class Unavailable(RuntimeError):
    """No model is reachable. Callers degrade instead of failing."""


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

# `suggested` is a convenience list, not a whitelist: any model id the provider
# accepts can be typed in. Only the Anthropic ids here are taken from current
# first-party documentation; the others are sensible defaults that the user can
# correct, which is why the field is free text everywhere.
PROVIDERS = {
    "ollama": {
        "label": "Ollama (this machine)",
        "needs_key": False,
        "default_model": "qwen2.5:7b",
        "suggested": ["qwen2.5:7b", "aya-expanse:8b", "gemma2:9b", "qwen2.5:14b"],
        "note": "Nothing leaves your machine. Weakest at Indian languages.",
    },
    "anthropic": {
        "label": "Claude",
        "needs_key": True,
        "default_model": "claude-opus-5",
        "suggested": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
        "note": "Sends one line of translated text per rewrite.",
    },
    "google": {
        "label": "Gemini",
        "needs_key": True,
        "default_model": "gemini-2.0-flash",
        "suggested": ["gemini-2.0-flash"],
        "note": "Sends one line of translated text per rewrite.",
    },
    "openai": {
        "label": "OpenAI",
        "needs_key": True,
        "default_model": "gpt-4o-mini",
        "suggested": ["gpt-4o-mini", "gpt-4o"],
        "note": "Sends one line of translated text per rewrite.",
    },
    "grok": {
        "label": "Grok (xAI)",
        "needs_key": True,
        "default_model": "grok-2-latest",
        "suggested": ["grok-2-latest"],
        "note": "Sends one line of translated text per rewrite.",
    },
    "groq": {
        "label": "Groq Cloud",
        "needs_key": True,
        "default_model": "llama-3.3-70b-versatile",
        "suggested": ["llama-3.3-70b-versatile", "moonshotai/kimi-k2-instruct",
                      "qwen/qwen3-32b", "llama-3.1-8b-instant"],
        "note": "Sends one line of translated text per rewrite. Fast enough "
                "that fitting a phrase feels instant.",
    },
    "custom": {
        "label": "Other (OpenAI-compatible)",
        "needs_key": True,
        "default_model": "",
        "suggested": [],
        "needs_url": True,
        "note": "Any endpoint that speaks the OpenAI chat API: vLLM, Together, OpenRouter, LM Studio.",
    },
}

TIMEOUT = float(os.getenv("MULTIVA_LLM_TIMEOUT", "90"))

# Kept outside the checkout: an API key does not belong anywhere near a repo.
SETTINGS_PATH = os.path.expanduser(
    os.getenv("MULTIVA_SETTINGS", "~/.multiva/llm.json"))

_DEFAULTS = {
    "provider": "ollama",
    "model": PROVIDERS["ollama"]["default_model"],
    "keys": {},
    "ollama_host": "http://127.0.0.1:11434",
    # Base URL for the OpenAI-compatible "custom" provider, without /chat/completions.
    "custom_url": "",
}


def load_settings() -> dict:
    """Stored settings, overlaid on the defaults and then on the environment."""
    data = dict(_DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in _DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception as e:                                   # noqa: BLE001
        print(f"[LLM] Ignoring unreadable settings at {SETTINGS_PATH}: {e}")

    # The environment wins, so a deployment can pin configuration without a
    # settings file and without the UI being able to override it.
    if os.getenv("MULTIVA_LLM_PROVIDER"):
        chosen = os.environ["MULTIVA_LLM_PROVIDER"]
        # Naming a provider but no model would otherwise carry the previous
        # provider's model across, and send "qwen2.5:7b" to Groq. The explicit
        # model override below still wins when one is given.
        if chosen != data["provider"] and chosen in PROVIDERS:
            data["model"] = PROVIDERS[chosen]["default_model"]
        data["provider"] = chosen
    if os.getenv("MULTIVA_LLM_MODEL"):
        data["model"] = os.environ["MULTIVA_LLM_MODEL"]
    if os.getenv("MULTIVA_LLM_URL") or os.getenv("OLLAMA_HOST"):
        data["ollama_host"] = (os.getenv("MULTIVA_LLM_URL")
                               or os.getenv("OLLAMA_HOST"))

    keys = dict(data.get("keys") or {})
    for provider, var in (("anthropic", "ANTHROPIC_API_KEY"),
                          ("google", "GEMINI_API_KEY"),
                          ("openai", "OPENAI_API_KEY"),
                          ("groq", "GROQ_API_KEY"),
                          ("grok", "XAI_API_KEY")):
        if os.getenv(var):
            keys[provider] = os.environ[var]
    data["keys"] = keys

    if data["provider"] not in PROVIDERS:
        data["provider"] = "ollama"
    return data


def save_settings(provider: str, model: str, key: str = None,
                  ollama_host: str = None, custom_url: str = None) -> dict:
    """
    Persist the choice, and the key if one was given.

    A blank key is not the same as no key: blank clears the stored one, absent
    leaves it alone, so re-saving the provider does not wipe a working key.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")

    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            stored = json.load(f)
    except Exception:                                        # noqa: BLE001
        stored = {}
    if not isinstance(stored, dict):
        stored = {}

    stored["provider"] = provider
    stored["model"] = (model or "").strip() or PROVIDERS[provider]["default_model"]
    if ollama_host:
        stored["ollama_host"] = ollama_host.strip()
    if custom_url is not None:
        stored["custom_url"] = custom_url.strip()

    keys = dict(stored.get("keys") or {})
    if key is not None:
        if key.strip():
            keys[provider] = key.strip()
        else:
            keys.pop(provider, None)
    stored["keys"] = keys

    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    tmp = f"{SETTINGS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2)
    # Owner-only, before it is in place: the file holds API keys.
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, SETTINGS_PATH)

    _probe.update(at=0.0, ok=False)          # force a fresh reachability check
    return load_settings()


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

_probe = {"at": 0.0, "ok": False}
PROBE_TTL = 30.0


def configured() -> bool:
    """
    Whether a model can actually be reached right now.

    For a hosted provider that means a key is present; the request itself is
    the real test. For Ollama it means the server answers and the model is
    pulled, because "installed but not pulled" is the common case and produces
    a confusing failure at the worst moment.
    """
    if os.getenv("MULTIVA_LLM", "").lower() in ("0", "off", "false", "no"):
        return False

    cfg = load_settings()
    provider = cfg["provider"]

    if PROVIDERS[provider]["needs_key"]:
        return bool(cfg["keys"].get(provider))

    now = time.time()
    if now - _probe["at"] < PROBE_TTL:
        return _probe["ok"]
    ok = False
    try:
        r = requests.get(f"{cfg['ollama_host'].rstrip('/')}/api/tags", timeout=2.0)
        if r.ok:
            names = {m.get("name", "") for m in r.json().get("models", [])}
            want = cfg["model"]
            ok = any(n == want or n.split(":")[0] == want.split(":")[0]
                     for n in names)
    except Exception:                                        # noqa: BLE001
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def installed_models() -> list:
    """Models Ollama has pulled, so the UI can offer them instead of guessing."""
    cfg = load_settings()
    try:
        r = requests.get(f"{cfg['ollama_host'].rstrip('/')}/api/tags", timeout=2.0)
        if r.ok:
            return sorted(m.get("name", "") for m in r.json().get("models", []))
    except Exception:                                        # noqa: BLE001
        pass
    return []


def status() -> dict:
    """
    What the client should say about script intelligence.

    Never returns a key. It reports only whether one is stored, which is all
    the UI needs to render its state.
    """
    cfg = load_settings()
    provider = cfg["provider"]
    return {
        "enabled": configured(),
        "provider": provider,
        "model": cfg["model"],
        "local": not PROVIDERS[provider]["needs_key"],
        "has_key": bool(cfg["keys"].get(provider)),
        "ollama_host": cfg["ollama_host"],
        "custom_url": cfg.get("custom_url", ""),
        "providers": {
            name: {k: v for k, v in spec.items()}
            for name, spec in PROVIDERS.items()
        },
        "installed": installed_models() if provider == "ollama" else [],
    }


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def _post(url: str, *, headers: dict = None, payload: dict) -> dict:
    try:
        r = requests.post(url, headers=headers or {}, json=payload, timeout=TIMEOUT)
    except requests.Timeout as e:
        raise Unavailable(f"The model timed out after {TIMEOUT:.0f}s.") from e
    except requests.RequestException as e:
        raise Unavailable(f"Could not reach the model: {e}") from e
    if not r.ok:
        # The provider's own message is far more useful than a status code.
        raise Unavailable(f"{r.status_code} from the model: {r.text[:200]}")
    try:
        return r.json()
    except json.JSONDecodeError as e:
        raise Unavailable("The model returned a response that was not JSON.") from e


def _ollama(cfg, system, user, max_tokens):
    data = _post(
        f"{cfg['ollama_host'].rstrip('/')}/api/chat",
        payload={
            "model": cfg["model"], "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            # Low but not zero: at zero a failed rewrite repeats verbatim on
            # every retry, so the loop can never escape.
            "options": {"temperature": 0.35, "top_p": 0.9,
                        "num_predict": max_tokens},
        })
    return (data.get("message") or {}).get("content", "")


def _anthropic(cfg, system, user, max_tokens):
    data = _post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": cfg["keys"]["anthropic"],
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        payload={
            "model": cfg["model"], "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        })
    # content is a list of blocks; only the text ones are the answer.
    return "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")


def _google(cfg, system, user, max_tokens):
    model = cfg["model"]
    data = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": cfg["keys"]["google"],
                 "content-type": "application/json"},
        payload={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.35,
                                 "maxOutputTokens": max_tokens},
        })
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError):
        # A safety block returns a well-formed response with no candidate.
        raise Unavailable("Gemini returned no text for that line.")


# OpenAI, xAI and most self-hosted servers speak the same chat API, so they
# share one implementation and differ only in base URL.
_OPENAI_COMPATIBLE = {
    "openai": "https://api.openai.com/v1",
    "grok": "https://api.x.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def _openai(cfg, system, user, max_tokens, provider="openai"):
    base = (_OPENAI_COMPATIBLE.get(provider)
            or (cfg.get("custom_url") or "").rstrip("/"))
    if not base:
        raise Unavailable(
            "No base URL set for the custom provider. Give it the endpoint's "
            "root, without /chat/completions.")
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['keys'][provider]}",
               "content-type": "application/json"}
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.35,
        "max_completion_tokens": max_tokens,
    }
    try:
        data = _post(url, headers=headers, payload=payload)
    except Unavailable as e:
        # Older models take `max_tokens` and reject the newer name. Retrying is
        # cheaper than maintaining a list of which model wants which.
        if "max_completion_tokens" not in str(e):
            raise
        payload.pop("max_completion_tokens")
        payload["max_tokens"] = max_tokens
        data = _post(url, headers=headers, payload=payload)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise Unavailable("OpenAI returned no text for that line.")


_CALL = {
    "ollama": _ollama,
    "anthropic": _anthropic,
    "google": _google,
    "openai": lambda c, s, u, m: _openai(c, s, u, m, "openai"),
    "grok": lambda c, s, u, m: _openai(c, s, u, m, "grok"),
    "groq": lambda c, s, u, m: _openai(c, s, u, m, "groq"),
    "custom": lambda c, s, u, m: _openai(c, s, u, m, "custom"),
}


def complete(system: str, user: str, max_tokens: int = 300) -> str:
    """One short completion. Deterministic: this is a rewrite, not brainstorming."""
    cfg = load_settings()
    provider = cfg["provider"]

    if PROVIDERS[provider]["needs_key"] and not cfg["keys"].get(provider):
        raise Unavailable(
            f"No API key set for {PROVIDERS[provider]['label']}. "
            f"Add one in the studio, or switch back to Ollama.")
    if provider == "ollama" and not configured():
        raise Unavailable(
            f"Ollama is not serving {cfg['model']}. "
            f"Start it and run: ollama pull {cfg['model']}")

    return (_CALL[provider](cfg, system, user, max_tokens) or "").strip()


# ---------------------------------------------------------------------------
# Output scrubbing
# ---------------------------------------------------------------------------

# A small model answers the question and then explains itself. Everything below
# would otherwise be spoken aloud in the dub.
_PREAMBLE = re.compile(
    r"^\s*(here(?:'s| is)[^:\n]*:|shortened[^:\n]*:|rewritten[^:\n]*:"
    r"|output[^:\n]*:|answer[^:\n]*:|result[^:\n]*:)\s*",
    re.IGNORECASE)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def clean_line(raw: str) -> str:
    """Reduce a chatty completion to the one line meant to be spoken."""
    text = _FENCE.sub("", (raw or "").strip()).strip()
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return ""

    # Drop lines that are commentary rather than script.
    kept = []
    for ln in lines:
        stripped = _PREAMBLE.sub("", ln).strip()
        if not stripped:
            continue
        # Numbered or bulleted alternatives: keep the content, not the marker.
        stripped = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", stripped)
        if re.match(r"^\(?(note|explanation|this )", stripped, re.IGNORECASE):
            continue
        kept.append(stripped)
    if not kept:
        return ""

    # When it offers several, the first is the answer and the rest are notes.
    # Only wrapping quotes come off: the danda and other sentence punctuation
    # are part of the line and belong in what gets spoken.
    return kept[0].strip().strip('"').strip("'").strip()


# ---------------------------------------------------------------------------
# Length-aware rewriting
# ---------------------------------------------------------------------------

SHORTEN_SYSTEM = """\
You shorten one line of dubbing script so it can be spoken in less time.

The line replaces the original audio of a video, so it must still say the same
thing. Shorten it by REPHRASING, never by deleting what it is about.

Rules:
- Reply with ONLY the shortened line. No preamble, no quotes, no explanation.
- Write in the SAME language and script as the input. Never translate it.
- Every noun, name, number and subject in the input must still be present.
- Shorten by removing filler words, contracting phrases, and choosing shorter
  synonyms. Never remove a word that carries meaning.
- If it cannot be shortened without losing meaning, reply with it unchanged.
- It must read as natural spoken speech."""


# Numbers are the one kind of content whose loss is unambiguous and which a
# listener will not catch. Words are murkier: string matching cannot tell
# "deleted the noun" from "used a shorter synonym", and those are the rewrites
# we are asking for. So numbers are enforced and words are reported.
_NUM = re.compile(r"\d+")

# Four codepoints, not five: in Devanagari and other Indic scripts the matras
# are separate codepoints, so a content word like "फोकस" is only four long and
# a higher threshold misses exactly what matters.
_WORD = re.compile(r"[^\s,.;:!?।\"\'()]{4,}")


def missing_numbers(original: str, rewrite: str) -> list:
    """Digits present in the original and absent from the rewrite."""
    return [n for n in _NUM.findall(original) if n not in rewrite]


def dropped_words(original: str, rewrite: str) -> list:
    """
    Words that may have been dropped rather than rephrased.

    Advisory only. A shorter synonym registers here too, which is why this is
    shown to the person reviewing the line instead of blocking the rewrite.
    Stems are compared so inflection does not read as deletion.
    """
    return [w for w in _WORD.findall(original) if w[:3] not in rewrite]


def shorten(text: str, language: str, ratio: float, source_text: str = "") -> str:
    """
    Rewrite `text` to about `ratio` of its spoken length (0 < ratio < 1).

    `source_text` is the line the translation came from. With it the model can
    shorten by returning to what was meant, instead of trimming a translation
    whose intent it cannot see.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Nothing to rewrite")
    ratio = max(0.35, min(0.98, float(ratio)))
    target = max(1, round(len(text) * ratio))

    parts = [f"Language: {language}"]
    if source_text.strip():
        parts.append(f"Original line it was translated from:\n{source_text.strip()}")
    parts += [
        f"Line to shorten:\n{text}",
        "",
        f"Make it about {target} characters, down from {len(text)}. "
        f"Reply with only the shortened line.",
    ]

    out = clean_line(complete(SHORTEN_SYSTEM, "\n".join(parts)))
    if not out:
        raise Unavailable("The local model returned nothing usable.")

    lost = missing_numbers(text, out)
    if lost:
        # One retry naming the numbers. A dub that changes a date or a figure
        # is worse than one that runs long, and nobody listening will catch it.
        retry = "\n".join(parts + [
            "",
            f"Your previous attempt dropped these numbers: {', '.join(lost)}. "
            "They must all appear. Shorten something else instead.",
        ])
        out = clean_line(complete(SHORTEN_SYSTEM, retry)) or out
        if missing_numbers(text, out):
            raise Unavailable(
                f"The rewrite kept dropping {', '.join(lost[:3])}. "
                "Shorten this line by hand.")
    return out
