"""
Voice-over from a typed script.

The dubbing path is: transcribe, translate, then speak the translation in the
speaker's voice against the video's timeline. A voice-over drops the middle two
constraints. The user supplies the words, so there is nothing to transcribe or
translate, and there is no video to fit, so nothing has to be time-stretched.

That last part matters: length fitting is where dubbing spends its risk budget
(stretch clamps, per-segment duration modelling). A voice-over has no target
length, so every phrase is spoken at its natural pace.

Reference selection is shared with dubbing, so the voice comes from the same
"cleanest continuous window of speech" logic.
"""

from __future__ import annotations

import re

import numpy as np

import tts_engines
import languages as L
from dubbing import _trim_silence, _edge_fade, SAMPLE_RATE

# IndicF5 degrades on long inputs, and dubbing never hands it more than a
# phrase. Scripts are split on sentence boundaries to stay in that range.
MAX_CHARS = 220
GAP_SENTENCE = 0.28      # seconds of silence between chunks
GAP_PARAGRAPH = 0.65     # seconds at a blank line

_SENTENCE_END = re.compile(r"(?<=[.!?।॥])\s+")


def split_script(script: str) -> list:
    """
    Break a script into synthesis chunks, remembering where the paragraph
    breaks were so the pauses can be longer there.

    Returns a list of (text, gap_after_seconds).
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", script or "") if p.strip()]
    chunks: list = []

    for pi, para in enumerate(paragraphs):
        # Sentence-split first, then pack sentences up to the length budget so
        # short sentences are not synthesised one word at a time.
        sentences = [s.strip() for s in _SENTENCE_END.split(para) if s.strip()]
        packed: list = []
        for sentence in sentences:
            while len(sentence) > MAX_CHARS:
                # A single sentence longer than the budget: break at the last
                # space inside it rather than mid-word.
                cut = sentence.rfind(" ", 0, MAX_CHARS)
                if cut <= 0:
                    cut = MAX_CHARS
                packed.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if packed and len(packed[-1]) + len(sentence) + 1 <= MAX_CHARS:
                packed[-1] = f"{packed[-1]} {sentence}"
            else:
                packed.append(sentence)

        for si, text in enumerate(packed):
            last_in_para = si == len(packed) - 1
            last_overall = last_in_para and pi == len(paragraphs) - 1
            gap = 0.0 if last_overall else (GAP_PARAGRAPH if last_in_para else GAP_SENTENCE)
            chunks.append((text, gap))

    return chunks


def render(reference_path: str, reference_text: str, script: str,
           language: str, out_path: str, progress=None) -> dict:
    """
    Speak `script` in the reference voice. `progress(done, total)` is called
    before each chunk so the caller can report position and honour a cancel.
    """
    chunks = split_script(script)
    if not chunks:
        raise ValueError("The script is empty.")

    synth_lang = L.synth_lang(language)
    pieces: list = []

    for i, (text, gap) in enumerate(chunks):
        if progress:
            progress(i, len(chunks))
        wave, sr = tts_engines.synthesize(
            text, reference_path, reference_text, language)
        if sr != SAMPLE_RATE:
            raise RuntimeError(
                f"Engine returned {sr} Hz, expected {SAMPLE_RATE} Hz")
        wave = np.asarray(wave, dtype=np.float32).reshape(-1)

        # The engine leaves a little room at both ends of every utterance.
        # Left in, it accumulates into a script that drags.
        wave = _trim_silence(wave)
        if wave.size == 0:
            continue
        wave = _edge_fade(wave, 8.0)
        pieces.append(wave)
        if gap > 0:
            pieces.append(np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32))

    if not pieces:
        raise RuntimeError("Synthesis produced no audio for this script.")

    track = np.concatenate(pieces)
    peak = float(np.max(np.abs(track))) if track.size else 0.0
    if peak < 1e-4:
        raise RuntimeError(
            "Synthesis produced silence. The reference clip is probably unusable.")
    # Normalise to a consistent level; chunk-to-chunk gain from the model
    # varies enough to be audible across a long script.
    track = (track / peak) * 0.89

    import soundfile as sf
    sf.write(out_path, track, SAMPLE_RATE, subtype="PCM_16")

    return {
        "path": out_path,
        "duration": round(len(track) / SAMPLE_RATE, 2),
        "chunks": len(chunks),
        "language": language,
        "synth_lang": synth_lang,
    }
