"""
Speech-to-Text v2 — faster-whisper (CTranslate2 backend)
Drop-in replacement for speech_to_text.py with:
  - 6x faster inference via CTranslate2
  - 50% less memory usage
  - Word-level timestamps for forced alignment
  - Automatic fallback to smaller models if large-v3 fails
"""

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
            print("[STT-v2] Falling back to next model...")


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
# A segment this short is a fragment of the sentence around it, not a
# sentence. Translated alone it loses its context: "अपने संकल्प है" (has its
# resolves) came back from NLLB as "జువాన్ తన నిర్ణయం", a name and a noun that
# were never said. Folded into the segment before it, the same words
# translate correctly. The gap has to be short, so a real pause still splits.
FRAGMENT_MAX_WORDS = 3
FRAGMENT_MAX_GAP = 1.5
# A single segment can run the length of the clip - Whisper gave one Hindi
# speech seventeen seconds in one piece. One subtitle cue that long is
# unreadable, and one phrase that long is more than a voice model should be
# asked to say in a breath. Anything over this is split where the speaker
# actually paused, using the word timestamps; a pause shorter than the gap
# below is not a pause.
MAX_SEGMENT_SECONDS = 8.0
SPLIT_MIN_GAP = 0.3
SPLIT_MIN_PIECE = 1.0

# Shared by the first pass and any recovery pass, so the two cannot drift.
_DECODE = dict(
    beam_size=5,
    best_of=5,
    # A ladder, not a scalar. With temperature=0.0 alone the two thresholds
    # below were dead: a Hindi segment that degenerated into "भाजपा के लिए"
    # six times scored a compression ratio of 2.93, the check fired, and with
    # nothing to fall back to the loop was kept and synthesized into the dub.
    temperature=[0.0, 0.2, 0.4],
    # Stops the loop at the source. 1.05 is enough to break "भाजपा के लिए"
    # x6; 1.2 also broke it but re-segmented every clip more finely, which
    # gave the dubber shorter phrases and cost 0.07 of voice match on a clean
    # English clip. Segmentation is handled on its own below, at real
    # pauses, so this stays as gentle as still works.
    repetition_penalty=1.1,
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

def split_long_segments(segments: list) -> list:
    """
    Break a long segment at the speaker's own pauses.

    Recursive on the longest gap: a piece over the limit is cut at its widest
    word gap, and the halves are checked again. A cut that would leave a sliver
    on either side is skipped for the next-widest gap.
    """
    out = []
    for sg in segments:
        out.extend(_split_one(sg))
    return out


def _split_one(sg: dict) -> list:
    words = sg.get("words") or []
    length = float(sg.get("end", 0)) - float(sg.get("start", 0))
    if length <= MAX_SEGMENT_SECONDS or len(words) < 4:
        return [sg]
    # Candidate cuts, widest gap first.
    gaps = sorted(
        ((words[i + 1]["start"] - words[i]["end"], i)
         for i in range(len(words) - 1)),
        reverse=True)
    for gap, i in gaps:
        if gap < SPLIT_MIN_GAP:
            break
        left_w, right_w = words[:i + 1], words[i + 1:]
        left = {"start": sg["start"], "end": left_w[-1]["end"],
                "text": " ".join(w["word"] for w in left_w).strip(), "words": left_w}
        right = {"start": right_w[0]["start"], "end": sg["end"],
                 "text": " ".join(w["word"] for w in right_w).strip(), "words": right_w}
        if (left["end"] - left["start"] < SPLIT_MIN_PIECE
                or right["end"] - right["start"] < SPLIT_MIN_PIECE):
            continue
        return _split_one(left) + _split_one(right)
    return [sg]


def merge_fragments(segments: list) -> list:
    """
    Fold sentence fragments into the segment they continue.

    Whisper sometimes breaks a sentence at a breath, leaving a two- or
    three-word segment that is grammatically part of what came before. Every
    stage downstream is worse for it: NLLB mistranslates it, the dubber gives
    it a slot of its own, and the subtitle flashes three words for two
    seconds. Merging keeps the words, the timings and the word-level detail.
    """
    if not segments:
        return segments
    out = [dict(segments[0])]
    for sg in segments[1:]:
        prev = out[-1]
        words = (sg.get("text") or "").split()
        gap = float(sg.get("start", 0)) - float(prev.get("end", 0))
        if 0 < len(words) <= FRAGMENT_MAX_WORDS and 0 <= gap <= FRAGMENT_MAX_GAP:
            prev["text"] = f"{prev.get('text', '').rstrip()} {sg['text'].strip()}".strip()
            prev["end"] = sg.get("end", prev.get("end"))
            if prev.get("words") is not None or sg.get("words"):
                prev["words"] = list(prev.get("words") or []) + list(sg.get("words") or [])
            continue
        out.append(dict(sg))
    return out


def collapse_loops(text: str, min_repeats: int = 3, max_len: int = 6) -> str:
    """
    Cut a phrase Whisper got stuck on back to one occurrence.

    A repetition loop is a run of the same short n-gram three or more times
    in a row - "भाजपा के लिए भाजपा के लिए भाजपा के लिए ...". The decode
    settings now stop it at the source; this is the net under them, because
    the alternative is speaking the loop aloud in the dub.
    """
    words = text.split()
    if len(words) < min_repeats * 2:
        return text
    for n in range(1, max_len + 1):
        i = 0
        out = []
        while i < len(words):
            gram = words[i:i + n]
            if len(gram) < n:
                out.extend(words[i:])
                break
            reps = 1
            while words[i + reps * n:i + (reps + 1) * n] == gram:
                reps += 1
            if reps >= min_repeats:
                out.extend(gram)
                i += reps * n
            else:
                out.append(words[i])
                i += 1
        words = out
    return " ".join(words)


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

        text = segment.text.strip()
        cleaned = collapse_loops(text)
        if cleaned != text:
            print(f"[STT-v2] Collapsed a repetition loop at {segment.start:.1f}s: "
                  f"{len(text.split())} -> {len(cleaned.split())} words")
        seg_data = {
            "start": segment.start,
            "end": segment.end,
            "text": cleaned,
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
    before = len(segments_list)
    segments_list = split_long_segments(segments_list)
    if len(segments_list) != before:
        print(f"[STT-v2] Split {len(segments_list) - before} long segment(s) at "
              f"pauses")
    before = len(segments_list)
    segments_list = merge_fragments(segments_list)
    if len(segments_list) != before:
        print(f"[STT-v2] Folded {before - len(segments_list)} fragment(s) into "
              f"the sentence before them")
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
