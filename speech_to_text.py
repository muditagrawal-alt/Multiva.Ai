import whisper
import torch

MODEL_SIZE = "base"  # use "small" if you have good CPU

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

model = whisper.load_model(MODEL_SIZE).to(DEVICE)


def transcribe_audio(audio_path: str):
    result = model.transcribe(
        audio_path,
        fp16=False  # safer on CPU
    )

    return {
        "text": result["text"].strip(),
        "language": result["language"]
    }