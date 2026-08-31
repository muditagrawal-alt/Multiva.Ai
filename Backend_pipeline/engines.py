"""
Which model runs each stage.

Every option here is one the code can actually load. A dropdown offering a
model the pipeline cannot use is worse than no dropdown, so this file is
deliberately conservative: the catalogue lists what has a real swap path, the
sizes are the real download sizes, and anything else has to be typed in as a
custom id with a warning attached.

Stage models are read at import by the modules that own them and cached for the
life of the process, so a change here takes effect when the engine restarts.
That is stated in the UI rather than papered over.
"""

from __future__ import annotations

import json
import os
import stat

SETTINGS_PATH = os.path.expanduser(
    os.getenv("MULTIVA_ENGINES", "~/.multiva/engines.json"))

# `key` is what the stage's module reads. `size` is the download, not the
# on-disk footprint after conversion.
CATALOG = {
    "stt": {
        "label": "Speech recognition",
        "why": "Produces the transcript and the segment boundaries every later stage hangs off. The single highest-leverage choice here.",
        "env": "WHISPER_MODEL",
        "default": "large-v3",
        "options": [
            {"id": "large-v3", "label": "Whisper large-v3", "size": "3.1 GB",
             "repo": "Systran/faster-whisper-large-v3",
             "note": "Best accuracy across Indian languages. The default."},
            {"id": "medium", "label": "Whisper medium", "size": "1.5 GB",
             "repo": "Systran/faster-whisper-medium",
             "note": "About twice as fast, noticeably weaker on accented speech."},
            {"id": "small", "label": "Whisper small", "size": "500 MB",
             "repo": "Systran/faster-whisper-small",
             "note": "For quick tests. Transcription errors propagate into the dub."},
            {"id": "distil-large-v3", "label": "Distil-Whisper large-v3", "size": "1.5 GB",
             "repo": "Systran/faster-distil-whisper-large-v3",
             "warn": "English only. Do not use it to transcribe Indian-language source."},
        ],
    },
    "mt": {
        "label": "Translation",
        "why": "Translates each segment. Larger models handle idiom and register better, at a real cost in memory and time.",
        "env": "NLLB_MODEL",
        "default": "facebook/nllb-200-distilled-600M",
        "options": [
            {"id": "facebook/nllb-200-distilled-600M", "label": "NLLB-200 distilled 600M",
             "size": "2.5 GB", "repo": "facebook/nllb-200-distilled-600M",
             "note": "The default. Fastest, and adequate for most speech."},
            {"id": "facebook/nllb-200-distilled-1.3B", "label": "NLLB-200 distilled 1.3B",
             "size": "5.5 GB", "repo": "facebook/nllb-200-distilled-1.3B",
             "note": "Better on long or formal sentences."},
            {"id": "facebook/nllb-200-3.3B", "label": "NLLB-200 3.3B",
             "size": "17 GB", "repo": "facebook/nllb-200-3.3B",
             "warn": "Heavy. Expect a slow first load and high memory use."},
        ],
    },
    "lipsync": {
        "label": "Lip sync",
        "why": "Regenerates the mouth against the new audio. Both checkpoints ship with Wav2Lip; only what is on disk can be selected.",
        "env": "WAV2LIP_CHECKPOINT",
        "default": "wav2lip_gan.pth",
        "options": [
            {"id": "wav2lip_gan.pth", "label": "Wav2Lip GAN", "size": "436 MB",
             "local": "Wav2Lip/checkpoints/wav2lip_gan.pth",
             "note": "Sharper mouth detail. The default."},
            {"id": "wav2lip.pth", "label": "Wav2Lip", "size": "436 MB",
             "local": "Wav2Lip/checkpoints/wav2lip.pth",
             "note": "Slightly more accurate sync, softer detail."},
        ],
    },
    "tts": {
        "label": "Voice cloning",
        "why": "Which engine speaks is chosen per language automatically: IndicF5 for the eleven Indian languages, XTTS elsewhere. What is adjustable is how much work it does per phrase.",
        "env": "INDICF5_NFE_STEP",
        "default": "16",
        "options": [
            {"id": "8", "label": "8 steps - fastest", "size": "-",
             "note": "About half the synthesis time. Rougher, and more variable between takes."},
            {"id": "16", "label": "16 steps - balanced", "size": "-",
             "note": "The default. What every measurement in this project used."},
            {"id": "32", "label": "32 steps - highest quality", "size": "-",
             "warn": "Roughly double the synthesis time, which is already the slowest stage."},
        ],
    },
}

