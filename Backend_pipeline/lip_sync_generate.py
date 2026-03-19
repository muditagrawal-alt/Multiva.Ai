import torch
import subprocess
from pathlib import Path

MODEL_PATH = "models/Wav2Lip-SD-GAN.pt"


def load_lip_sync_model():
    # Force CPU or MPS (NEVER CUDA on Mac)
    if torch.backends.mps.is_available():
        device = torch.device("mps")
    else:
        device = torch.device("cpu")

    print(f"Using device for lip-sync: {device}")

    model = torch.jit.load(MODEL_PATH, map_location=device)
    model = model.to(device)
    model.eval()

    return model, device


# Load once at startup
model, device = load_lip_sync_model()


def generate_lip_synced_video(input_video_path, audio_path, output_path):
    """
    Uses Wav2Lip TorchScript model via subprocess (safe + stable method).
    """

    print("Generating lip-synced video...")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        output_path
    ]

    subprocess.run(cmd, check=True)

    print("Lip-sync complete.")
    return output_path