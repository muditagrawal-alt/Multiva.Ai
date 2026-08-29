"""
Speech-to-Text v2 — faster-whisper (CTranslate2 backend)
Drop-in replacement for speech_to_text.py with:
  - 6x faster inference via CTranslate2
  - 50% less memory usage
  - Word-level timestamps for forced alignment
  - Automatic fallback to smaller models if large-v3 fails
"""

import os
import torch

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------
# Priority: WHISPER_MODEL env var > "large-v3" > "medium" > "base"
_DEFAULT_MODEL = "large-v3"
MODEL_SIZE = os.getenv("WHISPER_MODEL", _DEFAULT_MODEL)


def _get_compute_type():
    """Select optimal compute type for the available hardware."""
    if torch.cuda.is_available():
        return "float16"
    # MPS (Apple Silicon) — CTranslate2 doesn't support MPS natively,
    # but runs well on CPU with int8 quantization
    return "int8"


def _get_device():
    """Select device for faster-whisper."""
    if torch.cuda.is_available():
        return "cuda"
    # faster-whisper uses CTranslate2 which runs on CPU (not MPS)
    return "cpu"


DEVICE = _get_device()
COMPUTE_TYPE = _get_compute_type()

# ---------------------------------------------------------------------------
# Lazy model loading (avoids import-time downloads)
# ---------------------------------------------------------------------------
_model = None


def _load_model():
    global _model
    if _model is not None:
        return _model

    from faster_whisper import WhisperModel

    model_size = MODEL_SIZE
    attempts = [model_size, "medium", "base"]

    for attempt in attempts:
        try:
            print(f"[STT-v2] Loading faster-whisper model: {attempt} "
                  f"(device={DEVICE}, compute={COMPUTE_TYPE})")
            _model = WhisperModel(
                attempt,
                device=DEVICE,
                compute_type=COMPUTE_TYPE,
            )
            print(f"[STT-v2] Model '{attempt}' loaded successfully")
            return _model
        except Exception as e:
            print(f"[STT-v2] Failed to load '{attempt}': {e}")
            if attempt == attempts[-1]:
                raise RuntimeError(
                    f"Could not load any Whisper model. Last error: {e}"
                ) from e
            print(f"[STT-v2] Falling back to next model...")


# ---------------------------------------------------------------------------
# Public API — same interface as speech_to_text.py
# ---------------------------------------------------------------------------

def transcribe_audio(audio_path: str, language: str = None) -> dict:
    """
    Transcribe audio using faster-whisper.
    Returns dict with 'text', 'language', and optionally 'segments' with
    word-level timestamps.
    """
    model = _load_model()

    segments_iter, info = model.transcribe(
        audio_path,
        # Pinning the language matters when re-transcribing a dub for
        # evaluation: Hindi and Urdu are the same spoken language, so Whisper
        # will happily return Nastaliq for Hindi audio. Character error rate
        # then reads 1.00 on a perfectly good dub purely from a script
        # mismatch. Leave as None for normal transcription (auto-detect).
        language=language,
        beam_size=5,
        best_of=5,
        temperature=0.0,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        word_timestamps=True,  # Key feature: word-level timestamps
        # Skip non-speech before it reaches the encoder. Faster on real footage
        # (music beds, pauses, room tone) and it stops Whisper inventing text in
        # silence, which used to produce phantom segments that the dubbing
        # timeline would then dutifully allocate time to.
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        # Each segment is dubbed independently, so carrying context between them
        # buys nothing and risks a repetition loop poisoning the rest of the run.
        condition_on_previous_text=False,
    )

    # Collect segments and full text
    segments_list = []
    all_text_parts = []

    for segment in segments_iter:
        all_text_parts.append(segment.text)

        seg_data = {
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
        }

        # Include word-level timestamps if available
        if segment.words:
            seg_data["words"] = [
                {
                    "word": w.word.strip(),
                    "start": w.start,
                    "end": w.end,
                    "probability": round(w.probability, 3),
                }
                for w in segment.words
            ]

        segments_list.append(seg_data)

    full_text = " ".join(all_text_parts).strip()

    print(f"[STT-v2] Transcribed {len(segments_list)} segments, "
          f"language={info.language}, "
          f"duration={info.duration:.1f}s")

    return {
        "text": full_text,
        "language": info.language,
        "segments": segments_list,
        "duration": info.duration,
    }
