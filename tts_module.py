import os
import re
import unicodedata
import torch
from TTS.api import TTS
from pydub import AudioSegment

# --------------------------------------------------
# DEVICE SELECTION
# XTTS unstable on MPS → avoid it
# Force CPU for now
# --------------------------------------------------

def get_tts_device():
    """
    For now:
    - XTTS runs safest on CPU
    - Avoid MPS
    - Allow override via FORCE_TTS_DEVICE
    """

    forced = os.getenv("FORCE_TTS_DEVICE")
    if forced:
        return forced

    # If in future you want CUDA for other models,
    # you can change this logic.
    return "cpu"


TTS_DEVICE = get_tts_device()

# Enable fallback (safe on Mac)
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(TTS_DEVICE)


# --------------------------------------------------
# Supported XTTS Languages
# --------------------------------------------------

SUPPORTED_XTTS_LANGS = [
    "en", "es", "fr", "de", "it", "pt",
    "pl", "tr", "ru", "nl",
    "cs", "ar", "zh", "ja", "ko", "hi"
]


# -----------------------
# Text Cleaning
# -----------------------

def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\n", " ").strip()

    # Remove invisible unicode chars
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)

    return text


# -----------------------
# Multilingual Sentence Splitting
# -----------------------

def split_sentences(text: str):
    pattern = r'(?<=[.!?।。！？؟])\s*'
    sentences = re.split(pattern, text)
    return [s.strip() for s in sentences if s.strip()]


# -----------------------
# Enforce Character Limit
# -----------------------

def enforce_chunk_limit(sentences, max_len=180):
    chunks = []
    for s in sentences:
        while len(s) > max_len:
            chunks.append(s[:max_len])
            s = s[max_len:]
        chunks.append(s)
    return chunks


# -----------------------
# Main TTS Function
# -----------------------

def synthesize_voice(text: str, language: str, speaker_wav: str):

    if language not in SUPPORTED_XTTS_LANGS:
        raise ValueError(f"Language '{language}' not supported by XTTS")

    if not os.path.exists(speaker_wav):
        raise FileNotFoundError(f"Speaker file not found: {speaker_wav}")

    text = clean_text(text)

    # Japanese spacing improvement
    if language == "ja":
        text = text.replace("。", "。 ")

    sentences = split_sentences(text)
    sentences = enforce_chunk_limit(sentences)

    combined_audio = AudioSegment.empty()

    for i, sentence in enumerate(sentences):

        if not sentence.strip():
            continue

        temp_output = f"temp_sentence_{i}.wav"

        tts.tts_to_file(
            text=sentence,
            speaker_wav=speaker_wav,
            language=language,
            file_path=temp_output,
            temperature=0.6,
            repetition_penalty=1.5,
            length_penalty=1.0,
            speed=0.95
        )

        segment_audio = AudioSegment.from_wav(temp_output)

        pause = AudioSegment.silent(duration=250)
        combined_audio += segment_audio + pause

        os.remove(temp_output)

    final_output = "final_tts_output.wav"
    combined_audio.export(final_output, format="wav")

    return final_output