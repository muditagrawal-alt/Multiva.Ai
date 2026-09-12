# Production audit

Two passes over the project. The first (behavioural) is at the bottom. The
second was a full static-and-dynamic campaign before treating this as
production code.

**Final state: 112 checks green, voice match 0.720, zero known defects.**

---

## Pass 3 — the clean clone

The one thing every earlier pass skipped: cloning from GitHub into an empty
directory and installing from scratch. This development venv has carried a
working combination of packages since before the pins were written, which is
exactly why the blocker below survived three audits.

### `pip install -r requirements.txt` failed outright

```
ERROR: ResolutionImpossible
    The user requested huggingface-hub<1.0 and >=0.30
    transformers 5.16.1 depends on huggingface-hub<2.0 and >=1.5.0
```

**Nobody could install this project by following the README.** `transformers`
was unbounded at `>=4.36.0`, so pip reached for 5.x, which needs
`huggingface-hub>=1.5` and contradicts the `<1.0` pin three lines below it. The
file already documented that hazard for `gradio` and missed it for the package
the pin exists to serve.

**Fixed:** bounded to `<5`.

### Verified after the fix

| Step | Result |
|---|---|
| `git clone` | 11 MB |
| `pip install -r requirements.txt` | 169 packages, 2.0 GB, **0 errors** |
| Resolved versions | transformers 4.57.6, huggingface-hub 0.36.2 |
| `import app` and every pipeline module | clean, on a transformers the dev venv has never run |
| `download_models.py --check` | correctly reports the 2 Wav2Lip weights missing |
| `tts_engines.engine_available("xtts")` | `False` — the fix from pass 2 behaves on a real clean install |
| `npm install && npm run build` | 4576 modules, `web/` at 736 KB |
| Missing `web/` at startup | explained, with the command to fix it |

A clean clone installs **169 packages against this venv's 296**, which is where
most of the outstanding advisories were coming from.

### Three files a new clone tripped over

None referenced by anything, all removed:

- **`requirements-base.txt`** — a 165-package freeze beside the 28-package
  list, with no way to tell which to use
- **`runtime.txt`** — a Heroku artifact
- **`wav2lip_loader`** — dead code pointing at `models/wav2lip_gan.pth`, a path
  that does not exist

`Multiva Studio.command` was a second, diverging 78-line launcher that opened a
browser while `run.sh` opens the desktop app. It is now nine lines that
delegate.

---

## Pass 2 — the production campaign

### Method

| Technique | Scope | Result |
|---|---|---|
| Static analysis (`ruff`, correctness rules) | all our Python | 6 latent invariants hardened |
| Dependency CVE audit (`pip-audit`) | 296 installed packages | 20 vulnerable, 2 in the request path — upgraded |
| Fresh-clone simulation | package hidden, real endpoints driven | **1 critical bug** |
| Endpoint fuzzing | 192 hostile bodies × 8 mutating endpoints | 2 crashes, then a third fixing them |
| Concurrency | 240 parallel requests | 0 failures, 0.3s |
| Resource leaks | 400 mixed requests incl. file serving | 0 fd, 0 MB, +1 thread |
| Frontend | typecheck, production build, live console | clean, no runtime errors |
| Restart persistence | projects, knobs, model choice | all survived |

### 1. Sixteen of twenty-eight languages were broken on a clean install

The worst defect found, and invisible to every test before this.

`requirements.txt` leaves Coqui TTS out on purpose — non-commercial licence,
enormous dependency tree — but the app still offered all 28 languages, and 16
of them, **English included**, are spoken by XTTS. On a fresh clone those
failed with `ModuleNotFoundError` deep inside a render, after transcription and
translation had already run.

It never showed here because this venv carries 296 packages from an earlier
era, XTTS among them, and every test used Hindi.

Reproduced by moving the installed package aside and driving the real
endpoints. **Fixed:** languages report whether their engine is installed; a dub
or voice-over into an unavailable one is refused up front with the command that
enables it. Subtitles are unaffected in every language, because they never
reach a TTS engine — verified by producing a Spanish SRT with the package
hidden.

### 2. Projects hidden behind a random identifier

The Projects list filtered on a `user_id` the client generated as a UUID and
kept in `localStorage`. Clearing site data, or opening the studio in another
browser, minted a new one and emptied the list while every file sat untouched
on disk.

