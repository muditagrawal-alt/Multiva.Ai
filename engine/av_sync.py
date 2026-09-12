"""
Audio/video probing, muxing and verification.

Replaces advanced_video_processor.py, which had four defects that between them
produced exactly the gaps this pipeline was suffering from:

  * `smooth_audio_video_alignment` applied atempo with the ratio inverted
    (video/audio instead of audio/video), which stretched the dub the wrong way
    and roughly doubled the mismatch it was meant to remove;
  * its output was never consumed downstream, so the whole step was a no-op;
  * the chunked path cut chunks at full length but advanced by
    `chunk_duration - overlap`, concatenating duplicated video at every seam;
  * `verify_no_gaps` queried `frame=pkt_pts_time`, a field ffprobe renamed to
    `pts_time` in ffmpeg 5, so every frame parsed as empty and the function
    returned True unconditionally — and it checked video frame spacing, never
    the audio-vs-video duration mismatch that was the actual bug.

There is no tempo correction here at all any more. `dubbing` builds a track that
is already exactly the length of the video, so muxing is a straight copy and the
verification below is a genuine assertion rather than a formality.
"""

import json
import os
import subprocess


def _ffprobe(args: list) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json"] + args,
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout or "{}")


def probe(path: str) -> dict:
    """
    Return {'duration', 'video': {...} or None, 'audio': {...} or None}.

    Durations come from the container first and the stream second: a stream
    'duration' field is missing often enough (raw/copied streams) that relying
    on it alone is what made the old code fall back to nonsense.
    """
    data = _ffprobe(["-show_streams", "-show_format", path])
    info = {"duration": None, "video": None, "audio": None, "path": path}

    fmt_dur = data.get("format", {}).get("duration")
    if fmt_dur is not None:
        try:
            info["duration"] = float(fmt_dur)
        except (TypeError, ValueError):
            pass

    for s in data.get("streams", []):
        kind = s.get("codec_type")
        if kind not in ("video", "audio") or info.get(kind) is not None:
            continue

        entry = {"codec": s.get("codec_name")}
        try:
            entry["duration"] = float(s["duration"])
        except (KeyError, TypeError, ValueError):
            entry["duration"] = info["duration"]

        if kind == "video":
            rate = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/0"
            try:
                num, den = rate.split("/")
                entry["fps"] = (float(num) / float(den)) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                entry["fps"] = 0.0
            entry["width"] = int(s.get("width") or 0)
            entry["height"] = int(s.get("height") or 0)
            try:
                entry["nb_frames"] = int(s.get("nb_frames"))
            except (TypeError, ValueError):
                entry["nb_frames"] = None

        info[kind] = entry

    if info["duration"] is None:
        for kind in ("video", "audio"):
            if info[kind] and info[kind].get("duration"):
                info["duration"] = info[kind]["duration"]
                break

    if info["duration"] is None:
        raise ValueError(f"Could not determine duration of {path}")

    return info


def duration(path: str) -> float:
    return probe(path)["duration"]


def video_info(path: str) -> dict:
    info = probe(path)
    if not info["video"]:
        raise ValueError(f"No video stream in {path}")
    v = dict(info["video"])
    v["duration"] = info["duration"]
    if not v.get("fps"):
        v["fps"] = 25.0
    return v


def mux(video_path: str, audio_path: str, out_path: str,
        crf: int = 20, preset: str = "veryfast", reencode_video: bool = False) -> str:
    """
    Attach `audio_path` to the picture of `video_path`.

    Deliberately no `-shortest`: the dub is built to the video's exact length,
    so `-shortest` could only ever mask a bug by silently truncating. If the two
    disagree we want `verify_sync` to say so.
    """
    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
           "-map", "0:v:0", "-map", "1:a:0"]

    if reencode_video:
        cmd += ["-c:v", "libx264", "-crf", str(crf), "-preset", preset,
                "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]

    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def encode(input_path: str, out_path: str, crf: int = 20,
           preset: str = "veryfast", scale_height: int = None) -> str:
    """Single final encode. Audio is copied, never re-encoded again."""
    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
           "-pix_fmt", "yuv420p"]
    if scale_height:
        cmd += ["-vf", f"scale=-2:{scale_height}"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def extract_audio(video_path: str, out_path: str, sample_rate: int = 16000,
                  channels: int = 1) -> str:
    """16 kHz mono is what both Whisper and Wav2Lip expect."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn",
         "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", str(channels),
         out_path],
        check=True, capture_output=True,
    )
    return out_path


def trim(input_path: str, out_path: str, start: float, end: float) -> str:
    """
    Cut [start, end) out of a clip and re-encode it as a standalone file.

    Re-encoding rather than stream-copying is deliberate: a copy can only cut
    on keyframes, so the picture would start before the requested point while
    the audio started exactly on it. Everything downstream assumes the two
    agree from sample zero.
    """
    if end <= start:
        raise ValueError(f"Out point ({end:.2f}s) must be after in point ({start:.2f}s)")

    cmd = [
        "ffmpeg", "-y", "-nostats", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", input_path,
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-tune", "film",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def mix_music(voice_path: str, music_path: str, out_path: str,
              gain_db: float = -18.0, fade: float = 1.5) -> str:
    """
    Lay a music bed under a voice track.

    The voice is never touched: the bed is attenuated, looped or cut to the
    voice's length, faded at the tail, and summed. `amix` would duck both
    inputs by its own normalisation, so the sum is explicit instead.
    """
    voice_len = duration(voice_path)
    if voice_len <= 0:
        raise ValueError("The voice track has no duration to match")

    fade = max(0.0, min(fade, voice_len / 2))
    fade_from = max(0.0, voice_len - fade)

    # -stream_loop repeats a short bed; atrim cuts a long one. Both end up at
    # exactly the voice's length.
    filters = (
        f"[1:a]volume={gain_db}dB,"
        f"atrim=0:{voice_len:.3f},"
        f"afade=t=out:st={fade_from:.3f}:d={fade:.3f},"
        f"aresample=24000[bed];"
        f"[0:a][bed]amix=inputs=2:duration=first:normalize=0[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-nostats", "-loglevel", "error",
        "-i", voice_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex", filters,
        "-map", "[out]",
        "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def verify_sync(path: str, tolerance: float = 0.15) -> dict:
    """
    The check the old `verify_no_gaps` was supposed to be: does the audio
    actually run as long as the picture?

    Returns {'ok', 'video', 'audio', 'delta', 'reason'}.
    """
    info = probe(path)
    v = info["video"]["duration"] if info["video"] else None
    a = info["audio"]["duration"] if info["audio"] else None

    result = {"ok": False, "video": v, "audio": a, "delta": None, "reason": ""}

    if v is None:
        result["reason"] = "no video stream"
        return result
    if a is None:
        result["reason"] = "no audio stream"
        return result

    delta = abs(v - a)
    result["delta"] = delta

    if delta > tolerance:
        longer = "audio" if a > v else "video"
        result["reason"] = (f"{longer} runs {delta:.3f}s longer "
                            f"(video {v:.3f}s, audio {a:.3f}s)")
        return result

    result["ok"] = True
    result["reason"] = f"in sync (delta {delta * 1000:.0f}ms)"
    return result


def enforce_duration(audio_path: str, target: float, out_path: str) -> str:
    """
    Pad or trim an audio file to exactly `target` seconds.
    A last-resort guard; `dubbing` should already have produced this length.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path,
         "-af", f"apad=whole_dur={target:.4f}",
         "-t", f"{target:.4f}",
         "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1", out_path],
        check=True, capture_output=True,
    )
    return out_path
