#Checkpoint: 29/3/26
import os
import re
import unicodedata
from TTS.api import TTS
from pydub import AudioSegment, silence

def get_tts_device():
    forced = os.getenv("FORCE_TTS_DEVICE")
    if forced:
        return forced
    return "cpu"

TTS_DEVICE = get_tts_device()
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(TTS_DEVICE)

SUPPORTED_XTTS_LANGS = [
    "en", "es", "fr", "de", "it", "pt",
    "pl", "tr", "ru", "nl",
    "cs", "ar", "zh", "ja", "ko", "hi"
]

# Natural pause between sentences (ms)
SENTENCE_PAUSE_MS = 100

# ── Per-language ms-per-character budget for the duration guard ──
# Hindi/CJK characters represent full syllables so need more time per char.
# These are generous upper bounds — just catching runaway loops.
LANG_MS_PER_CHAR = {
    "hi": 200,   # Devanagari syllabic script
    "zh": 220,   "ja": 220,   "ko": 200,   # CJK
    "ar": 180,                              # Arabic script
    "ru": 150,   "pl": 140,   "cs": 140,   # Cyrillic / extended Latin
    # default for Latin-script languages:
    "default": 120,
}
MIN_FLOOR_MS = 2500   # never flag segments shorter than this


def ms_per_char(language: str) -> int:
    return LANG_MS_PER_CHAR.get(language, LANG_MS_PER_CHAR["default"])


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\n", " ").strip()
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)
    text = re.sub(r'([.!?]){2,}', r'\1', text)
    return text


def split_sentences(text: str) -> list:
    """
    Split on sentence-ending punctuation BUT protect abbreviations.
    e.g. 'बी.टेक', 'B.Tech', 'Dr.Smith' are NOT split.
    """
    # Protect abbreviations: short word + '.' + immediately another word char
    protected = re.sub(r'(?<=\S{1,4})\.\s*(?=\S)', '<<DOT>>', text)
    # Also protect single-letter abbrevs before uppercase / Devanagari
    protected = re.sub(r'(\b\w{1,3})\.\s+(?=[A-Z\u0900-\u097F])', r'\1<<DOT>>', protected)

    pattern = r'(?<=[.!?।。！？؟…])\s+'
    sentences = re.split(pattern, protected)
    sentences = [s.replace('<<DOT>>', '.').strip() for s in sentences]
    return [s for s in sentences if s]


def enforce_chunk_limit(sentences: list, max_len: int = 70) -> list:
    chunks = []
    for s in sentences:
        if len(s) <= max_len:
            chunks.append(s)
        else:
            words = s.split()
            temp = ""
            for w in words:
                candidate = (temp + " " + w).strip()
                if len(candidate) <= max_len:
                    temp = candidate
                else:
                    if temp:
                        chunks.append(temp)
                    temp = w
            if temp:
                chunks.append(temp)
    return chunks


def trim_segment_silence(segment: AudioSegment, silence_thresh_db: int = -40, padding_ms: int = 40) -> AudioSegment:
    chunks = silence.detect_nonsilent(segment, min_silence_len=80, silence_thresh=silence_thresh_db)
    if not chunks:
        return segment
    start_trim = max(0, chunks[0][0] - padding_ms)
    end_trim   = min(len(segment), chunks[-1][1] + padding_ms)
    return segment[start_trim:end_trim]


def build_clean_speaker_sample(speaker_wav: str) -> str:
    audio = AudioSegment.from_wav(speaker_wav)
    audio = audio.set_channels(1).set_frame_rate(24000)
    TARGET_MS = 6000
    if len(audio) <= TARGET_MS:
        sample = audio
    else:
        step = 500
        best_rms, best_start = -1, 0
        for start in range(0, len(audio) - TARGET_MS, step):
            rms = audio[start: start + TARGET_MS].rms
            if rms > best_rms:
                best_rms, best_start = rms, start
        sample = audio[best_start: best_start + TARGET_MS]
    out_path = "clean_speaker.wav"
    sample.export(out_path, format="wav")
    return out_path


def _run_tts(sentence, speaker_sample, language, temp_output, temperature, repetition_penalty, speed):
    tts.tts_to_file(
        text=sentence,
        speaker_wav=speaker_sample,
        language=language,
        file_path=temp_output,
        temperature=temperature,
        repetition_penalty=repetition_penalty,
        length_penalty=1.0,
        speed=speed
    )


def synthesize_voice(text: str, language: str, speaker_wav: str) -> str:
    if language not in SUPPORTED_XTTS_LANGS:
        raise ValueError(f"Language '{language}' not supported by XTTS")
    if not os.path.exists(speaker_wav):
        raise FileNotFoundError(f"Speaker file not found: {speaker_wav}")

    text = clean_text(text)
    if language == "ja":
        text = text.replace("。", "。 ")
    if language == "hi":
        text = text.replace("।", "। ")

    sentences = split_sentences(text)
    sentences = enforce_chunk_limit(sentences, max_len=70)
    print(f"[TTS] Total chunks: {len(sentences)}")

    speaker_sample = build_clean_speaker_sample(speaker_wav)
    pause          = AudioSegment.silent(duration=SENTENCE_PAUSE_MS)
    combined_audio = AudioSegment.empty()
    budget         = ms_per_char(language)

    for i, sentence in enumerate(sentences):
        if not sentence.strip():
            continue

        temp_output = f"temp_sentence_{i}.wav"

        _run_tts(
            sentence, speaker_sample, language, temp_output,
            temperature=0.3,
            repetition_penalty=2.0,
            speed=0.95
        )

        segment_audio = AudioSegment.from_wav(temp_output)
        char_count    = len(sentence)
        duration_ms   = len(segment_audio)
        max_expected  = max(char_count * budget, MIN_FLOOR_MS)
        flagged       = duration_ms > max_expected

        print(f"[TTS] Chunk {i:02d} | chars={char_count} | {duration_ms}ms | max={max_expected}ms {'⚠' if flagged else '✓'}")

        if flagged:
            os.remove(temp_output)
            _run_tts(
                sentence, speaker_sample, language, temp_output,
                temperature=0.2,
                repetition_penalty=2.5,
                speed=1.0
            )
            retry_audio = AudioSegment.from_wav(temp_output)
            print(f"[TTS-RETRY] Chunk {i:02d} | {len(retry_audio)}ms")

            # ← KEY FIX: only use retry if it actually improved things
            if len(retry_audio) >= duration_ms:
                print(f"[TTS-RETRY] Retry was not shorter — keeping original.")
                os.remove(temp_output)
                _run_tts(
                    sentence, speaker_sample, language, temp_output,
                    temperature=0.3, repetition_penalty=2.0, speed=0.95
                )
                segment_audio = AudioSegment.from_wav(temp_output)
            else:
                segment_audio = retry_audio

        segment_audio  = trim_segment_silence(segment_audio)
        combined_audio += segment_audio
        if i < len(sentences) - 1:
            combined_audio += pause

        os.remove(temp_output)

    final_output = "final_tts_output.wav"
    combined_audio.export(final_output, format="wav")
    print(f"[TTS] Final audio: {len(combined_audio)/1000:.2f}s")
    return final_output
