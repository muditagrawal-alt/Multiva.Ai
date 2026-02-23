import os
import re
import unicodedata
import torch
from TTS.api import TTS
from pydub import AudioSegment

# --------------------------------------------------
# IMPORTANT:
# XTTS + Apple MPS = unstable (complex tensor issue)
# So we FORCE CPU for stable voice cloning
# --------------------------------------------------

os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

DEVICE = "cpu"
print("Using device for XTTS:", DEVICE)

# Load XTTS v2 model
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(DEVICE)


# -----------------------
# Text Cleaning
# -----------------------

def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\n", " ").strip()
    return text


def split_sentences(text: str):
    # Works better for multilingual punctuation
    return re.split(r'(?<=[.!?।])\s+', text)


# -----------------------
# Main TTS Function
# -----------------------

def synthesize_voice(text: str, language: str, speaker_wav: str):

    # Safety check
    if not os.path.exists(speaker_wav):
        raise FileNotFoundError(f"Speaker file not found: {speaker_wav}")

    text = clean_text(text)
    sentences = split_sentences(text)

    combined_audio = AudioSegment.empty()

    for i, sentence in enumerate(sentences):

        if not sentence.strip():
            continue

        temp_output = f"temp_sentence_{i}.wav"

        print(f"Generating sentence {i+1}/{len(sentences)}")

        tts.tts_to_file(
            text=sentence,
            speaker_wav=speaker_wav,   # Your cloned voice reference
            language=language,        # "es", "hi", "en", etc.
            file_path=temp_output,
            temperature=0.6,          # Lower = more stable
            repetition_penalty=1.5,   # Avoid robotic looping
            length_penalty=1.0,
            speed=0.95                # Slightly slower = more natural
        )

        segment_audio = AudioSegment.from_wav(temp_output)

        # Natural pause between sentences
        pause = AudioSegment.silent(duration=250)

        combined_audio += segment_audio + pause

        os.remove(temp_output)

    final_output = "final_tts_output.wav"
    combined_audio.export(final_output, format="wav")

    print("TTS generation complete.")
    return final_output