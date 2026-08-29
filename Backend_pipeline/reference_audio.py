"""
Reference-audio selection for voice cloning.

The old pipeline took a blind `-t 15` from t=0, which in real footage is usually
intro music, a title card, silence, or a second speaker. Cloning quality then
varied purely with how the video happened to open — the single biggest source of
run-to-run inconsistency.

This module instead uses the word-level timestamps Whisper already produces to
find the cleanest contiguous run of confident speech, and extracts it at 24 kHz
(not 16 kHz — everything above 8 kHz is sibilance and breathiness, which is most
of what makes a voice identifiable to a cloning model).

It also returns the TRANSCRIPT of the chosen window, which IndicF5 requires as
its `ref_text` conditioning input.
"""

import os
import subprocess

# A prompt shorter than this is too little for a stable speaker embedding;
# longer than ~15s and F5 starts clipping it internally anyway.
MIN_REF_SECONDS = 6.0
MAX_REF_SECONDS = 12.0

# Words separated by more than this are treated as a pause that breaks a window.
MAX_INTERNAL_GAP = 0.6

# Openings are disproportionately likely to be music/titles/intros.
INTRO_PENALTY_BEFORE = 2.0


def _flatten_words(segments: list) -> list:
    """Collect word-level timestamps across all segments."""
    words = []
    for seg in segments or []:
        for w in seg.get("words") or []:
            if w.get("start") is None or w.get("end") is None:
                continue
            text = (w.get("word") or "").strip()
            if not text:
                continue
            words.append({
                "word": text,
                "start": float(w["start"]),
                "end": float(w["end"]),
                "p": float(w.get("probability", 1.0)),
            })
    words.sort(key=lambda w: w["start"])
    return words


def _score_window(window: list) -> float:
    """
    Higher is better. Rewards confident, continuous, well-filled speech.
    """
    if not window:
        return -1e9

    dur = window[-1]["end"] - window[0]["start"]
    if dur <= 0:
        return -1e9

    mean_conf = sum(w["p"] for w in window) / len(window)

    # Fraction of the window actually occupied by speech (vs. internal pauses).
    voiced = sum(w["end"] - w["start"] for w in window)
    density = min(1.0, voiced / dur)

    # Largest internal gap — a big one means we straddled a pause or a cut.
    max_gap = 0.0
    for a, b in zip(window, window[1:]):
        max_gap = max(max_gap, b["start"] - a["end"])

    score = (mean_conf * 2.0) + (density * 1.5) - (max_gap * 1.0)

    if window[0]["start"] < INTRO_PENALTY_BEFORE:
        score -= 0.5

    # Mild preference for longer prompts, saturating at MAX_REF_SECONDS.
    score += 0.3 * min(dur, MAX_REF_SECONDS) / MAX_REF_SECONDS

    return score


def find_best_window(segments: list, total_duration: float) -> tuple:
    """
    Return (start, end, text) for the best reference window, or None if the
    transcript has no usable word timings.
    """
    words = _flatten_words(segments)
    if not words:
        return None

    # Footage with long pauses (speeches, dramatic delivery) can leave every
    # gap-bounded window under MIN_REF_SECONDS. A short reference is not a
    # cosmetic problem: it throws off the seconds-per-byte calibration the
    # generator uses, and a 3.8s reference produced the worst output in the
    # whole corpus. So retry with a progressively more permissive gap before
    # settling for a short window.
    for max_gap in (MAX_INTERNAL_GAP, MAX_INTERNAL_GAP * 2, MAX_INTERNAL_GAP * 4):
        best = _best_window_for_gap(words, max_gap)
        if best and (best[-1]["end"] - best[0]["start"]) >= MIN_REF_SECONDS:
            break

    if best is None:
        best = _longest_run(words)

    if not best:
        return None

    start = max(0.0, best[0]["start"] - 0.10)
    end = min(total_duration, best[-1]["end"] + 0.10)
    text = " ".join(w["word"] for w in best).strip()
    return start, end, text


def candidate_windows(segments: list, total_duration: float, limit: int = 5) -> list:
    """
    The best few reference windows, not just the winner.

    Reference selection drives most of the cloning quality and has always been
    automatic and invisible. The scoring already ranks every candidate; this
    returns the top handful so a person can listen and disagree.

    Windows that overlap an already-chosen one by more than half are dropped,
    since a one-word shift is not a real alternative.
    """
    words = _flatten_words(segments)
    if not words:
        return []

    scored = []
    for max_gap in (MAX_INTERNAL_GAP, MAX_INTERNAL_GAP * 2, MAX_INTERNAL_GAP * 4):
        for i in range(len(words)):
            window = []
            for j in range(i, len(words)):
                if window and words[j]["start"] - window[-1]["end"] > max_gap:
                    break
                window.append(words[j])
                dur = window[-1]["end"] - window[0]["start"]
                if dur < MIN_REF_SECONDS:
                    continue
                if dur > MAX_REF_SECONDS:
                    break
                scored.append((_score_window(window), list(window)))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    chosen = []
    for score, window in scored:
        start = max(0.0, window[0]["start"] - 0.10)
        end = min(total_duration, window[-1]["end"] + 0.10)
        if end - start <= 0:
            continue
        overlapping = any(
            min(end, c["start"] + c["duration"]) - max(start, c["start"])
            > 0.5 * min(end - start, c["duration"])
            for c in chosen
        )
        if overlapping:
            continue
        chosen.append({
            "start": round(start, 2),
            "duration": round(end - start, 2),
            "score": round(float(score), 3),
            "text": " ".join(w["word"] for w in window).strip(),
        })
        if len(chosen) >= limit:
            break

    return chosen


