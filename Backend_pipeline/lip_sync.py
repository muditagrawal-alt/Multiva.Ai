"""
Warm, in-process Wav2Lip runner.

Replaces the old `lip_sync_generate.generate_lip_synced_video`, which shelled out
to Wav2Lip/inference.py once per request. That meant reloading torch, the s3fd
detector (90 MB) and wav2lip_gan (436 MB) on every single job, and it inherited
three behaviours from the stock script that broke this pipeline:

  * `full_frames = full_frames[:len(mel_chunks)]` plus `idx = i % len(frames)`
    make the OUTPUT length follow the AUDIO. Audio longer than the video made
    the picture visibly loop back to the start; shorter cut it off mid-shot.
    Here the frame count is authoritative and mel chunks are cut to match, so
    the picture is never looped or truncated.
  * a frame with no detected face aborted the run; real footage has plenty
    (motion blur, profile turns, cutaways). We carry the last good box forward.
  * output went through cv2's mp4v writer and was then re-encoded by ffmpeg —
    two lossy generations before the pipeline's other three. We pipe raw frames
    straight into a single ffmpeg encode.

Detection runs on downscaled frames and composites onto the full-resolution
original, so quality stays HD while the expensive part sees far fewer pixels.
"""

import importlib.util
import os
import subprocess
import sys
import threading

import cv2
import numpy as np

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WAV2LIP_DIR = os.path.join(THIS_DIR, "Wav2Lip")
import engines

# Only checkpoints actually present on disk are offered by the catalogue,
# so a stored choice that has since been deleted falls back rather than
# failing at render time.
_CKPT = engines.get("lipsync") or "wav2lip_gan.pth"
CHECKPOINT = os.path.join(WAV2LIP_DIR, "checkpoints", _CKPT)
if not os.path.exists(CHECKPOINT):
    CHECKPOINT = os.path.join(WAV2LIP_DIR, "checkpoints", "wav2lip_gan.pth")

IMG_SIZE = 96
MEL_STEP_SIZE = 16

# Wav2Lip only ever sees a 96x96 crop, so detecting on a full-resolution frame
# is wasted work. Detection runs at this height and the boxes are scaled back up.
# Measured on an M4 with s3fd (ms per frame):
#     height 478  ->  mps 238   cpu 877
#     height 256  ->  mps  67   cpu 139
# All four settings found a face in 32/32 frames, so the cheap one costs nothing
# in recall. 256-on-MPS is ~13x faster than the 478-on-CPU it replaces.
DETECT_HEIGHT = 256

# A face moves slowly relative to frame rate, so detecting on every frame is
# redundant. We detect every Nth frame and interpolate between.
DETECT_EVERY = 3

# Temporal smoothing window for face boxes (stock Wav2Lip uses 5).
SMOOTH_WINDOW = 5


