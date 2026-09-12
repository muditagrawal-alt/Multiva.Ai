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
import engines

# engines.get() already lets WHISPER_MODEL win, so an explicitly pinned
# environment still overrides whatever the setup screen stored.
MODEL_SIZE = engines.get("stt") or _DEFAULT_MODEL


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


def refresh() -> str:
    """
    Re-read the stage choice and drop the loaded model.

    Called when the setup screen or settings panel changes which model runs
    this stage. The next transcription loads the new one; a render already in
    flight holds its own reference and finishes on the old one, which is what
    you want mid-job.
    """
    global MODEL_SIZE, _model
    MODEL_SIZE = engines.get("stt") or _DEFAULT_MODEL
    _model = None
    return MODEL_SIZE


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


# A gap smaller than this at the end of a file is trailing silence, not
# missing speech. Larger, and something was dropped.
TAIL_GAP_SECONDS = 2.0
MAX_TAIL_PASSES = 3
# Only stop early when a pass recovers nothing at all. A larger threshold
# looks sensible and is not: on one clip the first recovery pass gained 0.14s
# and the two after it gained 2.6s between them, so anything that stops on a
# small gain throws away most of what there was to get.
MIN_TAIL_GAIN = 0.05
# Going back for the tail is only right when there is speech in it. On a clip
# whose speech genuinely ends early, a forced second pass over the silence
# hallucinated "Sure.", "Thank you." and a Norwegian subtitle credit, each
# of which was then synthesized into the dub. The tail has to hold at least
# this much VAD-detected speech, and this fraction of its length, first.
MIN_TAIL_SPEECH_SECONDS = 1.0
MIN_TAIL_SPEECH_FRACTION = 0.3
# A recovered segment shorter than this is not a phrase anyone said.
MIN_RECOVERED_SEGMENT = 0.3

# Shared by the first pass and any recovery pass, so the two cannot drift.
_DECODE = dict(
    beam_size=5,
    best_of=5,
    temperature=0.0,
    compression_ratio_threshold=2.4,
    log_prob_threshold=-1.0,
    no_speech_threshold=0.6,
    word_timestamps=True,
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


# ---------------------------------------------------------------------------
# Public API — same interface as speech_to_text.py
# ---------------------------------------------------------------------------

def _speech_in(audio_path: str, from_seconds: float) -> float:
    """Seconds of VAD-detected speech from `from_seconds` to the end."""
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        audio = decode_audio(audio_path, sampling_rate=16000)
        tail = audio[int(from_seconds * 16000):]
        if len(tail) == 0:
            return 0.0
        spans = get_speech_timestamps(tail, VadOptions(min_silence_duration_ms=500))
        return sum(t["end"] - t["start"] for t in spans) / 16000.0
    except Exception as e:                                   # noqa: BLE001
        print(f"[STT-v2] Could not measure speech in the tail: {e}")
        return 0.0


def transcribe_audio(audio_path: str, language: str = None) -> dict:
    """
    Transcribe audio using faster-whisper.
    Returns dict with 'text', 'language', and optionally 'segments' with
    word-level timestamps.
    """
    model = _load_model()

    def _pass(clip=None):
        """One decode over the file, or over a range of it."""
        return model.transcribe(
            audio_path,
            **(dict(_DECODE, language=language, clip_timestamps=clip)
               if clip else dict(_DECODE, language=language)))

    segments_iter, info = model.transcribe(
        audio_path,
        # Pinning the language matters when re-transcribing a dub for
        # evaluation: Hindi and Urdu are the same spoken language, so Whisper
        # will happily return Nastaliq for Hindi audio. Character error rate
        # then reads 1.00 on a perfectly good dub purely from a script
        # mismatch. Leave as None for normal transcription (auto-detect).
        language=language,
        **_DECODE,
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

    # large-v3 stops early on some audio: on a 20.3s clip it gave up at 15.5s
    # and the last sentence was simply gone - from the subtitles, and from the
    # dub. No decoding setting moved it (VAD off, no_speech up, temperature
    # fallback, condition_on_previous_text, hallucination threshold all made
    # no difference), but asking it to start again where it stopped returns
    # the missing speech with correct absolute timestamps.
    for _ in range(MAX_TAIL_PASSES):
        covered = max((sg["end"] for sg in segments_list), default=0.0)
        gap = (info.duration or 0.0) - covered
        if gap <= TAIL_GAP_SECONDS:
            break
        speech = _speech_in(audio_path, covered)
        if speech < MIN_TAIL_SPEECH_SECONDS or speech < gap * MIN_TAIL_SPEECH_FRACTION:
            print(f"[STT-v2] {gap:.1f}s after {covered:.1f}s holds only "
                  f"{speech:.1f}s of speech; leaving it")
            break
        print(f"[STT-v2] {gap:.1f}s after {covered:.1f}s was never transcribed "
              f"({speech:.1f}s of speech in it); going back for it")
        try:
            more_iter, _ = _pass(clip=[covered, info.duration])
            found = 0
            for segment in more_iter:
                if segment.end <= covered + 0.05 or not segment.text.strip():
                    continue
                if segment.end - segment.start < MIN_RECOVERED_SEGMENT:
                    continue
                all_text_parts.append(segment.text)
                seg_data = {"start": segment.start, "end": segment.end,
                            "text": segment.text.strip()}
                if segment.words:
                    seg_data["words"] = [
                        {"word": w.word.strip(), "start": w.start, "end": w.end,
                         "probability": round(w.probability, 3)}
                        for w in segment.words
                    ]
                segments_list.append(seg_data)
                found += 1
            if not found:
                break          # genuinely silence; stop asking
            gained = max((sg["end"] for sg in segments_list), default=0.0) - covered
            if gained < MIN_TAIL_GAIN:
                break          # grinding forward a fraction at a time; stop
        except Exception as e:                               # noqa: BLE001
            print(f"[STT-v2] Second pass failed, keeping what we have: {e}")
            break

    segments_list.sort(key=lambda sg: sg["start"])
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
