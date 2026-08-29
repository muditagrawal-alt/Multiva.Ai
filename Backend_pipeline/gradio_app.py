"""
Gradio test harness for the dubbing pipeline.

This is a debugging tool, not the product UI. It runs the same stages as
`app.process_video_task` but surfaces the intermediates that actually explain
why a result sounds or looks wrong:

  * the reference clip that was auto-selected  — the single biggest driver of
    cloning quality, and otherwise invisible
  * the dubbed audio track on its own          — lets you judge the voice
    without waiting for lip sync
  * per-segment timing and speaking rate       — a rate pinned at the clamp
    means the translation is too long for its slot
  * per-stage wall times                       — where the latency actually goes
  * the sync verdict                           — video vs audio duration

Run:  ../venv/bin/python gradio_app.py
"""

import os
import shutil
import sys
import time
import traceback
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import warnings

import gradio as gr

# Gradio 5.50 warns that `theme` must move to launch() — but its launch() has no
# `theme` parameter and no **kwargs, so the warning asks for something that is
# impossible in this version. The introspection below already puts `theme`
# wherever it is actually accepted, so silence the misleading warning.
warnings.filterwarnings(
    "ignore", message=".*'theme' parameter in the Blocks constructor.*")

# Gradio keeps moving these between Blocks() and launch() across versions, so
# ask the installed signatures instead of guessing from the version number.
#   theme              — moved to launch() (deprecated on Blocks from 5.50)
#   analytics_enabled  — lives on Blocks(); gradio 5.50's queue analytics calls
#                        NDFrame.infer_objects(copy=...), a pandas >= 2 API, and
#                        this env is on pandas 1.5.3 (pinned by coqui TTS and
#                        datasets). It throws after every run. Purely cosmetic —
#                        gr.Dataframe itself is fine on 1.5.3 — so switch it off
#                        rather than churn a dependency the TTS stack needs.
# (gradio must stay on 5.x here: gradio 6 wants huggingface-hub>=1.16, which
# transformers 4.49 refuses.)
import inspect as _inspect

_LAUNCH_PARAMS = set(_inspect.signature(gr.Blocks.launch).parameters)
_BLOCKS_PARAMS = set(_inspect.signature(gr.Blocks.__init__).parameters)
_THEME = gr.themes.Soft()
_BLOCKS_KW, _LAUNCH_KW = {}, {}

if "theme" in _LAUNCH_PARAMS:
    _LAUNCH_KW["theme"] = _THEME
elif "theme" in _BLOCKS_PARAMS:
    _BLOCKS_KW["theme"] = _THEME

if "analytics_enabled" in _BLOCKS_PARAMS:
    _BLOCKS_KW["analytics_enabled"] = False
elif "analytics_enabled" in _LAUNCH_PARAMS:
    _LAUNCH_KW["analytics_enabled"] = False

