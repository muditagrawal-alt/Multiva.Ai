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


def cues(segments: list, texts: list | None) -> list:
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
    for n, (start, end, body) in enumerate(cues(segments, texts), 1):
        lines.append(str(n))
        lines.append(f"{_clock(start, True)} --> {_clock(end, True)}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def vtt(segments: list, texts: list | None = None) -> str:
    lines = ["WEBVTT", ""]
    for start, end, body in cues(segments, texts):
        lines.append(f"{_clock(start, False)} --> {_clock(end, False)}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def plain(segments: list, texts: list | None = None) -> str:
    """One line per spoken segment, which reads better than one long blob."""
    return "\n".join(body for _, _, body in cues(segments, texts)) + "\n"


# Fonts that actually have the glyphs. Devanagari and Arabic need real
# shaping, not a fallback box per codepoint, so the search is ordered by what
# covers the most scripts.
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/Nirmala.ttc",
    "C:/Windows/Fonts/arialuni.ttf",
]

# Beyond this many cues the filtergraph gets unreasonable, and a muxed track
# is the better trade.
MAX_BURN_CUES = 80


def _font(size: int):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:                                # noqa: BLE001
                continue
    return None


def _wrap(draw, text: str, font, max_width: int) -> list:
    """Greedy wrap on spaces, measured in the font actually being drawn."""
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines[:3]


def _render_cue(text: str, video_w: int, font_size: int, path: str) -> bool:
    """
    Draw one caption to a transparent PNG.

    Pillow with raqm shapes Devanagari and Arabic properly - conjuncts,
    matras, joining forms - which is the part a naive per-glyph renderer gets
    wrong.
    """
    from PIL import Image, ImageDraw

    font = _font(font_size)
    if font is None:
        return False

    margin = max(24, video_w // 16)
    max_width = video_w - 2 * margin
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    lines = _wrap(probe, text, font, max_width)

    line_h = int(font_size * 1.45)
    img = Image.new("RGBA", (video_w, line_h * len(lines) + 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        y = 8 + line_h * i + line_h // 2
        # An outline, so white text stays readable over a white shirt.
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                if dx or dy:
                    d.text((video_w // 2 + dx, y + dy), line, font=font,
                           fill=(0, 0, 0, 220), anchor="mm")
        d.text((video_w // 2, y), line, font=font,
               fill=(255, 255, 255, 255), anchor="mm")
    img.save(path)
    return True


def _video_size(path: str):
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30).stdout.strip()
        w, h = (int(x) for x in out.split(",")[:2])
        return w, h
    except Exception:                                        # noqa: BLE001
        return 0, 0


def can_burn() -> bool:
    """
    Whether captions can be drawn onto the picture.

    Not ffmpeg's `subtitles` filter, which needs libass - Homebrew's ffmpeg
    does not have it, so on macOS that path is simply unavailable. This
    renders the text with Pillow and composites with `overlay`, which every
    build has.
    """
    try:
        from PIL import features
        return bool(features.check("freetype2")) and _font(28) is not None
    except Exception:                                        # noqa: BLE001
        return False


def attach(video_path: str, cues: list, out_path: str, workdir: str,
           srt_text: str = "", language: str = "und",
           crf: int = 18, preset: str = "medium", font_size: int = 0) -> str:
    """
    Put subtitles on a video, timed to the dialogue.

    `cues` is [(start, end, text)]. Drawn onto the picture when the text can be
    rendered, so they are visible anywhere without being switched on;
    otherwise muxed as a track, which needs nothing and costs a second.

    Returns "burned" or "muxed" so the caller can say which happened.
    """
    import subprocess

    srt_path = os.path.join(workdir, "subs.srt")
    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write(srt_text)

    width, height = _video_size(video_path)
    usable = [c for c in cues if (c[2] or "").strip()][:MAX_BURN_CUES]

    if can_burn() and width and usable:
        size = font_size or max(18, min(46, int(height * 0.055)))
        inputs, filters, label = [], [], "[0:v]"
        made = 0
        for i, (start, end, text) in enumerate(usable):
            png = os.path.join(workdir, f"cue{i}.png")
            if not _render_cue(text.strip(), width, size, png):
                continue
            inputs += ["-i", png]
            made += 1
            nxt = f"[v{made}]"
            filters.append(
                f"{label}[{made}:v]overlay=(W-w)/2:H-h-{max(16, height // 22)}"
                f":enable='between(t,{start:.3f},{end:.3f})'{nxt}")
            label = nxt

        if made:
            cmd = (["ffmpeg", "-y", "-i", os.path.abspath(video_path)] + inputs +
                   ["-filter_complex", ";".join(filters),
                    "-map", label, "-map", "0:a?",
                    "-c:a", "copy", "-c:v", "libx264",
                    "-crf", str(crf), "-preset", preset,
                    os.path.abspath(out_path)])
            proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
            if proc.returncode == 0:
                return "burned"
            tail = " ".join((proc.stderr or "").strip().splitlines()[-3:])
            print(f"[Subtitles] Could not draw them on ({tail}); muxing instead")

    cmd = ["ffmpeg", "-y", "-i", os.path.abspath(video_path),
           "-i", "subs.srt", "-c", "copy", "-c:s", "mov_text",
           "-metadata:s:s:0", f"language={language}",
           os.path.abspath(out_path)]
    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = " ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise RuntimeError(f"Could not add the subtitles: {tail}")
    return "muxed"
