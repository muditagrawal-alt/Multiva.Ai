import torch

MODEL_PATH = "models/wav2lip-SD-GAN.pt"

def load_lip_sync_model():
    model = torch.load(MODEL_PATH, map_location="cpu")
    model.eval()
    print("Lip-sync model loaded successfully!")
    return model

if __name__ == "__main__":
    load_lip_sync_model()