Found by loading the app during the audit: it said "No projects yet" with
**fourteen manifests present**, spread across six identifiers left by testing.

The concept is a leftover from the hosted version, where accounts existed. Here
there is one person and no sign-in. **Fixed:** every finished project on the
machine is listed.

### 3. Two crashes from malformed input, and a worse fix

Fuzzing turned up `{"seed": 1e400}` → 500, because `int(inf)` raises
`OverflowError`, which is not a `ValueError`. And `{"provider": 123}` → 500,
reaching `.strip()` on an int.

The first fix coerced with `str()`. That was worse: `str([])` is `"[]"`, which
sailed through as a hostname and left the script model pointing at nonsense
until a connection failed. **Fixed properly:** non-strings are refused with a
400 naming the field, and seeds are bounded to 32 bits, since a larger value
parsed cleanly and then failed inside synthesis.

Re-fuzzed after: 192 hostile requests, no server errors.

### 4. Six correspondence invariants that failed silently

`zip()` truncates to the shorter side. Six of them paired things that must
correspond one to one — a segment with its translation, a frame with its
detected face box — so a mismatch would have dropped the end of a timeline or
left frames un-synced in a finished render, with no error anywhere.

All six hold today (a full render produces no violation), so `strict=True`
costs nothing now and turns a future regression into an exception instead of a
subtly wrong video. Left loose where lengths differ by design: `reference_audio`
pairs a list with its own tail.

### 5. Dependencies

20 of 296 installed packages carry advisories. Two were in the path of
untrusted input and were upgraded: **python-multipart** (parses uploads,
0.0.22 → 0.0.32) and **urllib3** (2.6.3 → 2.7.0). The floor is pinned in
`requirements.txt`.

The remaining 18 are transitive ML-stack packages — `transformers`, `pillow`,
`nltk`, `gitpython` via `wandb`, `basicsr` via `gfpgan`. Most arrive through
packages nothing in the pipeline imports. They are **not** upgraded here:
`transformers` would need a major-version jump that risks the NLLB and
faster-whisper integration, and a fresh clone installs far fewer of them than
this development venv holds.

### Measured and dismissed

- **Blocking filesystem I/O in async endpoints** (14 sites). Real in principle:
  the settings endpoint walks the working directory on the event loop.
  Measured at **4ms over 149 files**, with a worst concurrent latency of 16ms.
  Not worth the complexity of offloading at this scale.
- **`PIPELINE_CONCURRENCY=1`.** One heavy operation at a time is deliberate.
  Interactive edits now fail fast with "the engine is busy" rather than queueing
  past the client timeout.

---

## Pass 1 — the behavioural audit

Both script-model providers, every feature, and the things the suite was not
looking at.

| | Ollama | Groq |
|---|---|---|
| End-to-end suite | 112 passed | 112 passed |
| Voice match | 0.720 | 0.720 |
| Fit: shortened | 3 of 4 | 4 of 4 |
| Fit: per line | 8.4s | 1.6s |

### Deleting a project mid-render ran away with the render

`DELETE` popped the job with no status check, so deleting while rendering
returned **200 reporting success**, left the pipeline running, crashed on the
next save with a `KeyError`, and minutes later **filed a finished video into
the output folder for a project the user had deleted**. Reproduced end to end.

**Fixed:** refused with a 409 saying to cancel first; every save is a no-op
when the job is gone.

### A deleted project could leave a directory behind

A task cancelled while its files were being deleted recreates its working
directory on the way out. **Fixed:** an empty job directory belongs to no
project, so the sweep takes it immediately.

### A render that could not be saved said nothing

`_file_render` swallows failures — correct, a render that worked must not be
reported as failed for an unwritable folder — but said nothing either, sending
people to an output folder that never got the video. **Fixed:** the reason is
shown and clears when filing next succeeds.

### An edit during a render reported failure, then succeeded anyway

A phrase edit needs the same model as a render, so one started during a render
queued behind it, ran past the client's timeout, and landed silently minutes
later. **Fixed:** interactive edits wait two seconds and then say the engine is
busy.

---

## Known and deliberate

- The fit loop can make six model calls at 90s each. Bounded, not fast.
- One heavy operation at a time.
- Two windows on one project are not coordinated; last save wins.
- XTTS stays out of the default install. Enable it with
  `pip install "TTS>=0.22.0"` if you need the non-Indian languages.