def _load_module(name: str, path: str):
    """Import a Wav2Lip file under a private name so it can't clash."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _pick_device() -> str:
    forced = os.getenv("FORCE_DEVICE")
    if forced:
        return forced
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class LipSyncError(RuntimeError):
    pass


class Wav2LipRunner:
    """Holds the detector and the generator warm across requests."""

    def __init__(self):
        self._model = None
        self._detector = None
        self._audio_mod = None
        self._lock = threading.Lock()
        self.device = None

    # -- loading ------------------------------------------------------------
    def _ensure_path(self):
        if WAV2LIP_DIR not in sys.path:
            sys.path.insert(0, WAV2LIP_DIR)

    def load(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            if not os.path.exists(CHECKPOINT):
                raise LipSyncError(f"Wav2Lip checkpoint missing: {CHECKPOINT}")

            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
            import torch

            self._ensure_path()
            self.device = _pick_device()
            print(f"[Wav2Lip] Loading models on {self.device}...")

            self._audio_mod = _load_module(
                "_w2l_audio", os.path.join(WAV2LIP_DIR, "audio.py"))
            models_mod = _load_module(
                "_w2l_models", os.path.join(WAV2LIP_DIR, "models", "__init__.py"))
            import face_detection

            model = models_mod.Wav2Lip()
            ckpt = torch.load(CHECKPOINT, map_location=self.device)
            state = {k.replace("module.", ""): v
                     for k, v in ckpt["state_dict"].items()}
            model.load_state_dict(state)
            self._model = model.to(self.device).eval()

            # s3fd runs correctly on MPS and is ~3.7x faster there than on CPU.
            det_device = self.device
            try:
                self._detector = face_detection.FaceAlignment(
                    face_detection.LandmarksType._2D,
                    flip_input=False, device=det_device)
            except Exception as e:
                print(f"[Wav2Lip] Detector failed on {det_device} ({e}); using CPU")
                det_device = "cpu"
                self._detector = face_detection.FaceAlignment(
                    face_detection.LandmarksType._2D,
                    flip_input=False, device=det_device)

            print(f"[Wav2Lip] Ready (generator={self.device}, detector={det_device})")

    # -- face detection -----------------------------------------------------
    def _detect_faces(self, frames: list, pads: tuple, batch_size: int,
                      detect_every: int = DETECT_EVERY) -> list:
        """
        Return one (y1, y2, x1, x2) box per frame, smoothed over time, with
        undetected frames inheriting the last good box.

        Only every `detect_every`-th frame actually goes through s3fd; the rest
        are linearly interpolated. A face does not travel far in three frames,
        and the result is temporally smoothed afterwards regardless.
        """
        h, w = frames[0].shape[:2]
        scale = min(1.0, DETECT_HEIGHT / float(h))

        step = max(1, int(detect_every))
        sampled_idx = list(range(0, len(frames), step))
        if sampled_idx[-1] != len(frames) - 1:
            sampled_idx.append(len(frames) - 1)

        small = [cv2.resize(frames[i], (int(w * scale), int(h * scale)))
                 if scale < 1.0 else frames[i] for i in sampled_idx]

        raw = []
        for i in range(0, len(small), batch_size):
            batch = np.array(small[i:i + batch_size])
            try:
                raw.extend(self._detector.get_detections_for_batch(batch))
            except RuntimeError as e:
                if batch_size == 1:
                    raise LipSyncError(f"Face detection failed: {e}") from e
                # Out of memory — retry this batch one frame at a time.
                for f in small[i:i + batch_size]:
                    raw.extend(self._detector.get_detections_for_batch(np.array([f])))

        pt, pb, pl, pr = pads
        sparse, last = [], None
        missing = 0

        for rect in raw:
            if rect is None:
                missing += 1
                sparse.append(last)
                continue
            x1, y1, x2, y2 = [int(v / scale) for v in rect]
            y1 = max(0, y1 - pt)
            y2 = min(h, y2 + pb)
            x1 = max(0, x1 - pl)
            x2 = min(w, x2 + pr)
            last = (y1, y2, x1, x2)
            sparse.append(last)

        if all(b is None for b in sparse):
            raise LipSyncError(
                "No face detected anywhere in the video. Wav2Lip needs a "
                "visible face; check framing, lighting, or crop the shot.")

        # Backfill any leading samples taken before the first good box.
        first = next(b for b in sparse if b is not None)
        sparse = [b if b is not None else first for b in sparse]

        if missing:
            print(f"[Wav2Lip] {missing}/{len(sparse)} sampled frames had no "
                  f"detection; carried the neighbouring box forward")

        boxes = self._interpolate(sampled_idx, sparse, len(frames))
        print(f"[Wav2Lip] Detected on {len(sampled_idx)}/{len(frames)} frames "
              f"at {int(h * scale)}p")
        return self._smooth(boxes, SMOOTH_WINDOW)

    @staticmethod
    def _interpolate(idx: list, sparse: list, n_frames: int) -> list:
        """Linearly interpolate boxes for the frames we did not detect on."""
        if len(idx) >= n_frames:
            return list(sparse[:n_frames])
        arr = np.array(sparse, dtype=np.float32)
        xs = np.array(idx, dtype=np.float32)
        target = np.arange(n_frames, dtype=np.float32)
        out = np.stack([np.interp(target, xs, arr[:, c]) for c in range(4)], axis=1)
        return [tuple(int(v) for v in row) for row in out]

    @staticmethod
    def _smooth(boxes: list, window: int) -> list:
        if window <= 1 or len(boxes) < window:
            return boxes
        arr = np.array(boxes, dtype=np.float32)
        out = np.copy(arr)
        for i in range(len(arr)):
            lo = max(0, i - window // 2)
            hi = min(len(arr), lo + window)
            out[i] = arr[lo:hi].mean(axis=0)
        return [tuple(int(v) for v in b) for b in out]

    # -- audio --------------------------------------------------------------
    def _mel_chunks(self, wav_path: str, n_frames: int, fps: float) -> list:
        """
        One mel chunk per VIDEO frame. The frame count is authoritative here —
        that is the whole point — so a dub that is a few milliseconds short
        repeats its final chunk rather than shortening the picture.
        """
        wav = self._audio_mod.load_wav(wav_path, 16000)
        mel = self._audio_mod.melspectrogram(wav)

        if np.isnan(mel.reshape(-1)).sum() > 0:
            raise LipSyncError(
                "Mel spectrogram contains NaN — the dubbed audio is silent or "
                "corrupt at some point.")

        step = 80.0 / fps
        chunks, total = [], mel.shape[1]
        for i in range(n_frames):
            start = int(i * step)
            if start + MEL_STEP_SIZE > total:
                chunks.append(mel[:, max(0, total - MEL_STEP_SIZE):])
            else:
                chunks.append(mel[:, start:start + MEL_STEP_SIZE])
        return chunks

    # -- main ---------------------------------------------------------------
    def run(self, video_path: str, audio_path: str, out_path: str,
            pads=(0, 12, 0, 0), wav2lip_batch_size: int = 64,
            face_det_batch_size: int = 16, detect_every: int = DETECT_EVERY,
            crf: int = 18, preset: str = "medium", feather: int = 10,
            progress=None) -> str:
        import torch

        self.load()

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise LipSyncError(f"Could not open {video_path}")
        cv_fps = cap.get(cv2.CAP_PROP_FPS)
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
        cap.release()

        if not frames:
            raise LipSyncError(f"No frames decoded from {video_path}")

        fps = _resolve_fps(video_path, len(frames), cv_fps)

        h, w = frames[0].shape[:2]
        print(f"[Wav2Lip] {len(frames)} frames @ {fps:.3f}fps ({w}x{h})")

        mel_chunks = self._mel_chunks(audio_path, len(frames), fps)
        boxes = self._detect_faces(frames, pads, face_det_batch_size,
                                   detect_every=detect_every)

        proc = _open_writer(out_path, w, h, fps, audio_path, crf, preset)
        blend = _edge_mask(IMG_SIZE, feather)

        try:
            for i in range(0, len(frames), wav2lip_batch_size):
                sl = slice(i, i + wav2lip_batch_size)
                batch_frames = frames[sl]
                batch_boxes = boxes[sl]
                batch_mels = mel_chunks[sl]

                faces = []
                for frame, (y1, y2, x1, x2) in zip(batch_frames, batch_boxes):
                    crop = frame[y1:y2, x1:x2]
                    if crop.size == 0:
                        crop = frame
                    faces.append(cv2.resize(crop, (IMG_SIZE, IMG_SIZE)))

                img_batch = np.asarray(faces, dtype=np.uint8)
                masked = img_batch.copy()
                masked[:, IMG_SIZE // 2:] = 0
                net_in = np.concatenate((masked, img_batch), axis=3) / 255.0
                mel_in = np.asarray(batch_mels)[..., np.newaxis]

                t_img = torch.FloatTensor(np.transpose(net_in, (0, 3, 1, 2))).to(self.device)
                t_mel = torch.FloatTensor(np.transpose(mel_in, (0, 3, 1, 2))).to(self.device)

                with torch.no_grad():
                    pred = self._model(t_mel, t_img)

                pred = pred.cpu().numpy().transpose(0, 2, 3, 1) * 255.0

                for p, frame, (y1, y2, x1, x2) in zip(pred, batch_frames, batch_boxes):
                    bw, bh = x2 - x1, y2 - y1
                    if bw <= 0 or bh <= 0:
                        proc.stdin.write(frame.tobytes())
                        continue
                    mouth = cv2.resize(p.astype(np.uint8), (bw, bh))
                    mask = cv2.resize(blend, (bw, bh))[..., None]
                    region = frame[y1:y2, x1:x2].astype(np.float32)
                    frame[y1:y2, x1:x2] = (
                        mouth.astype(np.float32) * mask + region * (1.0 - mask)
                    ).astype(np.uint8)
                    proc.stdin.write(frame.tobytes())

                if progress:
                    progress(min(i + wav2lip_batch_size, len(frames)), len(frames))

            proc.stdin.close()
            if proc.wait() != 0:
                err = proc.stderr.read().decode("utf-8", "replace")[-800:]
                raise LipSyncError(f"ffmpeg encode failed:\n{err}")
        finally:
            if proc.poll() is None:
                proc.kill()

        print(f"[Wav2Lip] Wrote {out_path}")
        return out_path


def _resolve_fps(video_path: str, n_frames: int, cv_fps: float) -> float:
    """
    Decide the frame rate to encode the output at.

    cv2's CAP_PROP_FPS cannot be trusted. For variable-frame-rate footage —
    phone recordings, screen captures, WhatsApp re-encodes, anything from a
    browser MediaRecorder — it reports an average, or occasionally a nonsense
    value like 90000 (the container timebase). Passing that straight to ffmpeg's
    `-r` makes the encoded picture a completely different LENGTH from the dub:
    the audio then runs on long after the video has finished, which is exactly
    the "audio isn't stitched to the video" symptom.

    `dubbing` builds the dub to equal the container duration, so the frame rate
    that makes the picture come out the same length is simply

        frames / container duration

    Prefer that; fall back to cv2 only when the container duration is unusable.
    """
    probed = None
    try:
        import av_sync
        probed = av_sync.probe(video_path)["duration"]
    except Exception as e:
        print(f"[Wav2Lip] Could not probe duration ({e}); trusting cv2 fps")

    derived = (n_frames / probed) if probed and probed > 0.05 else None

    def sane(v):
        return bool(v) and 1.0 <= float(v) <= 240.0

    if sane(derived):
        if sane(cv_fps) and abs(derived - cv_fps) / derived > 0.01:
            print(f"[Wav2Lip] cv2 reports {cv_fps:.3f}fps but {n_frames} frames "
                  f"over {probed:.3f}s is {derived:.3f}fps — using {derived:.3f} "
                  f"so the picture matches the dub's length")
        return float(derived)

    if sane(cv_fps):
        print(f"[Wav2Lip] Container duration unusable; falling back to cv2 "
              f"fps {cv_fps:.3f}")
        return float(cv_fps)

    print(f"[Wav2Lip] No usable frame rate (cv2={cv_fps!r}, derived={derived!r}); "
          f"defaulting to 25fps")
    return 25.0


def _edge_mask(size: int, feather: int) -> np.ndarray:
    """
    Alpha mask selecting only the MOUTH region of the generated face.

    Wav2Lip is conditioned on a face whose lower half is blanked, and only ever
    predicts that lower half — the top half of its output is a reconstruction of
    input it was already given. Compositing the whole 96x96 prediction back over
    the full face box therefore replaced sharp original pixels (eyes, nose,
    brows, cheeks) with an upscaled reconstruction of themselves, visibly
    softening the entire face to fix a mouth.

    This keeps the original frame above the mouth line and cross-fades into the
    generated pixels below it, so only what actually needed to change does.
    """
    m = np.zeros((size, size), dtype=np.float32)

    # Wav2Lip blanks from size//2 down; start the blend just above that so the
    # transition sits on the cheek rather than across the lips.
    mouth_top = int(size * 0.44)
    ramp_rows = max(2, int(size * 0.12))

    m[mouth_top + ramp_rows:, :] = 1.0
    ramp = np.linspace(0.0, 1.0, ramp_rows, dtype=np.float32)
    m[mouth_top:mouth_top + ramp_rows, :] = ramp[:, None]

    # Feather the remaining three edges so the patch has no hard border.
    f = max(0, min(feather, size // 4))
    if f:
        edge = np.linspace(0.0, 1.0, f, dtype=np.float32)
        m[-f:, :] *= edge[::-1, None]
        m[:, :f] *= edge[None, :]
        m[:, -f:] *= edge[::-1][None, :]
    return m


def _open_writer(out_path: str, w: int, h: int, fps: float, audio_path: str,
                 crf: int, preset: str):
    """Pipe raw BGR frames into ffmpeg, muxing the dub in the same pass."""
    cmd = [
        # -nostats/-loglevel error: we hold stderr open as a pipe and only read
        # it after the process exits. ffmpeg's default per-frame progress lines
        # would fill the 64KB pipe buffer on a long video, at which point ffmpeg
        # blocks writing stderr, stops draining stdin, and our frame writes block
        # too — a deadlock that looks like the render simply hanging.
        "ffmpeg", "-y", "-nostats", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{w}x{h}", "-pix_fmt", "bgr24", "-r", f"{fps}",
        "-i", "-",
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        # The default 4:2:0 chroma subsampling is what makes lips and teeth look
        # mushy after a re-encode. yuv420p is kept for player compatibility, but
        # tune=film and a higher-quality preset preserve fine facial detail that
        # -preset veryfast at CRF 20 was throwing away (measured: source 858
        # kbps in, 450 kbps out).
        "-tune", "film",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


_RUNNER = Wav2LipRunner()


def generate_lip_synced_video(video_path: str, audio_path: str, out_path: str,
                              **kwargs) -> str:
    return _RUNNER.run(video_path, audio_path, out_path, **kwargs)


def warmup() -> bool:
    try:
        _RUNNER.load()
        return True
    except Exception as e:
        print(f"[Wav2Lip] Warmup failed: {e}")
        return False
