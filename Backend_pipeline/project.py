"""
Project persistence.

Everything needed to reopen a finished dub already survives on disk: the
source clip, the reference, the per-phrase cache, the dub track and the
rendered video. What did not survive was the index — the plan, the reference
metadata and the paths lived in a module-level dict, so restarting the server
turned every editable project into a download link.

This writes that index next to the files it describes. A project is a folder
with a `project.json` in it; nothing else about the layout changes.
"""

from __future__ import annotations

import glob
import json
import os
import time

MANIFEST = "project.json"
VERSION = 1

# Job keys worth persisting. Deliberately explicit: the job dict also holds
# transient things (cancel flags, progress strings) that must not come back.
PERSISTED = (
    "kind", "status", "step", "filename", "title",
    "user_id", "video_id", "url", "output_path", "dub_path",
    "reference_path", "reference_text", "reference_seconds",
    "source_language", "target_language", "segment_count",
    "duration", "video_duration", "input_path", "workdir", "units_dir",
    "plan", "segments", "word_segments", "translated_segments",
    "translated_text", "original_text",
    "reference", "sync", "voice_match", "video_stale", "history",
    "filed_at", "filed_error",
    "music_path", "music_gain", "voiceover_seconds",
)


def _plain(value):
    """
    Make a value JSON-safe.

    Whisper hands back numpy scalars inside its segment dicts, which json
    refuses. Converting on write keeps the manifest readable by anything.
    """
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    # numpy scalars and anything else with a python equivalent
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _plain(item())
        except Exception:                                    # noqa: BLE001
            pass
    return str(value)


def save(job_id: str, job: dict) -> str | None:
    """Write the manifest for a job. Never raises: losing the index is bad,
    but failing a finished render because of it would be worse."""
    workdir = job.get("workdir")
    if not workdir or not os.path.isdir(workdir):
        return None
    payload = {
        "version": VERSION,
        "job_id": job_id,
        "saved_at": time.time(),
        **{k: _plain(job[k]) for k in PERSISTED if k in job},
    }
    path = os.path.join(workdir, MANIFEST)
    try:
        # Write beside the target and rename, so an interrupted write cannot
        # leave a half-parsed manifest behind.
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as e:                                   # noqa: BLE001
        print(f"[PROJECT] Could not save {job_id}: {e}")
        return None


def load(path: str) -> dict | None:
    """Read one manifest, or None if it is unreadable or its files are gone."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:                                   # noqa: BLE001
        print(f"[PROJECT] Skipping unreadable manifest {path}: {e}")
        return None

    if data.get("version") != VERSION:
        print(f"[PROJECT] Skipping {path}: manifest version "
              f"{data.get('version')}, expected {VERSION}")
        return None

    # A manifest describing files that no longer exist is worse than no
    # manifest, because it puts a project in the list that cannot be opened.
    for key in ("dub_path", "input_path"):
        target = data.get(key)
        if target and not os.path.exists(target):
            print(f"[PROJECT] Skipping {data.get('job_id')}: missing {key}")
            return None

    return data


def scan(root: str) -> dict:
    """
    Every project under `root`, keyed by job id.

    Only manifests are read; nothing here touches audio or video, so this stays
    cheap enough to run at startup.
    """
    found = {}
    for path in sorted(glob.glob(os.path.join(root, "*", MANIFEST))):
        data = load(path)
        if not data:
            continue
        job_id = data.get("job_id")
        if not job_id:
            continue
        data.pop("version", None)
        data.pop("job_id", None)
        # Kept as a creation date for projects the database never recorded.
        data["saved_at"] = data.pop("saved_at", None)
        # A project restored from disk is finished by definition; a run that
        # was interrupted mid-render has nothing to resume.
        if data.get("status") != "done":
            continue
        found[job_id] = data
    return found
