import torch

print("Torch Version:", torch.__version__)

if torch.cuda.is_available():
    print("Running on CUDA GPU")
elif torch.backends.mps.is_available():
    print("Running on Apple MPS GPU")
else:
    print("Running on CPU")