# Multiva.Ai

**Offline video dubbing for Indian languages that preserves the speaker's voice
and re-syncs their lips — with a quantitative evaluation harness.**

Upload a talking-head video, pick a target language, get back the same person
saying the same thing in that language, in their own voice, with their mouth
matching. Everything runs on-device: no API keys, no per-minute cost, no footage
leaving the machine.

```
video ─► faster-whisper ─► NLLB-200 ─► IndicF5 ─► Wav2Lip ─► dubbed video
          transcribe       translate    clone      re-sync
```

| | |
|---|---|
| **Languages** | Hindi, Marathi, Tamil, Telugu, Kannada, Gujarati, Malayalam, Punjabi, Odia, Assamese, Bengali (IndicF5) · English and 15 others (XTTS) |
| **Runs on** | Apple Silicon (MPS), CUDA, or CPU. Developed on an M4 / 24 GB |
| **Speed** | ~12–15× realtime. Offline batch tool, not live |
| **Cloning** | Zero-shot from ~6–12s of the speaker. No training, no fine-tuning |

---

## Results

Measured across 21 clips (Hindi, English and mixed; 368p–1080p; portrait and
landscape) with `eval_harness.py`.

| | n | speaker similarity | dub CER | pace | A/V sync |
|---|---|---|---|---|---|
| same-language re-voice | 7 | **0.912** | **0.088** | 0.76 | 0 ms |
| cross-lingual dub | 14 | **0.878** | **0.158** | 0.99 | 0 ms |

*Speaker similarity is a WavLM x-vector cosine rescaled per clip between a floor
(the reference vs a different speaker) and a ceiling (the reference vs the
speaker's own audio). 1.0 means indistinguishable from the real speaker; 0.0
means a stranger. A raw cosine from this model sits near 0.9x for everything and
is meaningless unanchored.*

*CER re-transcribes the dub and compares it to the text the pipeline intended to
say — the closest automatable proxy for "can a listener follow this".*

---

## What was hard

**A model that produced perfect silence.** `AutoModel.from_pretrained` matched
**0 of 447** checkpoint tensors: IndicF5's weights are saved from a
`torch.compile`-wrapped module, so every key carries an `_orig_mod.` level the
instantiated model does not have. Both the DiT and the vocoder stayed randomly
initialised — in practice NaN — and the pipeline emitted digital silence of
exactly the right duration. Every duration and sync check passed. The only
signal was a warning buried in transformers' startup output.

That is why this repo has an evaluation harness. Every failure here had the same
shape: fine by the metric that existed, broken by the metric that did not.

- silence passed duration checks
- a 0.40× phase-vocoder squeeze (from a reference whose reported length did not
  match the file on disk) passed sync checks
- per-phrase compression ranging 0.85×–1.76× hid inside a healthy-looking 1.15×
  aggregate

**The evaluation was measuring noise.** Flow-matching sampling is stochastic, so
identical inputs produced audio differing by 0.81 max amplitude — and the same
clip scored CER 0.088 on one run and 0.258 on the next with nothing changed.
Synthesis is now seeded (bit-identical across runs), because an A/B harness that
cannot separate a code change from sampling luck is worse than no harness: it
produces confident, wrong conclusions.

**Speech rhythm is load-bearing.** Whisper segments are not speech units. Filling
each segment's slot with one continuous utterance replaced every pause inside it
with words. The audio has 2.41s of silence across 16 pauses of 50–150ms — short
enough to feel like nothing, and removing them makes speech unfollowable.
Whisper's own word timestamps cannot find them (it reported 1.1% pause against
the waveform's 11.9%), so they are detected from the audio envelope directly.

**Lip sync was ~50× too slow.** Face detection ran on CPU at full resolution,
every frame, and the output passed through five lossy encode generations.
Detecting on MPS at 256p every third frame with interpolation, and piping raw
frames into a single ffmpeg encode, took it from roughly 75× realtime to
**1.43×** with no loss in detection recall (32/32 frames at every setting tested).

---

## Quick start

Everything runs on the Python 3.10 venv, the only environment with the full
stack (torch + MPS, f5_tts, faster-whisper, Wav2Lip, Coqui TTS).

```bash
# API
cd Backend_pipeline
../venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000

# Test harness (recommended for evaluating output)
../venv/bin/python gradio_app.py          # http://127.0.0.1:7860
```

The harness exposes what a black-box UI hides: the reference clip that was
auto-selected, the dubbed track on its own, per-phrase timing and speaking rate,
per-stage wall times, and the sync verdict. Uncheck **Run lip sync** to iterate
on voice quality ~5× faster.

## Evaluation

```bash
../venv/bin/python eval_harness.py --all                    # score existing runs
../venv/bin/python eval_harness.py --build ../test_videos \
    --target hi --seconds 12 --no-lipsync --json after.json # full sweep
```

Pointed at this project's own history, the harness flags the silent runs that
were once shipped as "verified" — `spk_norm` −1.08 to −1.65, `spd` 0.00 —
without being told which they were.

---

## Known limits

- **~12–15× realtime.** A 2-minute video takes roughly 25 minutes. TTS dominates;
  batching does not help (measured), because the cost is DiT sampling itself.
- **IndicF5 articulates faster than some speakers** — on one clip it said the same
  words in 14.6s where the speaker took 17.6s. `fix_duration` sets total length;
  the model pads rather than slows when given more.
- **Talking-head video only.** Wav2Lip needs a visible, roughly front-facing face.
- **Wav2Lip generates a 96×96 mouth**, composited back with a feathered mask. On
  1080p footage this is the most visible weakness.
- **XTTS (non-Indian languages) is under the Coqui Public Model License, which is
  non-commercial.** IndicF5 has no such restriction.
- Translation quality on poetry and heavy code-switching is weak; NLLB is a
  sentence-level model.

## Documentation

`Backend_pipeline/PIPELINE.md` — architecture, the invariants that make A/V
drift structurally impossible, measured latency numbers, and the traps
(checkpoint prefix, reference-duration coupling, gradio/huggingface-hub version
pin) that will silently break this if disturbed.
