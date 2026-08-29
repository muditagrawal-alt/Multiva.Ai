"""
Evaluation harness for the dubbing pipeline.

Why this exists
---------------
Every quality failure in this project had the same shape: something looked fine
by the metric that existed and was broken by the metric that did not.

  * A completely untrained model emitted digital silence of exactly the right
    length. Every duration and sync check passed.
  * A 0.40x phase-vocoder squeeze turned speech into a robotic warble. Duration
    checks still passed.
  * Per-unit compression ranged 0.85x-1.76x while the aggregate read 1.15x, so
    individual phrases were unintelligible and the summary looked healthy.

So this measures CONTENT, not just shape, and reports per-item numbers rather
than only aggregates.

Metrics
-------
speaker      WavLM x-vector cosine between the reference clip and the dub.
             Reported against a per-clip ceiling (reference vs the speaker's own
             source audio) and floor (reference vs a different speaker), because
             a bare cosine from this model sits around 0.9x for everything and
             is meaningless on its own. `speaker_norm` rescales to 0..1 between
             those two anchors — that is the number to track.

intelligible Re-transcribes the dub and compares against the text the pipeline
             intended to say. This is the closest proxy for "can a listener make
             out what it is saying", which is the complaint no other metric here
             would catch. CER is primary for Indian scripts; WER is noisy when
             the ASR itself misspells.

timing       Sync delta (video vs audio duration), speech-time ratio (does the
             dub speak for as long as the source did), and pause structure —
             count and total duration of 50-300ms pauses, the ones that carry
             speech rhythm.

pitch        Median F0 of source vs dub. A large shift means the wrong speaker
             identity even when the x-vector looks acceptable.

Usage
-----
    ../venv/bin/python eval_harness.py --all
    ../venv/bin/python eval_harness.py --run gradio_runs/<id>
    ../venv/bin/python eval_harness.py --all --json results.json

A run directory needs input.mp4, reference.wav and dub.wav. `meta.json` (written
by gradio_app) adds the intended text, without which intelligibility is skipped.
"""

import argparse
import glob
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

SV_MODEL = "microsoft/wavlm-base-plus-sv"

# Different-speaker clips used as the negative control. These ship with the
# vendored IndicF5 checkout.
NEGATIVE_GLOB = os.path.join(THIS_DIR, "IndicF5", "prompts", "*.wav")

# Pauses in this band carry speech rhythm; longer ones are structural breaks.
MICRO_PAUSE = (0.05, 0.30)
SILENCE_DB = -35.0


