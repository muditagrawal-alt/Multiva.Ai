import asyncio
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

# Ensure Backend_pipeline and root directory are in sys.path
_CURRENT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _CURRENT_DIR.parent
for _p in (str(_CURRENT_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Before anything reads an environment variable: a key in .env is worthless
# if it is loaded after the module that wanted it.
import localenv
_ENV_KEYS = localenv.load(str(_PROJECT_ROOT / ".env"))

from fastapi import (BackgroundTasks, Body, FastAPI, File, Form, HTTPException,
                     Query, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
# There is no database and no object store. Everything a project needs lives in
# its own folder on this machine, indexed by the manifest project.py writes,
# and renders are served straight off disk. Nothing is uploaded anywhere.

# ---------------------------------------------------------------------------
# Pipeline modules
# ---------------------------------------------------------------------------
import numpy as np

import av_sync
import dubbing
import engines
import models
import llm
import project
import subtitles
import voiceover
import languages as L
import reference_audio
import tts_engines
from lip_sync import generate_lip_synced_video
from speech_to_text_v2 import transcribe_audio
from translation_v2 import translate_segments

jobs: dict = {}

UPLOAD_DIR = os.path.abspath(os.path.join(_PROJECT_ROOT, "temp_uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

# The heavy stages (TTS + Wav2Lip) share one GPU. Running two jobs through them
# at once thrashes MPS and makes both slower than running them back to back.
_HEAVY = threading.Semaphore(int(os.getenv("PIPELINE_CONCURRENCY", 1)))

KEEP_INTERMEDIATE = os.getenv("KEEP_INTERMEDIATE", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Cleanup task
# ---------------------------------------------------------------------------
# How long an abandoned working directory survives. Saved projects are never
# swept: they carry a manifest and the user deletes them from the studio.


def sweep_workdirs() -> int:
    """
    Remove working directories left by runs that never became projects.

    Removing the cloud integration also removed the only thing that pruned this
    directory, so failed and cancelled runs accumulated with nothing to clear
    them. A directory is only swept when it has no manifest, is not a live job,
    and has not been touched recently.
    """
    if not os.path.isdir(UPLOAD_DIR):
        return 0
    cutoff = time.time() - engines.tunable("MULTIVA_SWEEP_HOURS") * 3600
    live = {j.get("workdir") for j in jobs.values()}
    freed = 0

    for name in os.listdir(UPLOAD_DIR):
        path = os.path.join(UPLOAD_DIR, name)
        try:
            if os.path.isdir(path):
                if not name.startswith("job_"):
                    continue
                # A manifest means this is a project someone can still open.
                if os.path.exists(os.path.join(path, project.MANIFEST)):
                    continue
                if path in live:
                    continue
                # An empty directory holds nothing and belongs to no project,
                # so it goes now rather than in two days. A task cancelled
                # while its files were being deleted can recreate one, and a
                # deleted project should not leave a trace behind it.
                if os.path.getmtime(path) > cutoff and os.listdir(path):
                    continue
                shutil.rmtree(path)
                freed += 1
            elif os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                # An upload whose job directory is gone has nothing left to
                # belong to.
                job_id = name.split("_", 1)[0]
                if job_id in jobs or os.path.isdir(os.path.join(UPLOAD_DIR, f"job_{job_id}")):
                    continue
                os.remove(path)
                freed += 1
        except OSError as e:
            print(f"[SWEEP] Could not remove {name}: {e}")

    if freed:
        print(f"[SWEEP] Removed {freed} abandoned item(s) from {UPLOAD_DIR}")
    return freed


async def sweep_loop():
    while True:
        try:
            sweep_workdirs()
        except Exception as e:                               # noqa: BLE001
            print(f"[SWEEP] Error: {e}")
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    restored = project.scan(UPLOAD_DIR)
    if restored:
        jobs.update(restored)
        print(f"[APP] Reopened {len(restored)} saved project(s)")
    sweeper = asyncio.create_task(sweep_loop())
    if os.getenv("WARMUP_ON_START", "1") not in ("0", "false", "no"):
        threading.Thread(target=_warmup, daemon=True).start()
    yield
    sweeper.cancel()


def _voice_match(ref_path: str, dub_path: str, source_path: str):
    """
    How close the dub sounds to the reference speaker, on a 0..1 scale.

    A bare WavLM cosine is not reportable: this model returns roughly 0.9 for
    almost any pair, so the raw number looks excellent even for a failed clone.
    eval_harness already solved this by anchoring against two references, and
    the same anchors are available here for free:

        ceiling  reference vs the speaker's own source audio
        floor    reference vs unrelated speakers (the IndicF5 prompt clips)

    The reported value is where the dub falls between them. Best effort: a
    scoring failure must never fail a render that otherwise succeeded.
    """
    if os.getenv("VOICE_MATCH", "1") in ("0", "false", "no"):
        return None
    try:
        from eval_harness import SpeakerScorer
        scorer = SpeakerScorer()
        ref = scorer.embed(ref_path)
        dub = scorer.embed(dub_path)
        if ref is None or dub is None:
            return None
        raw = scorer.cosine(ref, dub)
        ceiling = scorer.cosine(ref, scorer.embed(source_path))
        floor = scorer.floor(ref_path)
        import math
        if any(math.isnan(v) for v in (raw, ceiling, floor)) or ceiling <= floor:
            return None
        norm = (raw - floor) / (ceiling - floor)
        return {"score": round(max(0.0, min(1.0, norm)), 3),
                "cosine": round(raw, 4)}
    except Exception as e:                                   # noqa: BLE001
        print(f"[APP] Voice match unavailable: {e}")
        return None


# What the splash screen reports while the window is still closed. These are
# the real model loads, in the order they happen, not a progress animation.
BOOT = {"stage": "Starting", "index": 0, "total": 4, "ready": False, "notes": []}


def _boot(stage: str, index: int):
    BOOT["stage"] = stage
    BOOT["index"] = index
    print(f"[BOOT] {index}/{BOOT['total']} {stage}")


def _warmup():
    """
    Preload weights so the first render does not pay the load cost.

    Everything here used to load lazily on first use, which meant the first
    dub of a session was several minutes slower than the rest for reasons the
    user could not see. Loading it up front costs the same time somewhere the
    user is already waiting, and gives the splash something true to report.
    """
    steps = [
        ("Loading voice model", lambda: tts_engines.warmup(os.getenv("WARMUP_LANG", "hi"))),
        ("Loading speech recognition", _warm_asr),
        ("Loading translator", _warm_translator),
        ("Loading lip sync", _warm_lipsync),
    ]
    BOOT["total"] = len(steps)
    for i, (label, fn) in enumerate(steps, 1):
        _boot(label, i)
        try:
            fn()
        except Exception as e:                               # noqa: BLE001
            # A component that will not load is not fatal at boot; the stage
            # that needs it will fail with a real message. Record it so the
            # splash can say so rather than claiming everything is ready.
            print(f"[BOOT] {label} failed: {e}")
            BOOT["notes"].append(f"{label} unavailable")
    BOOT["stage"] = "Ready"
    BOOT["ready"] = True
    print("[BOOT] Warmup complete")


def _warm_asr():
    from speech_to_text_v2 import _load_model
    _load_model()


def _warm_translator():
    from translation_v2 import _load_model
    _load_model()


def _warm_lipsync():
    import lip_sync
    lip_sync.warmup()


app = FastAPI(title="Multiva.Ai — Indian-language Voice Cloning API",
              lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def format_duration(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


class JobCancelled(Exception):
    """Raised inside the pipeline when the user has asked it to stop."""


def _safe_name(name: str) -> str:
    """
    Reduce an uploaded filename to something that cannot leave UPLOAD_DIR.

    Only spaces were replaced before, so "../../../tmp/x.mp4" joined onto the
    uploads directory walked straight out of it: the job-id prefix does not
    help once a later component is "..". Take the basename, drop separators and
    leading dots, and keep the result to a length every filesystem accepts.
    """
    base = os.path.basename(str(name or "")).replace("\\", "_")
    base = re.sub(r"[/\x00]", "_", base)
    base = base.lstrip(". ").strip() or "upload"
    # Leave room for the job-id prefix and the suffixes the pipeline appends.
    root, ext = os.path.splitext(base)
    return (root[:80] or "upload") + ext[:12]


def _save(job_id: str) -> None:
    """Persist a job if it is still there. A project deleted mid-render is a
    reason to stop writing, not to raise."""
    job = jobs.get(job_id)
    if job is not None:
        project.save(job_id, job)


def _set(job_id: str, **kw):
    job = jobs.get(job_id)
    if job is None:
        return
    job.update(kw)
    # Every step report doubles as a cancellation checkpoint. The two longest
    # stages report per segment and per frame batch, so a cancel lands within
    # seconds instead of at the end of the run. Terminal updates carry a
    # `status` and must never raise.
    if "step" in kw and "status" not in kw and job.get("cancel_requested"):
        raise JobCancelled()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def process_video_task(job_id: str, input_path: str, original_language: str,
                       target_language: str, filename: str,
                       user_id: str = "anonymous",
                       trim_start: float = None, trim_end: float = None,
                       music_path: str = None, music_gain: float = -18.0,
                       kind: str = "dub"):
    """
    Segment-level dubbing pipeline.

    The dubbed track is built to exactly the video's duration (see `dubbing`),
    so lip sync receives matched streams and the output cannot drift, loop or
    truncate. There is no post-hoc tempo correction anywhere in this function.
    """
    workdir = os.path.join(UPLOAD_DIR, f"job_{job_id}")
    os.makedirs(workdir, exist_ok=True)

    stem = os.path.splitext(os.path.basename(filename))[0]

    try:
        _set(job_id, status="processing", step="validating")

        if not os.path.exists(input_path) or os.path.getsize(input_path) < 1000:
            raise ValueError("Uploaded file is missing or too small")

        info = av_sync.probe(input_path)
        if not info["video"]:
            raise ValueError("Uploaded file has no video stream")
        video_dur = info["duration"]

        # In and out points, applied before anything else looks at the file, so
        # transcription, reference selection and the timeline all agree on what
        # "the clip" is.
        if trim_start is not None or trim_end is not None:
            a = max(0.0, float(trim_start or 0.0))
            b = min(video_dur, float(trim_end if trim_end is not None else video_dur))
            if b - a < 1.0:
                raise ValueError(
                    f"The trimmed range is only {max(0.0, b - a):.2f}s; "
                    f"at least a second is needed.")
            if a > 0.0 or b < video_dur - 0.01:
                trimmed = os.path.join(workdir, f"{stem}_trimmed.mp4")
                av_sync.trim(input_path, trimmed, a, b)
                input_path = trimmed
                video_dur = av_sync.duration(trimmed)
                print(f"[APP] Trimmed to {a:.2f}s-{b:.2f}s ({video_dur:.2f}s)")
                _set(job_id, duration=format_duration(video_dur))
        _set(job_id, duration=format_duration(video_dur))

        # Validate the target up front — a clear error beats bad audio.
        try:
            target_language = L.normalize(target_language)
            L.engine_for(target_language)
        except L.UnsupportedLanguage as e:
            raise ValueError(str(e))

        print(f"[APP] {filename}: {video_dur:.2f}s, "
              f"{info['video']['width']}x{info['video']['height']} @ "
              f"{info['video']['fps']:.3f}fps -> {L.display_name(target_language)}")

        # ── 1. Audio for STT (16 kHz mono is what Whisper wants) ──
        _set(job_id, step="extracting_audio")
        stt_audio = av_sync.extract_audio(
            input_path, os.path.join(workdir, f"{stem}_stt.wav"), 16000, 1)

        # ── 2. Transcribe with word timestamps ──
        _set(job_id, step="transcribing")
        result = transcribe_audio(stt_audio)
        segments = result.get("segments") or []
        original_text = result.get("text", "")
        _set(job_id, original_text=original_text)

        if not segments:
            raise RuntimeError(
                "No speech detected in the video — nothing to dub.")

        # Prefer what the user selected; fall back to detection. The old code
        # ignored the UI value entirely and trusted Whisper, which mis-detects
        # on short or accented audio and then mistranslates the whole video.
        source_language = original_language or result.get("language")
        try:
            source_language = L.normalize(source_language)
        except L.UnsupportedLanguage:
            source_language = L.normalize(result.get("language") or "en")
        print(f"[APP] Source language: {source_language} "
              f"(detected {result.get('language')}, requested {original_language})")

        seg_rows = [{"start": float(sg.get("start", 0.0)),
                     "end": float(sg.get("end", 0.0)),
                     "text": (sg.get("text") or "").strip()}
                    for sg in segments]
        common = dict(workdir=workdir, input_path=input_path,
                      video_duration=video_dur, source_language=source_language,
                      target_language=target_language, segment_count=len(segments),
                      segments=seg_rows, word_segments=segments,
                      video_stale=False)

        if kind == "subtitles":
            _set(job_id, status="done", step="complete", **common)
            _save(job_id)
            print(f"[APP] Job {job_id} transcribed ({len(segments)} segments)")
            return

        # ── 3. Reference clip for cloning ──
        _set(job_id, step="selecting_reference")
        reference = reference_audio.build_reference(
            input_path, segments, video_dur,
            os.path.join(workdir, f"{stem}_ref.wav"), sample_rate=24000)

        # ── 4. Translate segment by segment ──
        _set(job_id, step="translating")
        translated = translate_segments(segments, source_language, target_language)
        from translation_v2 import fix_code_switching
        translated = fix_code_switching(translated, target_language)
        _set(job_id, translated_text=" ".join(t for t in translated if t).strip())

        if not any(t.strip() for t in translated):
            raise RuntimeError("Translation produced no text")

        if kind == "subtitles_translated":
            _set(job_id, status="done", step="complete",
                 translated_segments=list(translated), **common)
            _save(job_id)
            print(f"[APP] Job {job_id} translated ({len(segments)} segments)")
            return

        with _HEAVY:
            # ── 5. Build the dubbed track (exact length by construction) ──
            _set(job_id, step="synthesizing_voice")

            def tts_progress(done, total):
                _set(job_id, step=f"synthesizing_voice ({done + 1}/{total})")

            dub_path = os.path.join(workdir, f"{stem}_dub.wav")
            units_dir = os.path.join(workdir, "units")
            plan = dubbing.build_dubbed_track(
                segments, translated, reference, target_language, video_dur,
                dub_path,
                source_lang=source_language, progress=tts_progress,
                source_audio=stt_audio, cache_dir=units_dir)

            if kind == "audio":
                # The bed belongs in the delivered file when there is no
                # picture to mux it into later.
                delivered = dub_path
                if music_path:
                    delivered = os.path.join(workdir, f"{stem}_mixed.wav")
                    av_sync.mix_music(dub_path, music_path, delivered,
                                      gain_db=music_gain)
                    print(f"[APP] Mixed a music bed at {music_gain:.0f} dB")
                filed = _file_render(
                    job_id, delivered,
                    os.path.splitext(_safe_name(filename))[0].replace(f"{job_id}_", ""),
                    target_language)
                _set(job_id, status="done", step="complete",
                     dub_path=delivered, filed_at=filed,
                     plan=plan, units_dir=units_dir,
                     reference_path=reference["path"],
                     reference_text=reference.get("text", ""),
                     reference_seconds=round(reference.get("duration", 0.0), 2),
                     reference={"path": reference["path"],
                                "text": reference.get("text", ""),
                                "duration": reference.get("duration", 0.0),
                                "start": reference.get("start")},
                     music_path=music_path, music_gain=music_gain,
                     translated_segments=list(translated), **common)
                _save(job_id)
                print(f"[APP] Job {job_id} dubbed to audio")
                return

            # ── 6. Lip sync (single encode, audio muxed in the same pass) ──
            _set(job_id, step="lip_syncing")
            output_video_path = os.path.join(workdir, f"{stem}_dubbed.mp4")

            def w2l_progress(done, total):
                _set(job_id, step=f"lip_syncing ({done}/{total} frames)")

            # The dub track stays voice-only so it can still be A/B'd against
            # the reference; the bed is mixed into a separate file that the
            # picture is muxed with.
            audio_for_video = dub_path
            if music_path:
                mixed = os.path.join(workdir, f"{stem}_mixed.wav")
                av_sync.mix_music(dub_path, music_path, mixed, gain_db=music_gain)
                audio_for_video = mixed
                print(f"[APP] Mixed a music bed at {music_gain:.0f} dB")

            generate_lip_synced_video(
                input_path, audio_for_video, output_video_path,
                wav2lip_batch_size=engines.tunable("W2L_BATCH"),
                face_det_batch_size=engines.tunable("W2L_DET_BATCH"),
                crf=engines.tunable("OUTPUT_CRF"),
                preset=engines.tunable("OUTPUT_PRESET"),
                progress=w2l_progress)

        if not os.path.exists(output_video_path):
            raise RuntimeError("Lip sync produced no output file")

        # ── 7. Verify audio and video actually agree ──
        _set(job_id, step="verifying")
        check = av_sync.verify_sync(output_video_path,
                                    tolerance=engines.tunable("SYNC_TOLERANCE"))
        print(f"[APP] Sync check: {check['reason']}")
        _set(job_id, sync=check)
        if not check["ok"]:
            print(f"[APP] WARNING — output failed sync check: {check['reason']}")

        match = _voice_match(reference["path"], dub_path, stt_audio)
        if match:
            print(f"[APP] Voice match: {match['score']:.3f} (cosine {match['cosine']})")
            _set(job_id, voice_match=match)

        # ── 8. Publish ──
        # The render stays where it was written; this is the URL that serves it.
        _set(job_id, step="uploading_result")
        final_url = f"/jobs/{job_id}/video"
        # The original name, not the job-prefixed working one: this folder is
        # browsed by a person, and "dfd8ae5d-529_clip_hi.mp4" tells them nothing.
        filed_at = _file_render(
            job_id, output_video_path,
            os.path.splitext(_safe_name(filename))[0].replace(f"{job_id}_", ""),
            target_language)

        # The reference clip is what the voice was cloned FROM, and the dub is
        # what came out. Being able to hear both side by side is the only way a
        # user can judge clone quality, so keep them and expose them.
        _set(job_id, status="done", step="complete",
             url=final_url, output_path=output_video_path,
             filed_at=filed_at,
             video_stale=False,
             reference_path=reference["path"],
             reference_text=reference.get("text", ""),
             reference_seconds=round(reference.get("duration", 0.0), 2),
             dub_path=dub_path,
             source_language=source_language,
             segment_count=len(segments),
             # Whisper's per-segment timings and the translation aligned to
             # them. Both were computed and discarded; keeping them is what
             # makes transcript and subtitle export possible.
             plan=plan, units_dir=units_dir, workdir=workdir,
             music_path=music_path, music_gain=music_gain,
             word_segments=segments,
             input_path=input_path, video_duration=video_dur,
             reference={"path": reference["path"],
                        "text": reference.get("text", ""),
                        "duration": reference.get("duration", 0.0),
                        "start": reference.get("start")},
             target_language=target_language,
             segments=[{"start": float(sg.get("start", 0.0)),
                        "end": float(sg.get("end", 0.0)),
                        "text": (sg.get("text") or "").strip()}
                       for sg in segments],
             translated_segments=list(translated))
        _save(job_id)
        print(f"[APP] Job {job_id} completed")

    except JobCancelled:
        print(f"[APP] Job {job_id} cancelled by the user")
        jobs.get(job_id, {}).update(
            {"status": "cancelled", "step": "cancelled",
             "error": "Cancelled before it finished."})
    except Exception as e:
        traceback.print_exc()
        _set(job_id, status="failed", step="error", error=str(e))

    finally:
        if not KEEP_INTERMEDIATE:
            job = jobs.get(job_id, {})
            # Revising a finished dub needs more than the three output files:
            # the (possibly trimmed) source to re-run lip sync against, and the
            # per-phrase cache so one phrase can be rebuilt without redoing all
            # of them. Deleting these turned "edit a word" into a failed job.
            keep = {job.get("output_path"), job.get("reference_path"),
                    job.get("dub_path"), job.get("input_path")}
            output = job.get("output_path")
            for name in os.listdir(workdir) if os.path.isdir(workdir) else []:
                path = os.path.join(workdir, name)
                # The manifest is the index for everything kept above, not an
                # intermediate. Sweeping it away deleted the project the moment
                # it was saved.
                if name == project.MANIFEST:
                    continue
                if path not in keep and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
# The frontend used to be opened straight off disk, which is why profile.html
# had http://localhost:8000 hardcoded while workspace.html computed its own
# base URL. Serving it here gives one origin, kills the CORS/file:// problem,
# and makes every fetch a same-origin relative path.
_WEB_DIR = os.path.join(_PROJECT_ROOT, "web")
if os.path.isdir(_WEB_DIR):
    from fastapi.responses import FileResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles

    # Hashed build assets are immutable, so they can be cached hard.
    app.mount("/app/assets",
              StaticFiles(directory=os.path.join(_WEB_DIR, "assets")),
              name="assets")

    @app.get("/")
    async def _root():
        return RedirectResponse("/app/")

    @app.get("/app")
    @app.get("/app/{path:path}")
    async def _spa(path: str = ""):
        """
        Serve the built single-page app.

        React Router owns /app/studio and /app/library, which do not exist on
        disk. Without this fallback a refresh or a direct link to either one
        would 404 against StaticFiles. Real files (favicon, etc.) are still
        served from disk; everything else returns index.html and lets the
        router resolve it.
        """
        candidate = os.path.normpath(os.path.join(_WEB_DIR, path))
        if (path and candidate.startswith(_WEB_DIR) and os.path.isfile(candidate)):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_WEB_DIR, "index.html"))

else:
    # The interface is a build artefact and is not in the repository, so a
    # fresh clone reaches /app/ before it exists. Saying so at startup beats
    # a 404 with nothing behind it.
    print("[APP] No web/ directory: the studio interface has not been built.")
    print("[APP] Build it with:  cd frontend && npm install && npm run build")
    print("[APP] The API itself is fine; only the browser interface is missing.")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.post("/process_video/")
async def process_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    original_language: str = Query(..., description="Source language code"),
    target_language: str = Query(..., description="Target language code"),
    user_id: str = Query("anonymous", description="Owner of this job"),
    name: str = Query(None, description="What to call this project"),
    kind: str = Query("dub", description="dub, audio, subtitles, "
                                         "or subtitles_translated"),
    trim_start: float = Query(None, description="In point in seconds"),
    trim_end: float = Query(None, description="Out point in seconds"),
    music_gain: float = Query(-18.0, description="Music bed level in dB"),
    music: UploadFile = File(None),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    try:
        target_language = L.normalize(target_language)
        L.engine_for(target_language)
    except L.UnsupportedLanguage as e:
        raise HTTPException(status_code=400, detail=str(e))

    if kind not in ("dub", "audio", "subtitles", "subtitles_translated"):
        raise HTTPException(status_code=400, detail=f"Unknown output: {kind}")

    # The range still has to be checked against the real duration once the file
    # has been probed, but an out point at or before the in point is wrong on
    # its own terms. Catching it here fails in a second instead of accepting a
    # job that dies partway through transcription.
    if trim_start is not None and trim_start < 0:
        raise HTTPException(status_code=400, detail="The in point cannot be negative")
    if (trim_start is not None and trim_end is not None
            and trim_end - trim_start < 1.0):
        raise HTTPException(
            status_code=400,
            detail=f"The trimmed range is {trim_end - trim_start:.2f}s; "
                   f"at least a second is needed.")

    content = await file.read()
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 200MB)")
    if len(content) < 1000:
        raise HTTPException(status_code=400, detail="File too small or empty")

    job_id = str(uuid.uuid4())[:12]
    safe_filename = f"{job_id}_{_safe_name(file.filename)}"
    input_path = os.path.join(UPLOAD_DIR, safe_filename)
    with open(input_path, "wb") as f:
        f.write(content)

    jobs[job_id] = {
        "status": "queued",
        "step": "uploaded",
        "kind": kind,
        "filename": file.filename,
        "title": (name or "").strip() or None,
        "original_language": original_language,
        "target_language": target_language,
        "user_id": user_id,
        "created_at": asyncio.get_event_loop().time(),
    }

    music_path = None
    if music is not None and music.filename:
        music_bytes = await music.read()
        if len(music_bytes) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Music file too large (max 50MB)")
        music_path = os.path.join(UPLOAD_DIR,
                                  f"{job_id}_music_{_safe_name(music.filename)}")
        with open(music_path, "wb") as f:
            f.write(music_bytes)

    background_tasks.add_task(process_video_task, job_id, input_path,
                             original_language, target_language, safe_filename,
                             user_id, trim_start, trim_end, music_path,
                             music_gain, kind)

    return JSONResponse({
        "status": "accepted",
        "job_id": job_id,
        "message": "Video processing started. Poll /jobs/{job_id}/status.",
    })


@app.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    response = {"job_id": job_id, "status": job["status"],
                "step": job.get("step", "unknown"),
                "kind": job.get("kind", "dub"),
                "name": _project_name(job, job_id)}

    if job["status"] == "cancelled":
        response["error"] = job.get("error", "Cancelled.")

    if job["status"] == "done":
        response["url"] = job.get("url")
        response["translated_script"] = job.get("translated_text")
        response["original_text"] = job.get("original_text")
        response["sync"] = job.get("sync")
        response["source_language"] = job.get("source_language")
        response["segment_count"] = job.get("segment_count")
        response["reference_text"] = job.get("reference_text")
        response["reference_seconds"] = job.get("reference_seconds")
        response["voice_match"] = job.get("voice_match")
        response["voiceover_seconds"] = job.get("voiceover_seconds")
        response["has_transcript"] = bool(job.get("segments"))
        response["editable"] = bool(job.get("plan"))
        response["video_duration"] = job.get("video_duration")
        response["filed_at"] = job.get("filed_at")
        response["filed_error"] = job.get("filed_error")
        response["video_stale"] = bool(job.get("video_stale"))
        if job.get("reference_path"):
            response["reference_audio"] = f"/jobs/{job_id}/audio/reference"
        if job.get("dub_path"):
            response["dub_audio"] = f"/jobs/{job_id}/audio/dub"
    if job["status"] == "failed":
        response["error"] = job.get("error", "Unknown error")

    return JSONResponse(response)


@app.get("/jobs/{job_id}/video")
async def get_job_video(job_id: str):
    """The finished render, served from where the pipeline wrote it."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    path = job.get("output_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No render for this job")
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="video/mp4")


@app.get("/jobs/{job_id}/audio/{which}")
async def get_job_audio(job_id: str, which: str):
    """Serve a finished job's reference clip or dubbed track for playback."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    key = {"reference": "reference_path", "dub": "dub_path"}.get(which)
    if not key:
        raise HTTPException(status_code=404, detail="Unknown audio track")
    path = job.get(key)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Audio no longer on disk")
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="audio/wav")


def voiceover_task(job_id: str, input_path: str, script: str,
                   language: str, filename: str, user_id: str):
    """
    Speak a typed script in the voice of an uploaded reference clip.

    Shares the front of the dubbing pipeline (extract, transcribe, choose the
    cleanest reference window) and then skips translation, timeline fitting and
    lip sync entirely, because the user supplied the words and there is no
    video to stay in sync with.
    """
    workdir = os.path.join(UPLOAD_DIR, f"job_{job_id}")
    os.makedirs(workdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(filename))[0]

    try:
        _set(job_id, status="processing", step="validating")
        if not os.path.exists(input_path) or os.path.getsize(input_path) < 1000:
            raise ValueError("Uploaded file is missing or too small")

        info = av_sync.probe(input_path)
        media_dur = info["duration"]

        _set(job_id, step="extracting_audio")
        stt_audio = av_sync.extract_audio(
            input_path, os.path.join(workdir, f"{stem}_stt.wav"), 16000, 1)

        _set(job_id, step="transcribing")
        result = transcribe_audio(stt_audio)
        segments = result.get("segments") or []
        if not segments:
            raise RuntimeError(
                "No speech found in the reference clip, so there is no voice to copy.")

        _set(job_id, step="selecting_reference")
        reference = reference_audio.build_reference(
            input_path, segments, media_dur,
            os.path.join(workdir, f"{stem}_ref.wav"), sample_rate=24000)

        with _HEAVY:
            _set(job_id, step="synthesizing_voice")

            def progress(done, total):
                _set(job_id, step=f"synthesizing_voice ({done + 1}/{total})")

            out_path = os.path.join(workdir, f"{stem}_voiceover.wav")
            result_info = voiceover.render(
                reference["path"], reference.get("text", ""), script,
                language, out_path, progress=progress)

        jobs[job_id].update({
            "status": "done", "step": "complete",
            "dub_path": result_info["path"],
            "reference_path": reference["path"],
            "reference_text": reference.get("text", ""),
            "reference_seconds": round(reference.get("duration", 0.0), 2),
            "segment_count": result_info["chunks"],
            "voiceover_seconds": result_info["duration"],
            "source_language": language,
            "translated_text": script,
        })
        _save(job_id)
        print(f"[APP] Voice-over {job_id} completed "
              f"({result_info['duration']}s, {result_info['chunks']} chunks)")

    except JobCancelled:
        print(f"[APP] Voice-over {job_id} cancelled by the user")
        jobs.get(job_id, {}).update(
            {"status": "cancelled", "step": "cancelled",
             "error": "Cancelled before it finished."})
    except Exception as e:                                   # noqa: BLE001
        traceback.print_exc()
        jobs.get(job_id, {}).update(
            {"status": "failed", "step": "error", "error": str(e)})


def _source_text_at(segments: list, start: float, duration: float) -> str:
    """The source line a phrase overlaps most, for showing beside the edit box."""
    best, best_overlap = "", 0.0
    end = start + duration
    for seg in segments:
        overlap = min(end, seg["end"]) - max(start, seg["start"])
        if overlap > best_overlap:
            best_overlap, best = overlap, (seg.get("text") or "").strip()
    return best


# How many phrase edits can be walked back. Each step keeps one WAV, so the
# ceiling is on disk use as much as on memory.
HISTORY_DEPTH = 20


def _push_history(job: dict, unit: dict) -> None:
    """
    Record a phrase's state before it is overwritten.

    The audio is copied rather than regenerated on undo: re-synthesizing would
    give a different take, because the engine is stochastic, so "undo" would
    not actually return what was there.
    """
    index = unit["index"]
    current = dubbing.segment_path(job["units_dir"], index)
    backup = None
    if os.path.exists(current):
        backup = os.path.join(job["units_dir"], f"undo_{index}_{uuid.uuid4().hex[:8]}.wav")
        try:
            shutil.copyfile(current, backup)
        except OSError as e:                                 # noqa: BLE001
            print(f"[APP] Could not snapshot phrase {index}: {e}")
            backup = None

    history = job.setdefault("history", [])
    history.append({"index": index, "text": unit["text"],
                    "seed": unit.get("seed"), "wav": backup,
                    "cleared": bool(unit.get("cleared"))})

    while len(history) > HISTORY_DEPTH:
        stale = history.pop(0)
        if stale.get("wav") and os.path.exists(stale["wav"]):
            try:
                os.remove(stale["wav"])
            except OSError:
                pass


def _file_render(job_id: str, source_path: str, stem: str, language: str) -> str:
    """
    Copy a finished render into the user's output folder.

    The working copy stays where it is, because the studio serves playback and
    re-renders from there. This is the one a person goes looking for in Finder,
    so it gets a name that says what it is and never overwrites an earlier take.
    """
    try:
        folder = engines.output_dir()
        base = f"{stem}_{language}"
        target = os.path.join(folder, f"{base}.mp4")
        n = 2
        while os.path.exists(target):
            target = os.path.join(folder, f"{base}_{n}.mp4")
            n += 1
        shutil.copyfile(source_path, target)
        print(f"[APP] Filed {os.path.basename(target)} in {folder}")
        job = jobs.get(job_id)
        if job is not None:
            job.pop("filed_error", None)
        return target
    except Exception as e:                                   # noqa: BLE001
        # Filing is a convenience. A render that succeeded must not be
        # reported as failed because a folder was not writable - but it must
        # not be reported as filed either. Saying nothing sent people to an
        # output folder that never got the video.
        print(f"[APP] Could not file the render: {e}")
        job = jobs.get(job_id)
        if job is not None:
            job["filed_error"] = (
                f"The render is finished, but it could not be copied to "
                f"{engines.output_dir(create=False)}: {e}")
        return ""


def _require_editable(job_id: str) -> dict:
    """A job whose phrases can still be rebuilt: finished, with its plan kept."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.get("plan"):
        raise HTTPException(
            status_code=409,
            detail="This run has no editable timeline. Only dubs finished "
                   "since script editing was added can be revised.")
    return job


def _rebuild_track(job: dict) -> float:
    """
    Re-lay every cached phrase onto the track and rewrite the dub WAV.

    Only the edited phrase is ever re-synthesized; the rest are read back from
    the unit cache, so a one-word change costs one phrase and not a whole run.
    """
    plan = job["plan"]
    waves, missing = {}, []
    for unit in plan:
        wave = dubbing.read_unit(job["units_dir"], unit["index"])
        if wave is None:
            missing.append(unit["index"])
        else:
            waves[unit["index"]] = wave

    if missing and len(missing) == len(plan):
        raise HTTPException(
            status_code=409,
            detail="This project's phrase audio is gone, so the track cannot be "
                   "rebuilt. Re-render it.")
    if missing:
        # Not fatal - the track is still mostly right - but the caller has to
        # be able to say which lines fell silent instead of shipping them.
        print(f"[APP] Rebuilt without phrases {missing}: their audio is missing")
    job["missing_units"] = missing

    track = dubbing.assemble(plan, waves, job["video_duration"])
    import soundfile as sf
    sf.write(job["dub_path"], track, dubbing.SAMPLE_RATE, subtype="PCM_16")
    # The rendered video still carries the previous audio until it is redone.
    job["video_stale"] = True
    return round(len(track) / dubbing.SAMPLE_RATE, 3)


@app.get("/jobs/{job_id}/segments")
async def list_segments(job_id: str):
    """The editable phrase timeline: where each one sits and what it says."""
    job = _require_editable(job_id)
    source = job.get("segments") or []
    return JSONResponse({
        "video_duration": job.get("video_duration"),
        "video_stale": bool(job.get("video_stale")),
        "can_undo": len(job.get("history") or []),
        "target_language": job.get("target_language"),
        "segments": [
            {
                "index": u["index"],
                "start": round(u["start"], 3),
                "duration": round(u["duration"], 3),
                "text": u["text"],
                # Units come from phrase splitting, so their index does not
                # line up with the source segments. Match on time instead: the
                # source line a phrase was spoken over.
                "source_text": _source_text_at(source, u["start"], u["duration"]),
                "seed": u.get("seed"),
                "cleared": bool(u.get("cleared")),
                # What this phrase actually takes to say, against the slot it
                # has. The single most useful thing to know about a dub, and
                # it used to take a click per phrase to find out.
                "spoken": dubbing.unit_seconds(job["units_dir"], u["index"]),
            }
            for u in job["plan"]
        ],
    })


@app.post("/jobs/{job_id}/segments/{index}")
async def revise_segment(job_id: str, index: int, body: dict = Body(default={})):
    """
    Rewrite one phrase, re-roll its delivery, or both.

    `text` replaces what the phrase says. `seed` changes the sampling draw,
    which is how you get a different take of the same words: IndicF5 is
    stochastic and the pipeline otherwise pins the seed for reproducibility.
    """
    job = _require_editable(job_id)
    if job["status"] == "processing":
        raise HTTPException(status_code=409, detail="This job is still rendering.")

    unit = next((u for u in job["plan"] if u["index"] == index), None)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"No phrase {index} in this timeline")

    # Snapshot BEFORE the mutations below. Taking it afterwards records the
    # new state as the thing to restore, so undo returns the edit rather than
    # reversing it - which is exactly what it did until this was moved.
    previous = {"text": unit["text"], "seed": unit.get("seed")}

    text = body.get("text")
    if text is not None:
        text = str(text).strip()
        if not text:
            raise HTTPException(status_code=400, detail="A phrase cannot be empty")
        unit["text"] = text

    seed = body.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Seed must be a whole number")
        unit["seed"] = seed

    ref = job["reference"]
    try:
        with _HEAVY:
            wave = dubbing.synth_unit(unit, ref["path"], ref["text"],
                                      ref["duration"], job["target_language"],
                                      seed=unit.get("seed"))
    except Exception as e:                                   # noqa: BLE001
        unit.update(previous)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Could not speak that phrase: {e}")

    if wave.size == 0 or float(np.max(np.abs(wave))) < 1e-4:
        # Same reason as above: keep the words and the audio telling one story.
        unit.update(previous)
        raise HTTPException(
            status_code=500,
            detail="That phrase came back silent. Try different wording or another seed.")

    _push_history(job, {"index": index, **previous})
    unit.pop("cleared", None)
    dubbing.write_unit(job["units_dir"], index, wave)
    duration = _rebuild_track(job)
    project.save(job_id, job)
    spoken = round(len(wave) / dubbing.SAMPLE_RATE, 3)
    print(f"[APP] Job {job_id} phrase {index} revised "
          f"({spoken}s in a {unit['duration']:.2f}s slot)")

    return JSONResponse({
        "index": index,
        "text": unit["text"],
        "seed": unit.get("seed"),
        "spoken_seconds": spoken,
        "slot_seconds": round(unit["duration"], 3),
        # Longer than its slot means the phrase was shortened to fit; that is
        # worth surfacing rather than hiding.
        "overruns": spoken >= round(unit["duration"], 3) - 0.02,
        "track_seconds": duration,
        "video_stale": True,
        "missing_units": job.get("missing_units") or [],
        "can_undo": len(job.get("history", [])),
    })


@app.delete("/jobs/{job_id}/segments/{index}")
async def clear_segment(job_id: str, index: int):
    """
    Silence a phrase, keeping its slot and its words.

    This is where Cut puts the audio. The text is kept rather than emptied:
    an empty phrase is rejected everywhere else, and seeing what used to be
    said is what makes the cut reversible by eye as well as by undo.
    """
    job = _require_editable(job_id)
    if job["status"] == "processing":
        raise HTTPException(status_code=409, detail="This job is still rendering.")

    unit = next((u for u in job["plan"] if u["index"] == index), None)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"No phrase {index} in this timeline")
    if unit.get("cleared"):
        raise HTTPException(status_code=409, detail="That phrase is already silent")

    _push_history(job, unit)
    silence = np.zeros(int(float(unit["duration"]) * dubbing.SAMPLE_RATE),
                       dtype=np.float32)
    dubbing.write_unit(job["units_dir"], index, silence)
    unit["cleared"] = True

    duration = _rebuild_track(job)
    project.save(job_id, job)
    print(f"[APP] Job {job_id} phrase {index} cleared "
          f"({unit['duration']:.2f}s of silence)")

    return JSONResponse({
        "index": index,
        "text": unit["text"],
        "cleared": True,
        "slot_seconds": round(unit["duration"], 3),
        "track_seconds": duration,
        "video_stale": True,
        "can_undo": len(job.get("history", [])),
    })


@app.post("/jobs/{job_id}/undo")
async def undo_phrase(job_id: str):
    """
    Walk back the last phrase edit.

    Restores the previous text, seed and audio. The audio is restored from a
    copy rather than re-synthesized: the engine is stochastic, so regenerating
    would hand back a different take than the one being undone.
    """
    job = _require_editable(job_id)
    if job["status"] == "processing":
        raise HTTPException(status_code=409, detail="This job is still rendering.")

    history = job.get("history") or []
    if not history:
        raise HTTPException(status_code=409, detail="Nothing to undo.")

    entry = history.pop()
    unit = next((u for u in job["plan"] if u["index"] == entry["index"]), None)
    if unit is None:
        raise HTTPException(status_code=409,
                            detail="That phrase is no longer in the timeline.")

    unit["text"] = entry["text"]
    if entry.get("seed") is None:
        unit.pop("seed", None)
    else:
        unit["seed"] = entry["seed"]
    if entry.get("cleared"):
        unit["cleared"] = True
    else:
        unit.pop("cleared", None)

    restored_audio = False
    if entry.get("wav") and os.path.exists(entry["wav"]):
        try:
            shutil.copyfile(entry["wav"],
                            dubbing.segment_path(job["units_dir"], entry["index"]))
            os.remove(entry["wav"])
            restored_audio = True
        except OSError as e:                                 # noqa: BLE001
            print(f"[APP] Could not restore phrase {entry['index']}: {e}")

    duration = _rebuild_track(job)
    project.save(job_id, job)
    print(f"[APP] Job {job_id} undid phrase {entry['index']}")

    return JSONResponse({
        "index": entry["index"], "text": unit["text"],
        "seed": unit.get("seed"),
        # False means the text is back but the audio could not be restored, so
        # the phrase needs re-speaking. Saying so beats a silent mismatch.
        "audio_restored": restored_audio,
        "track_seconds": duration,
        "can_undo": len(history),
        "video_stale": True,
    })


@app.get("/jobs/{job_id}/segments/{index}/audio")
async def segment_audio(job_id: str, index: int):
    """Play back one phrase on its own, without rendering anything."""
    job = _require_editable(job_id)
    path = dubbing.segment_path(job["units_dir"], index)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="That phrase has no audio yet")
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="audio/wav")


@app.post("/jobs/{job_id}/rerender")
async def rerender_video(job_id: str, background_tasks: BackgroundTasks):
    """
    Re-run lip sync against the current audio.

    Editing phrases rewrites the audio immediately so it can be auditioned in
    seconds. The picture is only redone on request, because that is the part
    that costs minutes.
    """
    job = _require_editable(job_id)
    if job["status"] == "processing":
        raise HTTPException(status_code=409, detail="This job is already rendering.")

    job.update({"status": "processing", "step": "lip_syncing",
                "cancel_requested": False, "error": None})
    background_tasks.add_task(rerender_task, job_id)
    return JSONResponse({"job_id": job_id, "status": "processing"})


def rerender_task(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return
    try:
        def w2l_progress(done, total):
            _set(job_id, step=f"lip_syncing ({done}/{total} frames)")

        with _HEAVY:
            generate_lip_synced_video(
                job["input_path"], job["dub_path"], job["output_path"],
                wav2lip_batch_size=engines.tunable("W2L_BATCH"),
                face_det_batch_size=engines.tunable("W2L_DET_BATCH"),
                crf=engines.tunable("OUTPUT_CRF"),
                preset=engines.tunable("OUTPUT_PRESET"),
                progress=w2l_progress)

        _set(job_id, step="verifying")
        check = av_sync.verify_sync(
            job["output_path"], tolerance=engines.tunable("SYNC_TOLERANCE"))

        final_url = f"/jobs/{job_id}/video"

        stem = os.path.splitext(os.path.basename(job.get("filename") or job_id))[0]
        filed_at = _file_render(job_id, job["output_path"], stem,
                                job.get("target_language", "out"))
        jobs[job_id].update({"status": "done", "step": "complete",
                             "sync": check, "url": final_url,
                             "filed_at": filed_at,
                             "video_stale": False})
        _save(job_id)
        print(f"[APP] Job {job_id} re-rendered")
    except JobCancelled:
        jobs.get(job_id, {}).update(
            {"status": "cancelled", "step": "cancelled",
             "error": "Cancelled before it finished."})
    except Exception as e:                                   # noqa: BLE001
        traceback.print_exc()
        jobs.get(job_id, {}).update(
            {"status": "failed", "step": "error", "error": str(e)})


@app.get("/jobs/{job_id}/reference/candidates")
async def reference_candidates(job_id: str):
    """
    The best few windows the voice could be cloned from.

    Which window is chosen drives most of the cloning quality, and until now it
    was picked automatically with no way to disagree.
    """
    job = _require_editable(job_id)
    source = job.get("segments") or []
    if not source:
        raise HTTPException(status_code=409, detail="This run kept no transcript.")

    # candidate_windows needs word timings; the stored segments are sentence
    # level, so re-read the words from the transcript kept on the job.
    words = job.get("word_segments")
    if not words:
        raise HTTPException(
            status_code=409,
            detail="This run finished before reference picking was added. "
                   "Re-render to enable it.")

    current = job.get("reference", {})
    return JSONResponse({
        "current": {"start": current.get("start"),
                    "duration": current.get("duration"),
                    "text": current.get("text", "")},
        "candidates": reference_audio.candidate_windows(
            words, job.get("video_duration") or 0.0, limit=5),
    })


@app.post("/jobs/{job_id}/reference")
async def choose_reference(job_id: str, background_tasks: BackgroundTasks,
                           body: dict = Body(...)):
    """
    Clone from a different window and speak the whole script again.

    Unlike a phrase edit this cannot reuse the unit cache: every phrase was
    spoken in the old voice window, so all of them have to be redone.
    """
    job = _require_editable(job_id)
    if job["status"] == "processing":
        raise HTTPException(status_code=409, detail="This job is already rendering.")

    try:
        start = float(body["start"])
        duration = float(body["duration"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Pass a numeric start and duration")
    if duration < 2.0:
        raise HTTPException(status_code=400,
                            detail="A reference under two seconds will not clone well")

    job.update({"status": "processing", "step": "selecting_reference",
                "cancel_requested": False, "error": None})
    background_tasks.add_task(reclone_task, job_id, start, duration)
    return JSONResponse({"job_id": job_id, "status": "processing"})


def reclone_task(job_id: str, start: float, duration: float):
    job = jobs.get(job_id)
    if not job:
        return
    try:
        ref_path = os.path.join(job["workdir"], f"ref_{int(start * 100)}.wav")
        reference_audio.extract_reference(
            job["input_path"], start, duration, ref_path, sample_rate=24000)

        # Measure what was actually written, never what was asked for. F5
        # derives the reference length from the real waveform, so if the two
        # disagree every generated phrase comes out the wrong length and gets
        # time-stretched to compensate, which is what sounds robotic.
        actual = duration
        try:
            actual = av_sync.duration(ref_path)
        except Exception as e:                               # noqa: BLE001
            print(f"[APP] Could not probe the new reference ({e}); "
                  f"using the requested {duration:.2f}s")

        # The window's words come from the transcript already on the job rather
        # than a second ASR pass, and must describe the same span of audio.
        words = job.get("word_segments") or []
        spoken = [w["word"] for seg in words for w in (seg.get("words") or [])
                  if w["start"] >= start and w["end"] <= start + actual]
        reference = {"path": ref_path, "text": " ".join(spoken).strip(),
                     "duration": actual, "start": start}
        if not reference["text"]:
            raise RuntimeError(
                "No transcribed words fall inside that window, so there is no "
                "reference text to pair with the audio.")

        def progress(done, total):
            _set(job_id, step=f"synthesizing_voice ({done + 1}/{total})")

        with _HEAVY:
            _set(job_id, step="synthesizing_voice")
            track = dubbing.synthesize_timeline(
                job["plan"], reference["path"], reference["text"],
                reference["duration"], job["target_language"],
                job["video_duration"], progress, cache_dir=job["units_dir"])

        import soundfile as sf
        sf.write(job["dub_path"], track, dubbing.SAMPLE_RATE, subtype="PCM_16")

        jobs[job_id].update({
            "status": "done", "step": "complete",
            "reference": reference,
            "reference_path": reference["path"],
            "reference_text": reference["text"],
            "reference_seconds": round(reference["duration"], 2),
            "video_stale": True,
        })
        _save(job_id)
        print(f"[APP] Job {job_id} re-cloned from {start:.2f}s "
              f"(+{reference['duration']:.2f}s)")
    except JobCancelled:
        jobs.get(job_id, {}).update(
            {"status": "cancelled", "step": "cancelled",
             "error": "Cancelled before it finished."})
    except Exception as e:                                   # noqa: BLE001
        traceback.print_exc()
        jobs.get(job_id, {}).update(
            {"status": "failed", "step": "error", "error": str(e)})


@app.post("/jobs/{job_id}/segments/{index}/fit")
async def fit_segment(job_id: str, index: int, body: dict = Body(default={})):
    """
    Rewrite a phrase until it fits its slot when spoken.

    The pipeline's answer to an overlong line has always been to compress it,
    and compression past about 0.8x is what makes a dub sound robotic. This
    changes the words instead of the speed.

    The loop is measured, not hopeful: `natural_duration` mirrors F5's own
    internal length heuristic exactly and costs nothing to evaluate, so each
    rewrite is checked against the real target before anything is synthesized.
    """
    job = _require_editable(job_id)
    if job["status"] == "processing":
        raise HTTPException(status_code=409, detail="This job is still rendering.")
    if not llm.configured():
        raise HTTPException(
            status_code=503,
            detail="Script intelligence is off. Turn it on in Settings: run a "
                   "local model through Ollama, or add a key for a hosted "
                   "provider - a hosted one sends the script text off this "
                   "machine, unlike every other stage.")

    unit = next((u for u in job["plan"] if u["index"] == index), None)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"No phrase {index} in this timeline")

    engine = tts_engines.get_engine(job["target_language"])
    if not hasattr(engine, "natural_duration"):
        raise HTTPException(
            status_code=409,
            detail="This voice engine cannot estimate spoken length, so a fit "
                   "cannot be checked.")

    ref = job["reference"]
    lang = L.synth_lang(job["target_language"])
    slot = float(unit["duration"])
    measure = lambda t: engine.natural_duration(t, ref["path"], ref["text"], lang=lang)

    # A little headroom: landing exactly on the slot still leaves the fitter
    # shaving the tail off the last word.
    target = slot * float(body.get("headroom", 0.96))
    original = unit["text"]
    natural = measure(original)

    attempts = [{"text": original, "seconds": round(natural, 3), "source": "original"}]
    if natural <= target:
        return JSONResponse({
            "index": index, "changed": False, "text": original,
            "slot_seconds": round(slot, 3), "spoken_seconds": round(natural, 3),
            "attempts": attempts,
            "detail": "This phrase already fits its slot.",
        })

    source_text = _source_text_at(job.get("segments") or [], unit["start"], slot)
    best_text, best_seconds = original, natural

    # Each attempt rewrites the ORIGINAL, not the previous attempt. Chaining
    # compounds drift: in testing, the third link in the chain had invented a
    # clause that was never in the source. Asking harder each round instead
    # keeps every candidate one step from the truth.
    failures = []
    for attempt in range(int(body.get("tries", 3))):
        try:
            candidate = llm.shorten(
                original, L.display_name(job["target_language"]),
                (target / natural) * (0.9 ** attempt), source_text=source_text)
        except llm.Unavailable as e:
            # One bad draw is not a broken model. Asking for an aggressive
            # ratio makes an empty or unusable reply likely enough that
            # treating the first one as fatal threw away rewrites that had
            # already succeeded on an earlier, gentler attempt.
            failures.append(str(e))
            attempts.append({"text": None, "seconds": None,
                             "source": "failed", "detail": str(e)[:160]})
            continue
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        seconds = measure(candidate)
        attempts.append({"text": candidate, "seconds": round(seconds, 3),
                         "source": "rewrite"})
        if seconds < best_seconds:
            best_text, best_seconds = candidate, seconds
        if seconds <= target:
            break

    if best_text == original:
        # Nothing usable came back at all, as opposed to rewrites that came
        # back but were no shorter. The difference decides whether the user
        # should look at their model or at their line.
        if len(failures) == len(attempts) - 1:
            raise HTTPException(status_code=503, detail=failures[-1])
        return JSONResponse({
            "index": index, "changed": False, "text": original,
            "slot_seconds": round(slot, 3), "spoken_seconds": round(natural, 3),
            "attempts": attempts,
            "detail": "No rewrite came out shorter than the original."
                      + (f" {len(failures)} attempt(s) returned nothing usable."
                         if failures else ""),
        })

    unit["text"] = best_text
    try:
        with _HEAVY:
            wave = dubbing.synth_unit(unit, ref["path"], ref["text"],
                                      ref["duration"], job["target_language"],
                                      seed=unit.get("seed"))
    except Exception as e:                                   # noqa: BLE001
        unit["text"] = original
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Could not speak the rewrite: {e}")

    dubbing.write_unit(job["units_dir"], index, wave)
    _rebuild_track(job)
    project.save(job_id, job)
    print(f"[APP] Job {job_id} phrase {index} fitted "
          f"({natural:.2f}s -> {best_seconds:.2f}s for a {slot:.2f}s slot)")

    return JSONResponse({
        "index": index, "changed": True, "text": best_text,
        "slot_seconds": round(slot, 3),
        "spoken_seconds": round(best_seconds, 3),
        "was_seconds": round(natural, 3),
        "fits": best_seconds <= target,
        "attempts": attempts,
        # Words that may have been dropped rather than rephrased. Advisory:
        # a shorter synonym looks the same to a string comparison, so this is
        # for the person reading the line, not a blocking check.
        "check": llm.dropped_words(original, best_text)[:4],
        "video_stale": True,
        "can_undo": len(job.get("history", [])),
    })


EXPORTS = {
    # kind: (filename suffix, media type, builder)
    "transcript.txt": ("transcript", "text/plain",
                       lambda segs, tr: subtitles.plain(segs)),
    "translation.txt": ("translation", "text/plain",
                        lambda segs, tr: subtitles.plain(segs, tr)),
    "source.srt": ("source", "application/x-subrip",
                   lambda segs, tr: subtitles.srt(segs)),
    "source.vtt": ("source", "text/vtt",
                   lambda segs, tr: subtitles.vtt(segs)),
    "dub.srt": ("dub", "application/x-subrip",
                lambda segs, tr: subtitles.srt(segs, tr)),
    "dub.vtt": ("dub", "text/vtt",
                lambda segs, tr: subtitles.vtt(segs, tr)),
}


@app.get("/jobs/{job_id}/export/{kind}")
async def export_job_text(job_id: str, kind: str):
    """
    Transcript and subtitle files for a finished job.

    Everything served here is formatting over data the run already produced:
    Whisper's segment timings and the translation aligned to them. No model is
    invoked and nothing is recomputed.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    spec = EXPORTS.get(kind)
    if not spec:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown export. Available: {', '.join(sorted(EXPORTS))}")

    segments = job.get("segments")
    if not segments:
        raise HTTPException(
            status_code=409,
            detail="This job has no stored transcript. Only runs finished "
                   "since transcript export was added can be exported.")

    suffix, media_type, build = spec
    translated = job.get("translated_segments") or []
    if "dub" in kind or "translation" in kind:
        if not translated:
            raise HTTPException(status_code=409,
                                detail="This job has no stored translation.")

    body = build(segments, translated)
    ext = kind.rsplit(".", 1)[-1]
    name = f"{job_id[:8]}_{suffix}.{ext}"
    return Response(
        content=body,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/boot")
async def boot_status():
    """What the engine is loading, for the splash screen to report."""
    return JSONResponse(BOOT)


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """
    Ask a running job to stop.

    Cooperative: the flag is checked at every stage report, and the two long
    stages report per segment and per frame batch, so this lands within
    seconds. A job that has already finished is left alone.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] in ("done", "failed", "cancelled"):
        return JSONResponse({"status": job["status"], "cancelled": False})
    job["cancel_requested"] = True
    return JSONResponse({"status": "cancelling", "cancelled": True})


@app.post("/voiceover/")
async def create_voiceover(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    script: str = Form(..., description="What the voice should say"),
    language: str = Query(..., description="Language of the script"),
    user_id: str = Query("anonymous"),
    name: str = Query(None, description="What to call this project"),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No reference clip provided")
    script = (script or "").strip()
    if not script:
        raise HTTPException(status_code=400, detail="The script is empty")
    if len(script) > 20000:
        raise HTTPException(status_code=400, detail="Script is too long (max 20000 characters)")

    try:
        language = L.normalize(language)
        L.engine_for(language)
    except L.UnsupportedLanguage as e:
        raise HTTPException(status_code=400, detail=str(e))

    content = await file.read()
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Reference clip too large (max 200MB)")
    if len(content) < 1000:
        raise HTTPException(status_code=400, detail="Reference clip too small or empty")

    job_id = str(uuid.uuid4())[:12]
    safe_filename = f"{job_id}_{_safe_name(file.filename)}"
    input_path = os.path.join(UPLOAD_DIR, safe_filename)
    with open(input_path, "wb") as f:
        f.write(content)

    jobs[job_id] = {
        "status": "queued", "step": "uploaded", "kind": "voiceover",
        "filename": file.filename, "target_language": language,
        "title": (name or "").strip() or None,
        "user_id": user_id,
        "created_at": asyncio.get_event_loop().time(),
    }
    background_tasks.add_task(voiceover_task, job_id, input_path, script,
                             language, safe_filename, user_id)
    return JSONResponse({"job_id": job_id, "status": "queued"})


@app.get("/api/settings/advanced")
async def get_advanced_settings():
    """
    The pipeline's knobs, and which of them apply to the chosen engines.

    A setting for a model nobody is using is noise, so each one says whether
    it is relevant rather than leaving the studio to guess.
    """
    return JSONResponse({
        "tunables": engines.tunables_state(),
        "workdir": UPLOAD_DIR,
        "workdir_bytes": _dir_bytes(UPLOAD_DIR),
        "output_dir": engines.output_dir(create=False),
    })


@app.post("/api/settings/advanced")
async def set_advanced_settings(body: dict = Body(...)):
    """Store knob values. An environment variable still wins over these."""
    try:
        state = engines.save_tunables(body or {})
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not save: {e}")
    return JSONResponse({"tunables": state})


@app.post("/api/settings/sweep")
async def sweep_now():
    """Clear abandoned working folders on request rather than on the timer."""
    freed = sweep_workdirs()
    return JSONResponse({"removed": freed, "workdir_bytes": _dir_bytes(UPLOAD_DIR)})


def _dir_bytes(path: str) -> int:
    """How much a directory is holding. Best effort: a file that vanishes
    mid-walk is not worth failing a settings page over."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


@app.get("/api/models")
async def list_models():
    """
    Every model, its size, and whether it is already on this machine.

    Downloading several gigabytes should not require opening a terminal, so
    the studio drives the same downloader the command line does.
    """
    rows = models.inventory()
    return JSONResponse({
        "models": rows,
        "missing": sum(1 for r in rows if not r["present"]),
        "cache": models.cache_root(),
        "free_gb": round(models.free_space_gb(), 1),
        "progress": models.status(),
    })


@app.post("/api/models/download")
async def download_models(body: dict = Body(default={})):
    """
    Fetch the named models, or everything missing.

    Returns immediately; watch /api/models/progress. Refused while a run is
    already going, because two downloads writing one .part file would corrupt
    each other.
    """
    ids = body.get("ids") or None
    if ids is not None and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    if not models.run(ids):
        raise HTTPException(status_code=409,
                            detail="A download is already running.")
    return JSONResponse(models.status())


@app.get("/api/models/progress")
async def model_progress():
    """Which file, how far through, and whether anything went wrong."""
    return JSONResponse(models.status())


@app.post("/api/models/cancel")
async def cancel_models():
    """Stop after the file in flight. Partial files resume on the next run."""
    models.cancel()
    return JSONResponse(models.status())


@app.get("/api/settings/engines")
async def get_engine_settings():
    """
    Which model runs each stage, and which of them are already downloaded.

    `configured` is how the app knows whether to show setup on launch.
    """
    return JSONResponse({
        "configured": engines.configured(),
        "stages": engines.catalog(),
        "output_dir": engines.output_dir(create=False),
        "default_output_dir": engines.default_output_dir(),
    })


@app.post("/api/settings/engines")
async def set_engine_settings(body: dict = Body(...)):
    """
    Store stage choices.

    These are read at import by the module that owns each stage and cached for
    the life of the process, so the response says plainly that a restart is
    needed rather than pretending the change is live.
    """
    try:
        chosen = engines.save(body or {})
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not save settings: {e}")
    return JSONResponse({
        "configured": True,
        "chosen": chosen,
        # Only the stage models need a restart; the output folder takes effect
        # on the next render.
        "restart_required": any(k in (body or {}) for k in engines.CATALOG),
        "stages": engines.catalog(),
        "output_dir": engines.output_dir(create=False),
        "default_output_dir": engines.default_output_dir(),
    })


@app.get("/api/settings/llm")
async def get_llm_settings():
    """
    Which model does the script rewriting, and whether it can be reached.

    Never returns an API key, only whether one is stored.
    """
    return JSONResponse(llm.status())


@app.post("/api/settings/llm")
async def set_llm_settings(body: dict = Body(...)):
    """
    Choose a provider and model, and optionally store a key for it.

    Omitting `api_key` leaves any stored key untouched, so changing the model
    does not silently log you out; sending an empty one deletes it.
    """
    provider = (body.get("provider") or "").strip()
    if provider not in llm.PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider. Choose one of: {', '.join(llm.PROVIDERS)}")
    try:
        llm.save_settings(
            provider,
            (body.get("model") or "").strip(),
            key=body.get("api_key"),
            ollama_host=(body.get("ollama_host") or "").strip() or None,
            custom_url=body.get("custom_url"),
        )
    except OSError as e:
        raise HTTPException(status_code=500,
                            detail=f"Could not save settings: {e}")
    return JSONResponse(llm.status())


@app.post("/api/settings/llm/test")
async def test_llm_settings():
    """
    Prove the current setting works, before someone relies on it mid-render.

    Deliberately a real completion rather than a reachability ping: a key can
    be valid and the model name wrong, and that only shows up on a real call.
    """
    try:
        reply = llm.complete(
            "Reply with exactly the word: ok",
            "Say ok.", max_tokens=16)
    except llm.Unavailable as e:
        raise HTTPException(status_code=502, detail=str(e))
    return JSONResponse({"ok": True, "reply": llm.clean_line(reply)[:80],
                         **llm.status()})


@app.get("/languages")
async def get_languages():
    """Supported dubbing targets, Indian languages first."""
    return JSONResponse(L.supported_targets())


@app.get("/api/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "projects": len([j for j in jobs.values() if j.get("status") == "done"]),
        "active_jobs": sum(1 for j in jobs.values() if j["status"] == "processing"),
        # Off unless a key is configured. It is the only stage that sends text
        # off this machine, so the client says so before using it.
        "script_intelligence": llm.configured(),
        "llm": {k: v for k, v in llm.status().items()
                if k in ("provider", "model", "local", "has_key")},
    })


def _project_name(job: dict, job_id: str) -> str:
    """What to call this project: what the user named it, else its source
    file, else the id. The id is the last resort, not the label."""
    name = (job.get("title") or "").strip()
    if name:
        return name
    return job.get("filename") or job_id


@app.patch("/jobs/{job_id}")
async def rename_project(job_id: str, body: dict = Body(...)):
    """Rename a project."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No such job")
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="A project needs a name")
    if len(name) > 120:
        raise HTTPException(status_code=400, detail="That name is too long")
    job["title"] = name
    project.save(job_id, job)
    return JSONResponse({"job_id": job_id, "name": name})


@app.get("/videos/")
async def get_videos(user_id: str = "anonymous"):
    """
    Every project this user has on this machine.

    The manifest is the only record. There is no remote index to fall out of
    sync with, and no outage that can hide someone's work.
    """
    rows = []
    for job_id, job in jobs.items():
        if job.get("user_id") != user_id or job.get("status") != "done":
            continue
        created = job.get("saved_at")
        rows.append({
            "id": job_id,
            "job_id": job_id,
            "openable": True,
            "title": _project_name(job, job_id),
            "name": _project_name(job, job_id),
            "original_language": job.get("source_language"),
            "target_language": job.get("target_language"),
            "duration": job.get("duration"),
            "processing_status": "done",
            "dubbed_url": job.get("url"),
            "created_at": (datetime.fromtimestamp(created, timezone.utc).isoformat()
                           if created else None),
        })
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return JSONResponse(rows)


@app.delete("/videos/{job_id}")
async def delete_project(job_id: str):
    """
    Delete a project and everything it produced.

    Removes the whole working folder: source, reference, phrase cache, dub,
    render and manifest. Irreversible, which is why the studio asks twice.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Project not found")
    if job.get("status") == "processing":
        raise HTTPException(
            status_code=409,
            detail="This project is still rendering. Cancel it first, then "
                   "delete it.")
    jobs.pop(job_id, None)

    uploads = os.path.abspath(UPLOAD_DIR) + os.sep

    # Refuse to touch anything outside the uploads directory, so a corrupted
    # manifest cannot aim this at somewhere else on the disk.
    workdir = job.get("workdir")
    if workdir and os.path.isdir(workdir) and os.path.abspath(workdir).startswith(uploads):
        try:
            shutil.rmtree(workdir)
        except OSError as e:
            raise HTTPException(status_code=500,
                                detail=f"Could not delete the project files: {e}")

    source = job.get("input_path")
    if source and os.path.isfile(source) and os.path.abspath(source).startswith(uploads):
        try:
            os.remove(source)
        except OSError:
            pass

    print(f"[APP] Deleted project {job_id}")
    return JSONResponse({"status": "deleted", "job_id": job_id})
