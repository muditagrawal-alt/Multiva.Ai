# Backend architecture

Everything runs locally. There is no inference API call anywhere in this
pipeline; the only network traffic is an optional Supabase row and an optional
R2 upload, both of which the app degrades gracefully without.

## Entry points

| File | What it is |
|---|---|
| `app.py` | The FastAPI service. Owns the job store, the pipeline and every route. |
| `gradio_app.py` | A harness for trying synthesis without the studio. Imported by `eval_harness`. |
| `eval_harness.py` | Measures speaker similarity and intelligibility across a corpus. |

## Models

| Purpose | Model | Where |
|---|---|---|
| Speech recognition | `Systran/faster-whisper-large-v3` | `speech_to_text_v2.py` |
| Translation | `facebook/nllb-200-distilled-600M` | `translation_v2.py` |
| Voice cloning | `ai4bharat/IndicF5` + `charactr/vocos-mel-24khz` | `tts_engines.py` |
| Voice cloning (non-Indic) | XTTS-v2, non-commercial licence | `tts_engines.py` |
| Lip sync | Wav2Lip + s3fd | `lip_sync.py` |
| Speaker similarity | `microsoft/wavlm-base-plus-sv` | `eval_harness.py`, and the voice-match score in `app.py` |

All are preloaded at boot by `_warmup()` and reported through `/api/boot`, which
is what the splash screen displays.

## The dub pipeline

`process_video_task` in `app.py`, one stage per `_set(job_id, step=...)`:

1. **validating** — probe the file, apply in/out points (`av_sync.trim`)
2. **extracting_audio** — 16 kHz mono for ASR (`av_sync.extract_audio`)
3. **transcribing** — segments and word timings (`speech_to_text_v2`)
4. **selecting_reference** — score the cleanest continuous window (`reference_audio`)
5. **translating** — per segment, then repair code-switching (`translation_v2`)
6. **synthesizing_voice** — phrase-level, laid onto a fixed-length track (`dubbing`)
7. **lip_syncing** — mouth region only, single encode (`lip_sync`)
8. **verifying** — A/V drift and voice match
9. **uploading_result** — optional R2

Two stages report a real counter (`synthesizing_voice (3/8)`,
`lip_syncing (240/512 frames)`), which is what drives the progress bar and
bounds how quickly a cancel lands.

## The voice-over pipeline

`voiceover_task` shares stages 1-4, then speaks a typed script (`voiceover.py`).
It skips translation, timeline fitting and lip sync: the user supplied the
words and there is no picture to stay in sync with, so every phrase is spoken
at its natural length with no time-stretching.

## Revising a finished dub

`dubbing.synth_unit` conditions one phrase; `dubbing.assemble` lays cached
phrases onto a track. Each phrase's audio is cached in `<workdir>/units/`, so
editing one line re-synthesizes one phrase rather than the whole run.

- `POST /jobs/{id}/segments/{i}` — new text and/or a new sampling seed
- `POST /jobs/{id}/reference` — clone from a different window (re-speaks everything)
- `POST /jobs/{id}/rerender` — re-run lip sync against the current audio

Audio rebuilds in seconds; the picture is only redone on request, because that
is the part that costs minutes.

## Projects

`project.py` writes a `project.json` beside the files a job already keeps, and
the server scans for manifests at startup. Without it, restarting turned every
finished dub back into a download link. `/videos/` is local-first: manifests are
the source of truth and the database is enrichment, so a cloud outage never
hides local work.

## Cancellation

Cooperative. `_set()` raises `JobCancelled` whenever a step is reported while
the cancel flag is set, which makes every stage boundary a checkpoint for free.
A cancel lands within roughly one phrase, because a TTS forward pass is atomic
and cannot be interrupted mid-call.
