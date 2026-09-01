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

# The Wav2Lip weights are not a Hub snapshot repo, and they are too large to
# keep in git, so a fresh clone does not have them. The original project hosts
# them on a university link that has been down for long stretches; these
# mirrors were checked, and each file is verified by SHA-256 after download so
# a truncated or substituted file fails loudly instead of at render time.
#
# (label, path relative to the repo, size, bytes, sha256, mirrors)
WEIGHTS = [
    ("Lip sync",
     "Backend_pipeline/Wav2Lip/checkpoints/wav2lip_gan.pth",
     "436 MB", 435801865,
     "ca9ab7b7b812c0e80a6e70a5977c545a1e8a365a6c49d5e533023c034d7ac3d8",
     ["https://huggingface.co/camenduru/Wav2Lip/resolve/main/checkpoints/wav2lip_gan.pth",
      "https://huggingface.co/numz/wav2lip_studio/resolve/main/Wav2lip/wav2lip_gan.pth",
      "https://huggingface.co/Nekochu/Wav2Lip/resolve/main/wav2lip_gan.pth"]),
    ("Face detection",
     "Backend_pipeline/Wav2Lip/face_detection/detection/sfd/s3fd.pth",
     "86 MB", 89843225,
     "619a31681264d3f7f7fc7a16a42cbbe8b23f31a256f75a366e5a1bcd59b33543",
     # The canonical host for this file is adrianbulat.com, which is where
     # face_alignment itself fetches it from. It was measured at roughly a
     # tenth of the Hub's throughput from here, so it is the fallback.
     ["https://huggingface.co/camenduru/Wav2Lip/resolve/main/checkpoints/s3fd-619a316812.pth",
      "https://huggingface.co/camenduru/Wav2Lip/resolve/main/face_detection/detection/sfd/s3fd.pth",
      "https://www.adrianbulat.com/downloads/python-fan/s3fd-619a316812.pth"]),
]


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# HF_TOKEN is only needed for gated repos, but if the user put one in .env
# that is where they will expect it to be read from.
sys.path.insert(0, os.path.join(ROOT, "Backend_pipeline"))
try:
    import localenv
    localenv.load(os.path.join(ROOT, ".env"))
except ImportError:
    pass


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


def verify(path: str, want_bytes: int, want_sha: str) -> str:
    """"ok", "wrong size", "wrong contents", or "missing"."""
    if not os.path.isfile(path):
        return "missing"
    if os.path.getsize(path) != want_bytes:
        return "wrong size"
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "ok" if h.hexdigest() == want_sha else "wrong contents"


def fetch_weight(label, path, size, want_bytes, want_sha, mirrors,
                 attempts_per_mirror: int = 3) -> bool:
    """
    Download to a .part file, verify it, then move it into place.

    Resumable and retried, because these are hundreds of megabytes from hosts
    that throttle: a read that times out 40% through a 436 MB file should cost
    the remaining 60%, not the whole download.
    """
    import urllib.error
    import urllib.request

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    part = path + ".part"

    for url in mirrors:
        host = url.split("/")[2]
        for attempt in range(attempts_per_mirror):
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have and have >= want_bytes:
                have = 0                       # overshot; start clean
            req = urllib.request.Request(url)
            if have:
                req.add_header("Range", f"bytes={have}-")

            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    # A server that ignores Range replies 200 with the whole
                    # file; appending to what we have would corrupt it.
                    resuming = r.status == 206
                    mode = "ab" if resuming and have else "wb"
                    done = have if resuming and have else 0
                    with open(part, mode) as out:
                        while True:
                            block = r.read(1 << 20)
                            if not block:
                                break
                            out.write(block)
                            done += len(block)
                            pct = done * 100 // (want_bytes or 1)
                            print(f"\r          {host}  {pct:3d}%  "
                                  f"{done // 1_000_000} MB", end="", flush=True)
                print()
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                got = os.path.getsize(part) if os.path.exists(part) else 0
                print(f"\n          {type(exc).__name__} at "
                      f"{got // 1_000_000} MB", end="")
                if attempt < attempts_per_mirror - 1 and got > have:
                    print(" - resuming")
                    continue
                print(" - trying another source" if attempt == attempts_per_mirror - 1
                      else " - retrying")
                if got <= have:
                    break          # no forward progress; this mirror is a dead end
                continue

            state = verify(part, want_bytes, want_sha)
            if state == "ok":
                os.replace(part, path)
                print(f"          verified and installed")
                return True
            print(f"          rejected: {state}")
            _discard(part)
            break                  # a complete but wrong file will not improve

    _discard(part)
    print(f"          Could not download {label}. Fetch it by hand from one of:")
    for url in mirrors:
        print(f"            {url}")
    print(f"          and save it as {path}")
    return False


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


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
    weights_needed = []
    for label, rel, size, want_bytes, want_sha, mirrors in WEIGHTS:
        path = os.path.join(ROOT, rel)
        quick = ("missing" if not os.path.isfile(path)
                 else "ok" if os.path.getsize(path) == want_bytes
                 else "wrong size")
        state = "present" if quick == "ok" else "MISSING"
        print(f"  [{state:>7}] {label:<20} {size:>7}   downloadable")
        if quick != "ok":
            print(f"            Goes to {rel}")
            weights_needed.append((label, path, size, want_bytes, want_sha, mirrors))

    if args.check:
        print(f"\n  {len(missing) + len(weights_needed)} item(s) still to "
              f"download.\n")
        return 0

    for label, path, size, want_bytes, want_sha, mirrors in weights_needed:
        print(f"\n  {label} - {size}")
        if not fetch_weight(label, path, size, want_bytes, want_sha, mirrors):
            return 1

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
