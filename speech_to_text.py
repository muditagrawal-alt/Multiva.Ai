import whisper
import torch
import os

MODEL_SIZE = "base"


def get_asr_device():
    """
    Priority:
    1. CUDA
    2. CPU
    (Whisper is unstable on MPS)
    """

    forced = os.getenv("FORCE_DEVICE")
    if forced:
        return forced

    if torch.cuda.is_available():
        return "cuda"
    else:
        return "cpu"


ASR_DEVICE = get_asr_device()

model = whisper.load_model(MODEL_SIZE).to(ASR_DEVICE)


def transcribe_audio(audio_path: str):

    use_fp16 = ASR_DEVICE == "cuda"

    with torch.no_grad():
        result = model.transcribe(
            audio_path,
            fp16=use_fp16
        )

    return {
        "text": result["text"].strip(),
        "language": result["language"]
    }