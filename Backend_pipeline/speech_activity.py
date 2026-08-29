"""
Speech/silence detection over the source audio, and phrase splitting.

Why this module exists
----------------------
Whisper segments are not speech units. A single segment routinely spans several
phrases with real pauses inside it, and the dubbing timeline used to fill each
segment's whole slot with ONE continuous utterance. Every pause inside that
segment was therefore replaced by words.

That is audible and severe. Measured on a 20.27s clip: 11.9% of the audio is
silence — 2.41s spread over 16 separate pauses, every one of them between 50ms
and 150ms. Those short pauses are what makes speech parseable; removing them
crams 2.41s of extra syllables into the same running time, so the dub sounds
rushed and the words run into each other even when every segment is nominally
playing at 1.00x speed.

Whisper's own word timestamps cannot be used to find them. On the same clip its
word boundaries were contiguous — it reported 1.1% pause against the waveform's
11.9% — because word-level timing for Hindi at `medium` is interpolated rather
than measured. So the pauses are detected from the waveform directly.
"""

import numpy as np

# Frame step for the energy envelope.
HOP_SECONDS = 0.010

# Level below the clip's peak at which a frame counts as silence.
SILENCE_DB = -35.0

# Ignore anything shorter than this: it is a stop consonant or a glottal gap,
# not a pause, and splitting on it would shred words.
MIN_PAUSE = 0.08

# A phrase shorter than this is not worth synthesizing on its own; it gets
# merged into its neighbour.
MIN_PHRASE = 0.35


def detect_speech_runs(audio_path: str, silence_db: float = SILENCE_DB,
                       min_pause: float = MIN_PAUSE) -> list:
    """
    Return [(start, end), ...] of speech regions, in seconds.

    Silences shorter than `min_pause` are treated as part of the surrounding
    speech rather than as boundaries.
    """
    import librosa

    y, sr = librosa.load(audio_path, sr=16000)
    if y.size == 0:
        return []

    hop = max(1, int(sr * HOP_SECONDS))
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    if rms.max() <= 0:
        return []

    db = librosa.amplitude_to_db(rms, ref=np.max)
    voiced = db > silence_db

    # Bridge silences that are too short to count as pauses.
    bridge = int(round(min_pause / HOP_SECONDS))
    idx = 0
    while idx < len(voiced):
        if not voiced[idx]:
            start = idx
            while idx < len(voiced) and not voiced[idx]:
                idx += 1
            if start > 0 and idx < len(voiced) and (idx - start) < bridge:
                voiced[start:idx] = True
        else:
            idx += 1

    runs, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start * HOP_SECONDS, i * HOP_SECONDS))
            start = None
    if start is not None:
        runs.append((start * HOP_SECONDS, len(voiced) * HOP_SECONDS))

    total = len(y) / sr
    return [(max(0.0, a), min(total, b)) for a, b in runs if b > a]


def _split_text_by_weights(text: str, weights: list) -> list:
    """
    Split `text` into len(weights) parts at word boundaries, with each part's
    character count roughly proportional to its weight.

    Used to distribute a segment's translation across the phrases inside it.
    Approximate by nature — we do not know which translated word corresponds to
    which source phrase — but preserving the rhythm matters more than getting
    the split exactly on the right word.
    """
    words = (text or "").split()
    n = len(weights)
    if n <= 1 or len(words) <= 1:
        return [text.strip()] + [""] * (n - 1)

    total_w = float(sum(weights)) or 1.0
    total_c = sum(len(w) + 1 for w in words)

    parts, i = [], 0
    used = 0.0
    for k, w in enumerate(weights):
        if k == n - 1:
            parts.append(" ".join(words[i:]))
            break
        used += w
        want = total_c * (used / total_w)
        acc, j = 0, i
        while j < len(words) and acc < want:
            acc += len(words[j]) + 1
            j += 1
        # Always leave at least one word for each remaining part.
        j = max(i + 1, min(j, len(words) - (n - k - 1)))
        parts.append(" ".join(words[i:j]))
        i = j

    return [p.strip() for p in parts]


def build_phrase_units(segments: list, translated: list, speech_runs: list,
                       min_phrase: float = MIN_PHRASE) -> list:
    """
    Turn Whisper segments into finer phrase units aligned to real speech runs.

    Each unit is {"start", "end", "text", "target", "segment"}, where `text` is
    the SOURCE chunk and `target` the translated chunk — matching the convention
    plan_timeline expects (segment carries source text, translations arrive as a
    parallel list). Both are split the same way so the duration model still sees
    a meaningful target/source length ratio per unit.

    The gaps BETWEEN units are the speaker's actual pauses; the timeline leaves
    them as silence, which is what restores the natural rhythm.

    A segment containing no detected pause yields exactly one unit, so this
    degrades gracefully to the previous whole-segment behaviour.
    """
    units = []

    for seg_i, (seg, text) in enumerate(zip(segments, translated)):
        text = (text or "").strip()
        source_text = (seg.get("text") or "").strip()
        if not text:
            continue

        s0 = float(seg.get("start", 0.0))
        s1 = float(seg.get("end", s0))
        if s1 <= s0:
            continue

        # Speech runs overlapping this segment, clipped to it.
        inside = []
        for a, b in speech_runs:
            lo, hi = max(a, s0), min(b, s1)
            if hi - lo > 0.05:
                inside.append([lo, hi])

        if not inside:
            units.append({"start": s0, "end": s1, "text": source_text,
                          "target": text, "segment": seg_i})
            continue

        # Merge runs that are too short to stand alone as a phrase.
        merged = [inside[0]]
        for lo, hi in inside[1:]:
            if (hi - lo) < min_phrase or (merged[-1][1] - merged[-1][0]) < min_phrase:
                merged[-1][1] = hi
            else:
                merged.append([lo, hi])

        weights = [hi - lo for lo, hi in merged]
        tgt_chunks = _split_text_by_weights(text, weights)
        src_chunks = _split_text_by_weights(source_text, weights)

        for (lo, hi), tgt, src in zip(merged, tgt_chunks, src_chunks):
            if tgt.strip():
                units.append({"start": lo, "end": hi,
                              "text": src.strip() or tgt.strip(),
                              "target": tgt.strip(), "segment": seg_i})

    units.sort(key=lambda u: u["start"])
    return units


def summarize(units: list, segments: list, total_duration: float) -> str:
    speech = sum(u["end"] - u["start"] for u in units)
    return (f"[VAD] {len(segments)} segments -> {len(units)} phrase units; "
            f"{speech:.2f}s speech, {total_duration - speech:.2f}s pause "
            f"({100 * (total_duration - speech) / max(total_duration, 0.01):.1f}%)")