def default_output_dir() -> str:
    """
    Where finished videos are filed.

    Editors keep renders somewhere the user chose, not buried in a working
    directory, so this defaults to the platform's own video folder and can be
    pointed anywhere on first run.
    """
    home = os.path.expanduser("~")
    movies = os.path.join(home, "Movies")          # macOS
    videos = os.path.join(home, "Videos")          # Windows and most Linux
    base = movies if os.path.isdir(movies) else (videos if os.path.isdir(videos) else home)
    return os.path.join(base, "Multiva")


_DEFAULTS = {stage: spec["default"] for stage, spec in CATALOG.items()}
_DEFAULTS["output_dir"] = default_output_dir()
THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def load() -> dict:
    """Stored choices over defaults, with the environment winning over both."""
    data = dict(_DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in _DEFAULTS and v})
    except FileNotFoundError:
        pass
    except Exception as e:                                   # noqa: BLE001
        print(f"[ENGINES] Ignoring unreadable settings at {SETTINGS_PATH}: {e}")

    # An explicit environment variable pins the stage, so a deployment can
    # fix its configuration and the UI cannot override it.
    for stage, spec in CATALOG.items():
        if os.getenv(spec["env"]):
            data[stage] = os.environ[spec["env"]]
    if os.getenv("MULTIVA_OUTPUT_DIR"):
        data["output_dir"] = os.environ["MULTIVA_OUTPUT_DIR"]
    return data


def output_dir(create: bool = True) -> str:
    """
    The folder finished videos are filed into, created on demand.

    Falls back to the default if the configured path cannot be created, so a
    stale setting pointing at a removed drive never fails a render that has
    otherwise succeeded.
    """
    path = os.path.expanduser(load().get("output_dir") or default_output_dir())
    if not create:
        return path
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError as e:
        fallback = default_output_dir()
        print(f"[ENGINES] Cannot use {path} ({e}); filing renders in {fallback}")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def get(stage: str) -> str:
    """The model id for one stage. Called by the module that owns that stage."""
    return load().get(stage, _DEFAULTS.get(stage, ""))


def save(choices: dict) -> dict:
    """Persist stage choices. Unknown stages are ignored rather than stored."""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            stored = json.load(f)
    except Exception:                                        # noqa: BLE001
        stored = {}
    if not isinstance(stored, dict):
        stored = {}

    for stage, value in (choices or {}).items():
        if stage in CATALOG and isinstance(value, str) and value.strip():
            stored[stage] = value.strip()

    folder = (choices or {}).get("output_dir")
    if isinstance(folder, str) and folder.strip():
        stored["output_dir"] = os.path.expanduser(folder.strip())

    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    tmp = f"{SETTINGS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, SETTINGS_PATH)
    return load()


def configured() -> bool:
    """Whether the user has been through setup at least once."""
    return os.path.exists(SETTINGS_PATH)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def _hf_cached(repo: str) -> bool:
    root = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface")
    folder = os.path.join(root, "hub", "models--" + repo.replace("/", "--"),
                          "snapshots")
    return os.path.isdir(folder) and bool(os.listdir(folder))


def catalog() -> dict:
    """
    The catalogue annotated with what is already downloaded.

    Choosing a model that is not present means a multi-gigabyte download on the
    next run, and the person choosing should see that before they choose.
    """
    current = load()
    out = {}
    for stage, spec in CATALOG.items():
        options = []
        for opt in spec["options"]:
            ready, source = True, "builtin"
            if opt.get("repo"):
                ready = _hf_cached(opt["repo"])
                source = "download"
            elif opt.get("local"):
                ready = os.path.isfile(os.path.join(THIS_DIR, opt["local"]))
                # Wav2Lip checkpoints are not on the Hub. Telling someone a
                # missing one "will be fetched" would be a lie they only
                # discover mid-render.
                source = "manual"
            options.append({**opt, "ready": ready, "source": source})
        out[stage] = {
            "label": spec["label"], "why": spec["why"],
            "default": spec["default"], "current": current[stage],
            "pinned": bool(os.getenv(spec["env"])),
            "options": options,
        }
    return out
