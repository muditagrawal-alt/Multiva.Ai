"""
Script intelligence, running locally.

The hard problems left in this pipeline are linguistic, not visual. The
translator works one sentence at a time with no memory of the last one, and a
translation that does not fit its slot gets time-stretched, which is what makes
a dub sound robotic. Both are language problems, so this is where a language
model earns its place.

It runs against Ollama on this machine, so the property every other stage has
holds here too: nothing leaves the box. The cost is capability. A 7B model
follows instructions far less reliably than a frontier one, so everything here
is written defensively: the output is scrubbed of preamble, and every caller
checks the result rather than trusting it.
"""

from __future__ import annotations

import json
import os
import re
import time

import requests

HOST = (os.getenv("MULTIVA_LLM_URL")
        or os.getenv("OLLAMA_HOST")
        or "http://127.0.0.1:11434").rstrip("/")
if not HOST.startswith("http"):
    HOST = f"http://{HOST}"

MODEL = os.getenv("MULTIVA_LLM_MODEL", "qwen2.5:7b")
TIMEOUT = float(os.getenv("MULTIVA_LLM_TIMEOUT", "90"))

# Availability is a network check, and /api/health asks for it on every poll.
_probe = {"at": 0.0, "ok": False}
PROBE_TTL = 30.0


class Unavailable(RuntimeError):
    """No model is reachable. Callers degrade instead of failing."""


def configured() -> bool:
    """
    Whether the local model is reachable and pulled.

    Cached briefly: this is called from the health endpoint, which the studio
    polls, and a TCP round trip per poll is a waste.
    """
    if os.getenv("MULTIVA_LLM", "").lower() in ("0", "off", "false", "no"):
        return False
    now = time.time()
    if now - _probe["at"] < PROBE_TTL:
        return _probe["ok"]
    ok = False
    try:
        r = requests.get(f"{HOST}/api/tags", timeout=2.0)
        if r.ok:
            names = {m.get("name", "") for m in r.json().get("models", [])}
            # Ollama reports "qwen2.5:7b"; a bare "qwen2.5" should still match.
            ok = any(n == MODEL or n.split(":")[0] == MODEL.split(":")[0]
                     for n in names)
    except Exception:                                        # noqa: BLE001
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def status() -> dict:
    """What the client should say about script intelligence."""
    return {"enabled": configured(), "model": MODEL, "host": HOST, "local": True}


def complete(system: str, user: str, max_tokens: int = 300) -> str:
    """One short completion. Deterministic: this is a rewrite, not brainstorming."""
    if not configured():
        raise Unavailable(
            f"No local model. Start Ollama and run: ollama pull {MODEL}")
    try:
        r = requests.post(
            f"{HOST}/api/chat",
            json={
                "model": MODEL,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "options": {
                    # Low but not zero: at 0 a failed rewrite repeats verbatim
                    # on every retry, so the loop can never escape.
                    "temperature": 0.35,
                    "top_p": 0.9,
                    "num_predict": max_tokens,
                },
            },
            timeout=TIMEOUT,
        )
    except requests.Timeout as e:
        raise Unavailable(f"The local model timed out after {TIMEOUT:.0f}s.") from e
    except requests.RequestException as e:
        raise Unavailable(f"Could not reach the local model at {HOST}.") from e

    if not r.ok:
        raise Unavailable(f"The local model returned {r.status_code}: {r.text[:160]}")
    try:
        return (r.json()["message"]["content"] or "").strip()
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise Unavailable("The local model returned an unreadable response.") from e


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
