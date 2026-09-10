"""
Subtitle and transcript formatting.

Whisper already produces per-segment start/end times for every run, and the
translator produces one string per segment aligned to that same list. Both were
computed and then thrown away. This turns them into the files people actually
ask for after a dub.

No model runs here. This is formatting over data the pipeline already has.
"""

from __future__ import annotations

import os
import re


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


_CAN_BURN = None


def can_burn() -> bool:
    """
    Whether this ffmpeg can draw subtitles onto the picture.

    The `subtitles` filter needs libass, and plenty of builds do not have it —
    including what Homebrew installed on the machine this was written on. That
    is not a reason to fail: a muxed subtitle track needs no filter at all.
    """
    global _CAN_BURN
    if _CAN_BURN is None:
        import subprocess
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                 capture_output=True, text=True, timeout=20)
            _CAN_BURN = bool(re.search(r"\bsubtitles\b", out.stdout or ""))
        except Exception:                                    # noqa: BLE001
            _CAN_BURN = False
    return _CAN_BURN


def attach(video_path: str, srt_text: str, out_path: str, workdir: str,
           language: str = "und", crf: int = 18, preset: str = "medium",
           font_size: int = 22) -> str:
    """
    Put subtitles on a video, timed to the dialogue.

    Burned into the picture where ffmpeg can do it, so they are visible
    anywhere without being switched on. Otherwise muxed as a subtitle track,
    which needs no libass, copies the streams rather than re-encoding, and is
    a second's work instead of minutes.

    Either way the cues are the ones the dub uses, which is what keeps them on
    the dialogue rather than drifting.

    Returns "burned" or "muxed" so the caller can say which happened.
    """
    import subprocess

    srt_path = os.path.join(workdir, "subs.srt")
    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write(srt_text)

    if can_burn():
        # Commas separate filters, so they are escaped inside force_style.
        style = ("FontSize=%d\\,PrimaryColour=&H00FFFFFF\\,"
                 "OutlineColour=&H90000000\\,BorderStyle=3\\,"
                 "Outline=1\\,Shadow=0\\,MarginV=28" % font_size)
        cmd = ["ffmpeg", "-y", "-i", os.path.abspath(video_path),
               "-vf", f"subtitles=subs.srt:force_style={style}",
               "-c:a", "copy", "-c:v", "libx264",
               "-crf", str(crf), "-preset", preset,
               os.path.abspath(out_path)]
        mode = "burned"
    else:
        cmd = ["ffmpeg", "-y", "-i", os.path.abspath(video_path),
               "-i", "subs.srt", "-c", "copy", "-c:s", "mov_text",
               "-metadata:s:s:0", f"language={language}",
               os.path.abspath(out_path)]
        mode = "muxed"

    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = " ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise RuntimeError(f"Could not add the subtitles ({mode}): {tail}")
    return mode
