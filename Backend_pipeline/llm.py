"""
Script intelligence.

The hard problems left in this pipeline are linguistic, not visual. The
translator works one sentence at a time with no memory of the last one, and a
translation that does not fit its slot gets time-stretched, which is what makes
a dub sound robotic. Both are language problems, so this is where a language
model earns its place.

Privacy: every other stage runs on this machine. This one does not. Text sent
here leaves the machine, so it is off by default and every caller has to handle
`Unavailable`. Nothing is sent unless the user configures a key and asks for it.
"""

from __future__ import annotations

import os

MODEL = os.getenv("MULTIVA_LLM_MODEL", "claude-opus-5")

_client = None
_checked = False


class Unavailable(RuntimeError):
    """No model is configured. Callers degrade instead of failing."""


def configured() -> bool:
    """Whether script intelligence can run at all, without constructing a client."""
    if os.getenv("MULTIVA_LLM", "").lower() in ("0", "off", "false", "no"):
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _get_client():
    global _client, _checked
    if _client is not None:
        return _client
    if not configured():
        raise Unavailable(
            "Script intelligence is off. Set ANTHROPIC_API_KEY to enable it. "
            "Note that it sends script text off this machine; every other "
            "stage stays local.")
    if not _checked:
        _checked = True
    try:
        import anthropic
    except ImportError as e:                                 # noqa: BLE001
        raise Unavailable("The anthropic package is not installed.") from e
    _client = anthropic.Anthropic()
    return _client


def complete(system: str, user: str, max_tokens: int = 512) -> str:
    """
    One short completion. Effort is low because every use here is a single
    focused rewrite, not a reasoning task.
    """
    client = _get_client()
    import anthropic

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIStatusError as e:
        raise Unavailable(f"The model returned {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise Unavailable("Could not reach the model. Check your connection.") from e

    if response.stop_reason == "refusal":
        raise Unavailable("The model declined to rewrite that text.")

    return "".join(b.text for b in response.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# Length-aware rewriting
# ---------------------------------------------------------------------------

SHORTEN_SYSTEM = """\
You rewrite lines of dubbing script so they fit the time available when spoken.

A dub replaces the original audio of a video. Each line has a fixed slot, and a
line that runs long gets compressed until it sounds unnatural. Your job is to
say the same thing in fewer syllables.

Rules:
- Write in the SAME language as the input. Never translate.
- Preserve the meaning, the facts, and the register. Do not add or drop claims.
- Keep names, numbers and technical terms exactly as they appear.
- Prefer shorter synonyms and tighter phrasing over deleting content.
- The result must read as natural spoken speech, not a telegram.
- Reply with the rewritten line and nothing else. No quotes, no notes, no
  explanation, no alternatives."""


def shorten(text: str, language: str, ratio: float, source_text: str = "") -> str:
    """
    Rewrite `text` to be roughly `ratio` of its spoken length (0 < ratio < 1).

    `source_text` is the original line the translation came from. Given it, the
    model can shorten by going back to what was meant rather than trimming a
    translation it cannot see the intent behind.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Nothing to rewrite")
    ratio = max(0.35, min(0.98, float(ratio)))
    target = max(1, round(len(text) * ratio))

    parts = [
        f"Language: {language}",
        f"Line to shorten:\n{text}",
        "",
        f"It currently takes about {1 / ratio:.2f} times longer to say than the "
        f"time available. Aim for roughly {target} characters, down from "
        f"{len(text)}.",
    ]
    if source_text.strip():
        parts.insert(1, f"The line it was translated from:\n{source_text.strip()}")

    out = complete(SHORTEN_SYSTEM, "\n".join(parts))

    # A model that answers with a preamble would otherwise be spoken aloud.
    first = out.strip().strip('"').strip()
    if "\n" in first:
        first = max(first.split("\n"), key=len).strip().strip('"').strip()
    if not first:
        raise Unavailable("The model returned an empty rewrite.")
    return first
