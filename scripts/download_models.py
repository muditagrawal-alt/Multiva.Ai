#!/usr/bin/env python3
"""
Pre-download every model Multiva needs.

The pipeline pulls these automatically on first use, but that means the first
clone appears to hang for several minutes while ~5 GB downloads. Running this
first makes that wait explicit and resumable, and it verifies the environment
before anyone tries to dub a video.

    python scripts/download_models.py            # everything
    python scripts/download_models.py --core     # required models only
    python scripts/download_models.py --check    # report only, download nothing

Works on macOS, Windows and Linux.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

# (label, repo id, what it does, roughly how big, required?)
MODELS = [
    ("Speech recognition", "Systran/faster-whisper-large-v3",
     "Transcribes the source video and finds segment boundaries", "3.1 GB", True),
    ("Translation", "facebook/nllb-200-distilled-600M",
     "Translates each segment into the target language", "2.5 GB", True),
    ("Voice cloning", "ai4bharat/IndicF5",
     "Speaks the translation in the reference voice, 11 Indian languages", "1.4 GB", True),
    ("Vocoder", "charactr/vocos-mel-24khz",
     "Turns IndicF5's mel output into audio", "55 MB", True),
    ("Speaker scoring", "microsoft/wavlm-base-plus-sv",
     "Scores clone similarity in the evaluation harness", "400 MB", False),
]

# Wav2Lip weights are not on the Hub; they ship in the repo.
LOCAL_WEIGHTS = [
    ("Lip sync", "Backend_pipeline/Wav2Lip/checkpoints/wav2lip_gan.pth", "436 MB"),
    ("Face detection", "Backend_pipeline/Wav2Lip/face_detection/detection/sfd/s3fd.pth", "86 MB"),
]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cache_root() -> str:
    return os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface"
    )


def is_cached(repo_id: str) -> bool:
    """True when the repo already has a materialised snapshot locally."""
    folder = "models--" + repo_id.replace("/", "--")
    path = os.path.join(cache_root(), "hub", folder, "snapshots")
    if not os.path.isdir(path):
        return False
    return any(
        os.scandir(os.path.join(path, entry))
        for entry in os.listdir(path)
        if os.path.isdir(os.path.join(path, entry))
    )


def free_space_gb() -> float:
    return shutil.disk_usage(cache_root() if os.path.isdir(cache_root()) else ROOT).free / 1e9


def main() -> int:
    ap = argparse.ArgumentParser(description="Download Multiva's models")
    ap.add_argument("--core", action="store_true",
                    help="skip optional models (evaluation harness)")
    ap.add_argument("--check", action="store_true",
                    help="report what is present without downloading")
    args = ap.parse_args()

    wanted = [m for m in MODELS if m[4] or not args.core]

    print("\n  Multiva models")
    print("  " + "-" * 62)
    print(f"  Cache: {cache_root()}")
    print(f"  Free disk: {free_space_gb():.1f} GB\n")

    missing = []
    for label, repo, why, size, required in wanted:
        have = is_cached(repo)
        mark = "present" if have else "missing"
        tag = "" if required else "  (optional)"
        print(f"  [{mark:>7}] {label:<20} {size:>7}   {repo}{tag}")
        print(f"            {why}")
        if not have:
            missing.append((label, repo, size))

    print()
    for label, rel, size in LOCAL_WEIGHTS:
        path = os.path.join(ROOT, rel)
        state = "present" if os.path.isfile(path) else "MISSING"
        print(f"  [{state:>7}] {label:<20} {size:>7}   in repo")
        if state == "MISSING":
            print(f"            Expected at {rel}")

    if args.check:
        print(f"\n  {len(missing)} model(s) still to download.\n")
        return 0

    if not missing:
        print("\n  Everything is already downloaded. You can start the studio.\n")
        return 0

    if free_space_gb() < 8:
        print(f"\n  Only {free_space_gb():.1f} GB free. About 8 GB is needed. Aborting.\n")
        return 1

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("\n  huggingface_hub is not installed in this environment.")
        print("  Install dependencies first:  pip install -r requirements.txt\n")
        return 1

    print(f"\n  Downloading {len(missing)} model(s). This is resumable; if it is")
    print("  interrupted, run the command again and it continues.\n")

    for i, (label, repo, size) in enumerate(missing, 1):
        print(f"  ({i}/{len(missing)}) {label} - {size}")
        try:
            snapshot_download(repo_id=repo)
            print(f"          done\n")
        except Exception as exc:                      # noqa: BLE001
            print(f"          FAILED: {exc}\n")
            print("  Fix the error above and re-run. Already-downloaded models are kept.\n")
            return 1

    print("  All models downloaded. Start the studio next.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