# ---------------------------------------------------------------------------
# Speaker similarity
# ---------------------------------------------------------------------------
class SpeakerScorer:
    def __init__(self):
        self._fe = None
        self._model = None
        self._cache = {}

    def _load(self):
        if self._model is None:
            import torch
            from transformers import AutoFeatureExtractor, WavLMForXVector
            print(f"[EVAL] Loading {SV_MODEL}...")
            self._fe = AutoFeatureExtractor.from_pretrained(SV_MODEL)
            self._model = WavLMForXVector.from_pretrained(SV_MODEL).eval()
        return self._fe, self._model

    def embed(self, path: str):
        if path in self._cache:
            return self._cache[path]
        import torch
        import librosa
        fe, model = self._load()
        y, _ = librosa.load(path, sr=16000)
        if y.size < 1600:
            return None
        inputs = fe(y, sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            e = model(**inputs).embeddings
        e = torch.nn.functional.normalize(e, dim=-1)[0]
        self._cache[path] = e
        return e

    @staticmethod
    def cosine(a, b) -> float:
        import torch
        if a is None or b is None:
            return float("nan")
        return float(torch.dot(a, b))

    def floor(self, ref_path: str) -> float:
        """
        Similarity between the reference and unrelated speakers — the score a
        failed clone would land near. Without this anchor a cosine of 0.97 is
        not interpretable.
        """
        others = sorted(glob.glob(NEGATIVE_GLOB))[:4]
        ref = self.embed(ref_path)
        scores = [self.cosine(ref, self.embed(p)) for p in others]
        scores = [s for s in scores if not np.isnan(s)]
        return float(np.mean(scores)) if scores else float("nan")


# ---------------------------------------------------------------------------
# Text distance
# ---------------------------------------------------------------------------
def _levenshtein(a: list, b: list) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _normalize(text: str) -> str:
    import re
    text = (text or "").strip().lower()
    text = re.sub(r"[.,!?;:।\"'()\[\]—–-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def error_rates(reference: str, hypothesis: str) -> dict:
    ref, hyp = _normalize(reference), _normalize(hypothesis)
    if not ref:
        return {"wer": float("nan"), "cer": float("nan")}
    rw, hw = ref.split(), hyp.split()
    rc, hc = list(ref.replace(" ", "")), list(hyp.replace(" ", ""))
    return {
        "wer": _levenshtein(rw, hw) / max(1, len(rw)),
        "cer": _levenshtein(rc, hc) / max(1, len(rc)),
    }


# ---------------------------------------------------------------------------
# Signal metrics
# ---------------------------------------------------------------------------
def signal_stats(path: str) -> dict:
    import librosa
    y, sr = librosa.load(path, sr=16000)
    if y.size == 0:
        return {}

    hop = 160
    rms = librosa.feature.rms(y=y, frame_length=320, hop_length=hop)[0]
    peak = float(np.max(np.abs(y)))
    if rms.max() <= 0:
        return {"duration": len(y) / sr, "peak": peak, "voiced_ratio": 0.0,
                "speech_seconds": 0.0, "micro_pauses": 0,
                "micro_pause_seconds": 0.0, "f0_median": 0.0, "flatness": 0.0}

    voiced = librosa.amplitude_to_db(rms, ref=np.max) > SILENCE_DB

    runs, cur = [], 0
    for v in voiced:
        if not v:
            cur += 1
        elif cur:
            runs.append(cur * hop / sr)
            cur = 0
    if cur:
        runs.append(cur * hop / sr)
    runs = np.array(runs) if runs else np.array([0.0])
    micro = runs[(runs >= MICRO_PAUSE[0]) & (runs < MICRO_PAUSE[1])]

    S = np.abs(librosa.stft(y))
    flat = librosa.feature.spectral_flatness(S=S)[0]
    frame_rms = librosa.feature.rms(S=S)[0]
    loud = flat[frame_rms > frame_rms.max() * 0.05]

    f0 = librosa.yin(y, fmin=60, fmax=400, sr=sr)
    vf = f0[(f0 > 65) & (f0 < 380)]

    return {
        "duration": len(y) / sr,
        "peak": peak,
        "voiced_ratio": float(voiced.mean()),
        "speech_seconds": float(voiced.mean() * len(y) / sr),
        "micro_pauses": int(len(micro)),
        "micro_pause_seconds": float(micro.sum()),
        # Measured over loud frames only: digital-silence frames read as
        # maximally flat and would swamp the average.
        "flatness": float(loud.mean()) if loud.size else float("nan"),
        "f0_median": float(np.median(vf)) if vf.size else 0.0,
    }


# ---------------------------------------------------------------------------
# Per-run evaluation
# ---------------------------------------------------------------------------
def evaluate_run(run_dir: str, scorer: SpeakerScorer,
                 transcribe=None) -> dict:
    import av_sync

    name = os.path.basename(run_dir.rstrip("/"))
    out = {"run": name, "dir": run_dir}

    src_video = next((p for p in glob.glob(os.path.join(run_dir, "input.*"))
                      if not p.endswith(".json")), None)
    ref_wav = os.path.join(run_dir, "reference.wav")
    dub_wav = os.path.join(run_dir, "dub.wav")
    stt_wav = os.path.join(run_dir, "stt.wav")

    if not (os.path.exists(ref_wav) and os.path.exists(dub_wav)):
        out["error"] = "missing reference.wav or dub.wav"
        return out

    # Source audio for comparison: prefer the already-extracted stt.wav.
    source_audio = stt_wav if os.path.exists(stt_wav) else None
    if source_audio is None and src_video:
        source_audio = av_sync.extract_audio(
            src_video, os.path.join(run_dir, "_eval_src.wav"))

    # ---- speaker similarity ----
    ref_e = scorer.embed(ref_wav)
    dub_e = scorer.embed(dub_wav)
    sim = scorer.cosine(ref_e, dub_e)
    ceiling = (scorer.cosine(ref_e, scorer.embed(source_audio))
               if source_audio else float("nan"))
    floor = scorer.floor(ref_wav)

    out["speaker_similarity"] = sim
    out["speaker_ceiling"] = ceiling
    out["speaker_floor"] = floor
    if not (np.isnan(sim) or np.isnan(ceiling) or np.isnan(floor)) and ceiling > floor:
        out["speaker_norm"] = (sim - floor) / (ceiling - floor)
    else:
        out["speaker_norm"] = float("nan")

    # ---- signal / timing ----
    dub_stats = signal_stats(dub_wav)
    out["dub"] = dub_stats
    if source_audio:
        src_stats = signal_stats(source_audio)
        out["source"] = src_stats
        if src_stats.get("speech_seconds"):
            out["speech_time_ratio"] = (dub_stats["speech_seconds"]
                                        / src_stats["speech_seconds"])
        out["f0_shift_hz"] = abs(dub_stats.get("f0_median", 0)
                                 - src_stats.get("f0_median", 0))

    # ---- sync ----
    dubbed = os.path.join(run_dir, "dubbed.mp4")
    if not os.path.exists(dubbed):
        dubbed = os.path.join(run_dir, "audio_only.mp4")
    if os.path.exists(dubbed):
        check = av_sync.verify_sync(dubbed)
        out["sync_ok"] = check["ok"]
        out["sync_delta"] = check.get("delta")

    # ---- intelligibility ----
    meta_path = os.path.join(run_dir, "meta.json")
    intended = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            intended = " ".join(t for t in meta.get("translated", []) if t)
            out["target_lang"] = meta.get("target_lang")
            out["source_lang"] = meta.get("source_lang")

            # A backfilled meta.json was transcribed AFTER the fact, possibly by
            # a different ASR model than the run actually used. Intelligibility
            # then measures the gap between two ASR models rather than the dub's
            # quality — observed inflating CER from ~0.30 to ~0.40 purely by
            # transcribing the backfill with large-v3 against a medium-era run.
            # Only meta written by the run itself gives a trustworthy number.
            if meta.get("backfilled"):
                out["cer_unreliable"] = True
        except Exception as e:
            out["meta_error"] = str(e)

    if intended and transcribe:
        try:
            # Pin the ASR to the target language, or a Hindi dub can come
            # back in Nastaliq and score CER 1.00 on a script mismatch alone.
            tl = out.get("target_lang")
            try:
                heard = transcribe(dub_wav, language=tl).get("text", "")
            except TypeError:
                heard = transcribe(dub_wav).get("text", "")
            out["dub_transcript"] = heard[:300]
            out.update({f"dub_{k}": v for k, v in
                        error_rates(intended, heard).items()})
        except Exception as e:
            out["asr_error"] = str(e)
    elif intended:
        out["note"] = "intelligibility skipped (--no-asr)"
    else:
        out["note"] = "no meta.json — intelligibility skipped"

    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _fmt(v, spec="6.3f"):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "   -  "
    if isinstance(v, bool):
        return " yes  " if v else "  NO  "
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return str(v)[:6]


def report(results: list) -> str:
    lines = []
    head = (f"{'run':>10} {'spk_norm':>9} {'spk_cos':>8} {'ceil':>6} {'floor':>6} "
            f"{'dubCER':>7} {'sync':>6} {'spd':>6} {'pauses':>7} {'dF0':>6}")
    lines.append(head)
    lines.append("-" * len(head))

    for r in results:
        if r.get("error"):
            lines.append(f"{r['run']:>10}  ERROR: {r['error']}")
            continue
        dub = r.get("dub", {})
        lines.append(
            f"{r['run']:>10} "
            f"{_fmt(r.get('speaker_norm'), '9.3f')} "
            f"{_fmt(r.get('speaker_similarity'), '8.4f')} "
            f"{_fmt(r.get('speaker_ceiling'), '6.3f')} "
            f"{_fmt(r.get('speaker_floor'), '6.3f')} "
            f"{_fmt(r.get('dub_cer'), '7.3f')}{'*' if r.get('cer_unreliable') else ' '}"
            f"{_fmt(r.get('sync_delta'), '6.3f')} "
            f"{_fmt(r.get('speech_time_ratio'), '6.2f')} "
            f"{_fmt(dub.get('micro_pauses'), '7d')} "
            f"{_fmt(r.get('f0_shift_hz'), '6.1f')}"
        )

    ok = [r for r in results if not r.get("error")]
    if ok:
        def mean(key):
            rows = ok
            if key == "dub_cer":
                rows = [r for r in ok if not r.get("cer_unreliable")]
            vals = [r[key] for r in rows
                    if isinstance(r.get(key), (int, float))
                    and not np.isnan(r.get(key))]
            return float(np.mean(vals)) if vals else float("nan")

        lines.append("-" * len(head))
        lines.append(
            f"{'MEAN':>10} {_fmt(mean('speaker_norm'), '9.3f')} "
            f"{_fmt(mean('speaker_similarity'), '8.4f')} "
            f"{'':>6} {'':>6} "
            f"{_fmt(mean('dub_cer'), '7.3f')} "
            f"{_fmt(mean('sync_delta'), '6.3f')} "
            f"{_fmt(mean('speech_time_ratio'), '6.2f')}"
        )

    lines.append("")
    lines.append("spk_norm  1.0 = as close to the reference as the speaker's own")
    lines.append("          audio; 0.0 = a different speaker. THE number to track.")
    lines.append("dubCER    character error rate re-transcribing the dub against")
    lines.append("          the intended text. Lower is more intelligible.")
    lines.append("          * = meta.json was backfilled, so this compares two ASR")
    lines.append("            models rather than dub quality. Not trustworthy.")
    lines.append("spd       dub speech time / source speech time. 1.0 = same pace.")
    lines.append("dF0       median pitch shift in Hz vs the source speaker.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Corpus building
# ---------------------------------------------------------------------------
def build_corpus(clips_dir: str, target_lang: str, seconds: float,
                 limit: int, whisper: str, nfe: int, lip_sync: bool) -> list:
    """
    Run the pipeline over a directory of videos and return the run directories.

    Clips are trimmed to `seconds` because TTS dominates runtime at roughly
    10-14x realtime; a 12s excerpt exercises every stage without a 20-clip sweep
    taking all day. Failures are recorded and skipped rather than aborting the
    sweep — a baseline with 10 of 12 clips still beats no baseline.
    """
    import subprocess
    import gradio_app

    exts = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
    clips = sorted(p for p in glob.glob(os.path.join(clips_dir, "*"))
                   if p.lower().endswith(exts))
    if limit:
        clips = clips[:limit]

    tmp = os.path.join(THIS_DIR, "gradio_runs", "_corpus_src")
    os.makedirs(tmp, exist_ok=True)

    run_dirs, failures = [], []
    for i, src in enumerate(clips, 1):
        name = os.path.splitext(os.path.basename(src))[0][:40]
        trimmed = os.path.join(tmp, f"{i:02d}.mp4")
        print(f"\n[CORPUS] {i}/{len(clips)}  {name}")

        try:
            # Skip the first 2s: openings are disproportionately titles/music.
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "2", "-t", str(seconds), "-i", src,
                 "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                 "-c:a", "aac", "-ac", "1", trimmed],
                check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            print(f"[CORPUS] trim failed: {e}")
            failures.append((name, "trim"))
            continue

        try:
            out = gradio_app.run_pipeline(
                trimmed, "auto", target_lang, lip_sync, nfe, whisper, 3, 20,
                progress=lambda *a, **k: None)
            run_dir = os.path.dirname(out[0])
            with open(os.path.join(run_dir, "source_name.txt"), "w") as f:
                f.write(name)
            run_dirs.append(run_dir)
            print(f"[CORPUS] -> {os.path.basename(run_dir)}")
        except Exception as e:
            print(f"[CORPUS] pipeline failed: {type(e).__name__}: {e}")
            failures.append((name, str(e)[:80]))

    if failures:
        print(f"\n[CORPUS] {len(failures)} clip(s) failed:")
        for n, why in failures:
            print(f"  {n}: {why}")
    return run_dirs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Evaluate dubbing pipeline runs")
    ap.add_argument("--run", action="append", default=[],
                    help="a run directory (repeatable)")
    ap.add_argument("--all", action="store_true",
                    help="evaluate every directory in gradio_runs/")
    ap.add_argument("--json", help="write full results to this JSON file")
    ap.add_argument("--no-asr", action="store_true",
                    help="skip intelligibility (no Whisper load)")
    ap.add_argument("--whisper", default="large-v3",
                    help="Whisper model for re-transcription")
    ap.add_argument("--build", metavar="CLIPS_DIR",
                    help="run the pipeline over a directory of videos first")
    ap.add_argument("--target", default="hi", help="target language for --build")
    ap.add_argument("--seconds", type=float, default=12.0,
                    help="trim each clip to this length for --build")
    ap.add_argument("--limit", type=int, default=0,
                    help="max clips to process for --build")
    ap.add_argument("--nfe", type=int, default=16, help="IndicF5 steps for --build")
    ap.add_argument("--no-lipsync", action="store_true",
                    help="audio-only for --build (much faster)")
    args = ap.parse_args()

    runs = list(args.run)
    if args.build:
        runs += build_corpus(args.build, args.target, args.seconds, args.limit,
                             args.whisper, args.nfe, not args.no_lipsync)
    if args.all:
        runs += sorted(glob.glob(os.path.join(THIS_DIR, "gradio_runs", "*")))
    runs = [r for r in runs if os.path.isdir(r)]
    if not runs:
        print("No run directories found. Use --all or --run <dir>.")
        return 1

    transcribe = None
    if not args.no_asr:
        os.environ["WHISPER_MODEL"] = args.whisper
        from speech_to_text_v2 import transcribe_audio
        transcribe = transcribe_audio

    scorer = SpeakerScorer()
    results = []
    for d in runs:
        print(f"[EVAL] {os.path.basename(d)}...")
        try:
            results.append(evaluate_run(d, scorer, transcribe))
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append({"run": os.path.basename(d), "error": str(e)})

    print()
    print(report(results))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=float)
        print(f"\n[EVAL] Wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