import av_sync
import dubbing
import languages as L
import reference_audio
import tts_engines

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gradio_runs")
os.makedirs(OUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Dropdown choices
# ---------------------------------------------------------------------------
def _target_choices():
    """
    Indian languages first — that is the focus of the product.

    No separator rows: a Dropdown choice list must not repeat a value, and a
    separator would have to borrow one, which breaks `value=` matching.
    The engine is shown per row instead.
    """
    indic, other = [], []
    for entry in L.supported_targets():
        if entry["engine"] == L.ENGINE_INDICF5:
            indic.append((f"{entry['name']} ({entry['code']})", entry["code"]))
        else:
            other.append((f"{entry['name']} ({entry['code']}) · XTTS",
                          entry["code"]))
    return indic + other


SOURCE_CHOICES = [("Auto-detect", "auto")] + [
    (f"{L.LANGUAGES[c]['name']} ({c})", c) for c in L.LANGUAGES
]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(video_path, source_lang, target_lang, do_lip_sync,
                 nfe_step, whisper_model, detect_every, crf,
                 progress=gr.Progress()):
    if not video_path:
        raise gr.Error("Upload a video first.")

    run_id = uuid.uuid4().hex[:8]
    workdir = os.path.join(OUT_DIR, run_id)
    os.makedirs(workdir, exist_ok=True)
    timings, t_all = {}, time.time()

    def stage(name, t0):
        timings[name] = time.time() - t0

    try:
        os.environ["WHISPER_MODEL"] = whisper_model
        tts_engines.get_engine(target_lang).nfe_step = int(nfe_step)

        src = os.path.join(workdir, "input" + os.path.splitext(video_path)[1])
        shutil.copy(video_path, src)

        info = av_sync.probe(src)
        if not info["video"]:
            raise gr.Error("That file has no video stream.")
        video_dur = info["duration"]

        # ---- transcribe ----
        progress(0.05, desc="Extracting audio…")
        t = time.time()
        stt_wav = av_sync.extract_audio(src, os.path.join(workdir, "stt.wav"))
        stage("extract", t)

        progress(0.12, desc="Transcribing…")
        from speech_to_text_v2 import transcribe_audio
        t = time.time()
        result = transcribe_audio(stt_wav)
        stage("transcribe", t)

        segments = result.get("segments") or []
        if not segments:
            raise gr.Error("No speech detected in that video.")

        detected = result.get("language")
        resolved_src = detected if source_lang == "auto" else source_lang
        try:
            resolved_src = L.normalize(resolved_src)
        except L.UnsupportedLanguage:
            resolved_src = "en"

        # Overriding the source with the wrong language is silently destructive:
        # NLLB will happily "translate" Hindi it has been told is English and
        # return confident nonsense. Surface the disagreement.
        lang_warning = ""
        if source_lang != "auto" and detected and L.normalize(detected) != resolved_src:
            lang_warning = (
                f"⚠️ You selected **{L.display_name(resolved_src)}** as the source, "
                f"but the audio was detected as **{L.display_name(L.normalize(detected))}**. "
                f"If the detection is right, the translation step is being fed the "
                f"wrong language and the output will be nonsense — switch the source "
                f"to Auto-detect."
            )
            print(f"[UI] {lang_warning}")

        # ---- reference ----
        progress(0.2, desc="Choosing reference clip…")
        t = time.time()
        reference = reference_audio.build_reference(
            src, segments, video_dur, os.path.join(workdir, "reference.wav"))
        stage("reference", t)

        # ---- translate ----
        progress(0.28, desc="Translating…")
        from translation_v2 import translate_segments
        t = time.time()
        translated = translate_segments(segments, resolved_src, target_lang)
        # Same-language re-voicing skips the translator entirely, so residual
        # English inside Hindi text would otherwise reach the TTS as Latin.
        from translation_v2 import fix_code_switching
        translated = fix_code_switching(translated, target_lang)
        stage("translate", t)

        if not any(x.strip() for x in translated):
            raise gr.Error("Translation produced no text.")

        # ---- plan + synthesize ----
        dm = dubbing.DurationModel(reference["duration"], reference["text"],
                                   source_lang=resolved_src, target_lang=target_lang)
        units, unit_texts = dubbing.prepare_units(
            segments, translated, source_audio=stt_wav, video_duration=video_dur)
        _eng = tts_engines.get_engine(target_lang)
        _nat = (lambda t: _eng.natural_duration(t, reference["path"],
                reference["text"], lang=L.synth_lang(target_lang))) \
            if hasattr(_eng, "natural_duration") else None
        plan = dubbing.plan_timeline(units, unit_texts, dm, video_dur, natural_fn=_nat)

        def tts_progress(done, total):
            progress(0.32 + 0.45 * done / max(total, 1),
                     desc=f"Cloning voice — segment {done + 1}/{total}")

        t = time.time()
        track = dubbing.synthesize_timeline(
            plan, reference["path"], reference["text"], reference["duration"],
            target_lang, video_dur, tts_progress)
        dub_wav = os.path.join(workdir, "dub.wav")
        import soundfile as sf
        sf.write(dub_wav, track, dubbing.SAMPLE_RATE, subtype="PCM_16")
        stage("tts", t)

        # ---- lip sync ----
        out_video = None
        if do_lip_sync:
            from lip_sync import generate_lip_synced_video

            def w2l_progress(done, total):
                progress(0.8 + 0.18 * done / max(total, 1),
                         desc=f"Lip sync — frame {done}/{total}")

            t = time.time()
            out_video = generate_lip_synced_video(
                src, dub_wav, os.path.join(workdir, "dubbed.mp4"),
                detect_every=int(detect_every), crf=int(crf),
                progress=w2l_progress)
            stage("lip_sync", t)
        else:
            # Audio-only preview: original picture, dubbed track, no re-encode.
            t = time.time()
            out_video = av_sync.mux(src, dub_wav,
                                    os.path.join(workdir, "audio_only.mp4"))
            stage("mux", t)

        # Persist what the run intended to say, so eval_harness can measure
        # intelligibility later by re-transcribing the dub against it.
        try:
            import json as _json
            with open(os.path.join(workdir, "meta.json"), "w",
                      encoding="utf-8") as f:
                _json.dump({
                    "source_lang": resolved_src,
                    "target_lang": target_lang,
                    "detected_lang": detected,
                    "nfe_step": int(nfe_step),
                    "whisper_model": whisper_model,
                    "segments": [{"start": s_.get("start"), "end": s_.get("end"),
                                  "text": (s_.get("text") or "").strip()}
                                 for s_ in segments],
                    "translated": list(translated),
                    "units": [{"start": u["start"], "end": u["end"],
                               "text": u.get("target", "")} for u in units],
                }, f, ensure_ascii=False, indent=2)
        except Exception as _e:
            print(f"[UI] Could not write meta.json: {_e}")

        # ---- report ----
        check = av_sync.verify_sync(out_video)
        total = time.time() - t_all

        verdict = "✅" if check["ok"] else "⚠️"
        summary = [
            f"### {verdict} {check['reason']}",
            "",
        ]
        if lang_warning:
            summary += [lang_warning, ""]
        if resolved_src == L.normalize(target_lang):
            summary += [f"ℹ️ Source and target are both "
                        f"**{L.display_name(target_lang)}** — this is a "
                        f"same-language re-voice, the text is not translated.", ""]
        summary += [
            f"**{L.display_name(resolved_src)} → {L.display_name(target_lang)}**"
            f" · engine `{L.engine_for(target_lang)}`"
            + (f" (spoken as {L.synth_lang(target_lang)})"
               if L.synth_lang(target_lang) != target_lang else ""),
            f"Video **{video_dur:.3f}s** · audio **{check['audio'] or 0:.3f}s**"
            f" · {len(plan)} segments",
            f"Whisper detected `{detected}`"
            + ("" if source_lang == "auto" else f", you selected `{source_lang}`"),
            "",
            f"**Total {total:.1f}s** for {video_dur:.1f}s of video "
            f"({total / max(video_dur, 0.01):.1f}× realtime)",
            "",
            "| stage | seconds |", "|---|---|",
        ]
        for k, v in timings.items():
            summary.append(f"| {k} | {v:.1f} |")

        ref_note = (f"Auto-selected **{reference['start']:.2f}s – "
                    f"{reference['end']:.2f}s** ({reference['duration']:.2f}s)\n\n"
                    f"> {reference['text'][:300]}")

        rows = []
        for p, seg in zip(plan, [units[p["index"]] for p in plan]):
            clamped = ("⚠️ clamped" if p["rate"] >= dubbing.MAX_SPEEDUP - 1e-3
                       or p["rate"] <= dubbing.MAX_SLOWDOWN + 1e-3 else "")
            rows.append([
                f"{seg.get('start', 0):.2f}–{seg.get('end', 0):.2f}",
                f"{p['start']:.2f}",
                f"{p['duration']:.2f}",
                f"{p['rate']:.2f} {clamped}".strip(),
                (seg.get("text") or "").strip()[:70],
                p["text"][:70],
            ])

        return (out_video, "\n".join(summary), ref_note,
                reference["path"], dub_wav, rows)

    except gr.Error:
        raise
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
with gr.Blocks(title="Multiva.Ai — pipeline test", **_BLOCKS_KW) as demo:
    gr.Markdown(
        "# 🎬 Multiva.Ai — dubbing pipeline test harness\n"
        "Runs the real pipeline and exposes the intermediates. "
        "Uncheck **lip sync** to iterate on voice quality ~5× faster."
    )

    with gr.Row():
        with gr.Column(scale=1):
            video_in = gr.Video(label="Video")
            target_in = gr.Dropdown(
                _target_choices(), value="hi", label="Target language",
                info="Indian languages use IndicF5. Rows marked XTTS use "
                     "Coqui, whose model licence is non-commercial.")
            source_in = gr.Dropdown(SOURCE_CHOICES, value="auto",
                                    label="Source language",
                                    info="Auto-detect uses Whisper; overriding "
                                         "helps when detection is wrong.")
            lipsync_in = gr.Checkbox(
                value=True, label="Run lip sync",
                info="Off = dubbed audio over the original picture. Much faster.")

            with gr.Accordion("Advanced", open=False):
                nfe_in = gr.Slider(8, 48, value=16, step=4,
                                   label="IndicF5 steps (nfe_step)",
                                   info="16 ≈ 6× realtime, 32 ≈ 12×. "
                                        "Higher = better, slower.")
                whisper_in = gr.Dropdown(
                    ["large-v3", "medium", "small", "base"], value="large-v3",
                    label="Whisper model",
                    info="large-v3 for Indian languages: `medium` misspells "
                         "Hindi badly (आदमी→आद्मी, अपराध→अपराद), and TTS then "
                         "pronounces the misspelling. Costs ~30s more.")
                detect_in = gr.Slider(1, 6, value=3, step=1,
                                      label="Detect face every N frames")
                crf_in = gr.Slider(14, 30, value=18, step=1, label="Output CRF",
                                   info="Lower = better quality, bigger file. 18 is near-visually-lossless.")

            run_btn = gr.Button("Dub", variant="primary", size="lg")

        with gr.Column(scale=1):
            video_out = gr.Video(label="Result")
            report_out = gr.Markdown()
            with gr.Accordion("Reference clip used for cloning", open=True):
                ref_note_out = gr.Markdown()
                ref_audio_out = gr.Audio(label="Reference", type="filepath")
            dub_audio_out = gr.Audio(label="Dubbed track (alone)", type="filepath")

    seg_out = gr.Dataframe(
        headers=["orig span", "placed at", "duration", "rate", "source", "translated"],
        label="Segments — a clamped rate means the translation does not fit its slot",
        wrap=True,
    )

    run_btn.click(
        fn=run_pipeline,
        inputs=[video_in, source_in, target_in, lipsync_in,
                nfe_in, whisper_in, detect_in, crf_in],
        outputs=[video_out, report_out, ref_note_out,
                 ref_audio_out, dub_audio_out, seg_out],
    )


def _free_port(preferred: int) -> int:
    """
    Return `preferred` if it is free, otherwise an OS-assigned free port.

    Gradio hard-fails with "Cannot find empty port in range" when the port is
    taken, which happens constantly during testing — a previous run of this
    harness is usually still holding it. Falling back beats refusing to start.
    """
    import socket

    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
                port = sock.getsockname()[1]
            except OSError:
                continue
        if port != preferred:
            print(f"[UI] Port {preferred} is busy (another run of this harness?"
                  f" try: pkill -f gradio_app.py) — using {port} instead")
        return port
    return preferred


if __name__ == "__main__":
    port = _free_port(int(os.getenv("GRADIO_SERVER_PORT", 7860)))
    print(f"\n[UI] Multiva.Ai test harness -> http://127.0.0.1:{port}\n")
    demo.launch(server_name="127.0.0.1", server_port=port,
                share=False, inbrowser=True, **_LAUNCH_KW)
