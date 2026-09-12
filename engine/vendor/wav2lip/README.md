# Wav2Lip (vendored)

The parts of [Rudrabha/Wav2Lip](https://github.com/Rudrabha/Wav2Lip) that
`engine/lip_sync.py` imports, copied at upstream commit `bac9a81`:

| Path | What it is |
|---|---|
| `audio.py`, `hparams.py` | mel-spectrogram front end, and the constants it needs |
| `models/` | the Wav2Lip generator and SyncNet definitions |
| `face_detection/` | the s3fd face detector |

Training scripts, evaluation code and the original inference CLI are not
included; the engine runs its own loop (see `docs/ARCHITECTURE.md`, "Lip
sync"). Nothing here is modified.

The weights are not in git. `scripts/download_models.py` fetches them,
verified by SHA-256, into:

```
engine/vendor/wav2lip/checkpoints/wav2lip_gan.pth
engine/vendor/wav2lip/face_detection/detection/sfd/s3fd.pth
```

## Licence

Upstream states: *"This repository can only be used for personal, research
and non-commercial purposes. For commercial requests, contact
rudrabha@synclabs.so or prajwal@synclabs.so."* That restriction travels with
this copy. If you use this project commercially, lip sync needs a licence
from the authors, or a different model.

Please cite the paper if you use it:

```
@inproceedings{10.1145/3394171.3413532,
  author    = {Prajwal, K R and Mukhopadhyay, Rudrabha and Namboodiri, Vinay P. and Jawahar, C.V.},
  title     = {A Lip Sync Expert Is All You Need for Speech to Lip Generation In the Wild},
  booktitle = {Proceedings of the 28th ACM International Conference on Multimedia},
  year      = {2020},
  doi       = {10.1145/3394171.3413532}
}
```
