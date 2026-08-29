"""
Subtitle and transcript formatting.

Whisper already produces per-segment start/end times for every run, and the
translator produces one string per segment aligned to that same list. Both were
computed and then thrown away. This turns them into the files people actually
ask for after a dub.

No model runs here. This is formatting over data the pipeline already has.
"""

from __future__ import annotations


def _clock(seconds: float, comma: bool) -> str:
    """HH:MM:SS,mmm for SRT, HH:MM:SS.mmm for WebVTT."""
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _cues(segments: list, texts: list | None) -> list:
    """
    Pair each segment's timing with the text to show. `texts` overrides the
    segment's own text when present, which is how the translated track reuses
    the source timings.
    """
    out = []
    for i, seg in enumerate(segments):
        body = (texts[i] if texts and i < len(texts) else seg.get("text", "")) or ""
        body = body.strip()
        if not body:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))
        # A zero or inverted duration produces a cue no player will show.
        if end <= start:
            end = start + 0.6
        out.append((start, end, body))
    return out


def srt(segments: list, texts: list | None = None) -> str:
    lines = []
    for n, (start, end, body) in enumerate(_cues(segments, texts), 1):
        lines.append(str(n))
        lines.append(f"{_clock(start, True)} --> {_clock(end, True)}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def vtt(segments: list, texts: list | None = None) -> str:
    lines = ["WEBVTT", ""]
    for start, end, body in _cues(segments, texts):
        lines.append(f"{_clock(start, False)} --> {_clock(end, False)}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def plain(segments: list, texts: list | None = None) -> str:
    """One line per spoken segment, which reads better than one long blob."""
    return "\n".join(body for _, _, body in _cues(segments, texts)) + "\n"
