"""
Every model Multiva needs, and how to get it.

Shared by the command line and the studio's setup page. Downloading a few
gigabytes should not require opening a terminal, but the terminal path has to
keep working for people who prefer it, so both call the same code.

Each file is verified by SHA-256 after download, resumes where it stopped, and
falls through to another mirror rather than failing outright.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import urllib.error
import urllib.request

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
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "ok" if h.hexdigest() == want_sha else "wrong contents"


def fetch_weight(label, path, size, want_bytes, want_sha, mirrors,
                 attempts_per_mirror: int = 3,
                 on_bytes=None, should_stop=None) -> bool:
    """
    Download to a .part file, verify it, then move it into place.

    Resumable and retried, because these are hundreds of megabytes from hosts
    that throttle: a read that times out 40% through a 436 MB file should cost
    the remaining 60%, not the whole download.
    """
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
                    with open(part, mode) as out:  # noqa: SIM117
                        while True:
                            block = r.read(1 << 20)
                            if not block:
                                break
                            out.write(block)
                            done += len(block)
                            if on_bytes:
                                on_bytes(done)
                            else:
                                pct = done * 100 // (want_bytes or 1)
                                print(f"\r          {host}  {pct:3d}%  "
                                      f"{done // 1_000_000} MB", end="", flush=True)
                            if should_stop and should_stop():
                                raise RuntimeError("Cancelled")
                if not on_bytes:
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




# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

WEIGHT_NOTES = {
    "Lip sync": "Moves the mouth to match the dubbed audio",
    "Face detection": "Finds the face in each frame for lip sync",
}


def inventory() -> list[dict]:
    """
    Every model, in one shape, with whether it is already here.

    The two kinds are fetched differently - a Hub snapshot against a single
    verified file - but nothing above this needs to care which.
    """
    rows = []
    for label, repo, why, size, required in MODELS:
        rows.append({
            "id": repo, "label": label, "why": why, "size": size,
            "required": bool(required), "kind": "hub",
            "present": is_cached(repo),
        })
    for label, rel, size, want_bytes, want_sha, mirrors in WEIGHTS:
        path = os.path.join(ROOT, rel)
        rows.append({
            "id": rel, "label": label,
            "why": WEIGHT_NOTES.get(label, ""),
            "size": size, "required": True, "kind": "file",
            "present": os.path.isfile(path)
                       and os.path.getsize(path) == want_bytes,
        })
    return rows


def missing(core_only: bool = False) -> list[dict]:
    return [r for r in inventory()
            if not r["present"] and (r["required"] or not core_only)]


# ---------------------------------------------------------------------------
# Fetching, with somewhere to watch it from
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_state: dict = {
    "running": False, "label": "", "index": 0, "total": 0,
    "bytes": 0, "of": 0, "error": None, "finished": False, "cancel": False,
}


def status() -> dict:
    with _lock:
        return dict(_state)


def cancel() -> None:
    """Ask the run to stop. It lands after the file in flight."""
    with _lock:
        _state["cancel"] = True


def _set(**fields) -> None:
    with _lock:
        _state.update(fields)


def _cancelled() -> bool:
    with _lock:
        return bool(_state["cancel"])


def fetch_hub(repo_id: str) -> None:
    """Pull a Hub snapshot. Resumable on its own; no byte count to report."""
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=repo_id)


def run(ids: list[str] | None = None) -> bool:
    """
    Fetch the named models, or everything missing. Returns False if a run is
    already in flight, because two downloads writing the same .part file would
    corrupt each other.
    """
    with _lock:
        if _state["running"]:
            return False
        _state.update({"running": True, "label": "", "index": 0, "total": 0,
                       "bytes": 0, "of": 0, "error": None,
                       "finished": False, "cancel": False})

    def work():
        try:
            todo = [r for r in inventory() if not r["present"]]
            if ids:
                todo = [r for r in todo if r["id"] in ids]
            _set(total=len(todo))
            weights = {w[1]: w for w in WEIGHTS}

            for i, row in enumerate(todo, 1):
                if _cancelled():
                    _set(error="Cancelled.")
                    break
                _set(index=i, label=row["label"], bytes=0, of=0)

                if row["kind"] == "hub":
                    fetch_hub(row["id"])
                else:
                    label, rel, size, want_bytes, want_sha, mirrors = weights[row["id"]]
                    _set(of=want_bytes)
                    ok = fetch_weight(
                        label, os.path.join(ROOT, rel), size,
                        want_bytes, want_sha, mirrors,
                        on_bytes=lambda n: _set(bytes=n),
                        should_stop=_cancelled)
                    if not ok:
                        raise RuntimeError(
                            f"{label} could not be downloaded from any mirror.")
        except Exception as exc:                             # noqa: BLE001
            _set(error=f"{type(exc).__name__}: {exc}")
            print(f"[MODELS] Download failed: {exc}")
        finally:
            _set(running=False, finished=True)

    threading.Thread(target=work, daemon=True).start()
    return True
