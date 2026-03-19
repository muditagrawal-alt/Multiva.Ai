import torch

# Path to your downloaded model
MODEL_PATH = "models/wav2lip-SD-GAN.pt"

# Choose device: MPS if available, else CPU
device = torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")
print(f"Using device: {device}")

try:
    # Load TorchScript model directly onto the device
    model = torch.jit.load(MODEL_PATH, map_location=device)
    model.eval()
    print("Lip-sync model loaded successfully!")
except Exception as e:
    print("Error loading lip-sync model:", e)