def _best_window_for_gap(words: list, max_gap: float):
    """Highest-scoring window whose internal gaps never exceed `max_gap`."""
    best, best_score = None, -1e9

    for i in range(len(words)):
        window = []
        for j in range(i, len(words)):
            if window and words[j]["start"] - window[-1]["end"] > max_gap:
                break
            window.append(words[j])
            dur = window[-1]["end"] - window[0]["start"]
            if dur < MIN_REF_SECONDS:
                continue
            if dur > MAX_REF_SECONDS:
                break
            s = _score_window(window)
            if s > best_score:
                best_score = s
                best = list(window)

    return best


def _longest_run(words: list):
    """Longest gap-free run, however short — last resort."""
    run, longest = [], []
    for w in words:
        if run and w["start"] - run[-1]["end"] > MAX_INTERNAL_GAP * 4:
            if (run[-1]["end"] - run[0]["start"]) > (
                longest[-1]["end"] - longest[0]["start"] if longest else 0
            ):
                longest = run
            run = []
        run.append(w)
    if run and (not longest or (run[-1]["end"] - run[0]["start"]) >
                (longest[-1]["end"] - longest[0]["start"])):
        longest = run
    return longest


def extract_reference(video_path: str, start: float, duration: float,
                      out_path: str, sample_rate: int = 24000,
                      denoise: bool = False) -> str:
    """
    Cut the reference window and condition it for speaker-embedding quality:
    trim edge silence, remove low-frequency rumble, normalize loudness.

    Deliberately does NOT apply aggressive denoise by default — spectral
    denoisers smear exactly the timbre detail a cloning model keys on.
    """
    # ONLY timing-preserving filters here. `silenceremove` used to be in this
    # chain and it was catastrophic: it strips INTERNAL pauses, not just the
    # edges, and cut a 7.08s window down to 2.58s — 64% of the audio gone.
    #
    # That breaks F5 in two ways at once. The prompt drops below the ~5s it
    # needs for a stable speaker identity, and — far worse — the audio no longer
    # corresponds to `ref_text`, which still transcribes the whole window. The
    # model is handed 2.6s of audio and told it says 7 seconds' worth of words,
    # so its duration model is wrecked and the output comes out rushed and
    # robotic.
    #
    # Anything added here must leave the timeline alone, or `ref_text` has to be
    # re-derived to match.
    chain = ["highpass=f=60", "loudnorm=I=-18:TP=-2:LRA=11"]
    if denoise:
        chain.insert(1, "afftdn=nr=8:nf=-30")

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", video_path,
        "-vn",
        "-af", ",".join(chain),
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def build_reference(video_path: str, segments: list, total_duration: float,
                    out_path: str, sample_rate: int = 24000) -> dict:
    """
    Pick and extract the best voice-cloning reference from a video.

    Returns {"path", "text", "start", "end", "duration"}.
    `text` is the transcript of the window, required by IndicF5 as `ref_text`.
    """
    picked = find_best_window(segments, total_duration)

    if picked is None:
        # No usable word timings at all — fall back to a mid-video window,
        # which still beats t=0 for avoiding intros.
        start = min(max(0.0, total_duration * 0.25), max(0.0, total_duration - MIN_REF_SECONDS))
        end = min(total_duration, start + MAX_REF_SECONDS)
        text = ""
        print("[REF] No word timings available; falling back to a mid-video window")
    else:
        start, end, text = picked

    requested = max(0.5, end - start)
    extract_reference(video_path, start, requested, out_path, sample_rate)

    # Report the duration of the file we ACTUALLY wrote, never the duration we
    # asked for. Callers use this to compute F5's `fix_duration`, and F5 derives
    # the reference length from the real waveform — so if the two disagree, every
    # generated segment comes out the wrong length and then gets time-stretched
    # to compensate, which sounds robotic. Encoder padding alone can shift this
    # by tens of milliseconds even with no filtering.
    actual = requested
    try:
        import av_sync
        actual = av_sync.duration(out_path)
    except Exception as e:
        print(f"[REF] Could not probe reference duration ({e}); "
              f"falling back to the requested {requested:.2f}s")

    if abs(actual - requested) > 0.05:
        print(f"[REF] NOTE: extracted {actual:.2f}s from a {requested:.2f}s "
              f"window — using the measured value")

    print(f"[REF] Reference window {start:.2f}s–{end:.2f}s ({actual:.2f}s) "
          f"@{sample_rate}Hz -> {os.path.basename(out_path)}")
    if actual < MIN_REF_SECONDS - 0.5:
        print(f"[REF] WARNING: only {actual:.2f}s of reference audio; "
              f"voice cloning is unreliable below ~{MIN_REF_SECONDS:.0f}s")
    if text:
        print(f"[REF] Reference text: {text[:90]}{'...' if len(text) > 90 else ''}")

    return {"path": out_path, "text": text, "start": start,
            "end": end, "duration": actual}
