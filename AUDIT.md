# Final audit

A rigorous pass over the whole project: both script-model providers, every
feature, and a hunt for the things the suite was not looking at.

**Result: 112 checks green on Ollama, 112 on Groq, voice match 0.720 on both.**
Three real bugs found and fixed, all of them invisible to the existing tests
because the tests only ever did things in the right order.

---

## How it was tested

| | Ollama | Groq |
|---|---|---|
| End-to-end suite | 112 passed, 0 failed | 112 passed, 0 failed |
| Voice match | 0.720 | 0.720 |
| Fit: shortened | 3 of 4 | 4 of 4 |
| Fit: numbers kept | 4 of 4 | 4 of 4 |
| Fit: per line | 8.4s | 1.6s |

On the one phrase in the test clip that genuinely overruns — a 5.694s slot
holding 7.4s of speech — Groq brought it to 3.889s. Ollama did not get it under
the slot.

Also checked: 14 adversarial edge cases (malformed bodies, null fields, nested
values where a number belongs, unicode and over-long names, undersized uploads,
unsupported languages, negative in-points, and unknown ids against seven
endpoints) — all handled. The bundled desktop app was launched cold with the
port free: it found its checkout, spawned an engine in 8s, and served the
studio.

---

## What was wrong

### 1. Deleting a project mid-render ran away with the render

The worst of the three, and entirely reachable from the interface.

`DELETE /videos/{id}` popped the job with no check on its status. Deleting a
project while it was rendering:

- returned **200, reporting success**
- left the pipeline running, still using the GPU
- crashed on the next save with `KeyError: '9a367cbf-e91'`
- and minutes later **filed a finished video into the output folder for a
  project the user had deleted** — `multiva_fixture_hi_51.mp4` appeared in
  `~/Movies/Multiva` after the delete
- left an orphaned working directory behind

Reproduced end to end before fixing, not inferred.

**Fixed:** a rendering job cannot be deleted — 409, saying to cancel first.
Cancel then delete works and leaves nothing. Every `project.save` is a no-op
when the job is gone, so a delete landing in the same instant cannot raise.

### 2. A deleted project could leave a directory behind

A task cancelled while its files were being deleted recreates its working
directory on the way out. The result was an empty folder that the sweep would
not touch for 48 hours, because the sweep only removed things older than the
retention window.

**Fixed:** an empty job directory belongs to no project and holds nothing, so
it goes immediately. Directories with a manifest are still never swept —
verified: 11 saved projects before and after.

### 3. A render that could not be saved said nothing at all

`_file_render` deliberately swallows failures, which is right: a render that
worked must not be reported as failed because a folder was unwritable. But it
returned an empty path and nothing else, so the studio showed no "Saved to" row
and no error either. Someone with an unmounted drive or a permissions change
would go to their output folder, find nothing, and conclude the render failed.

Reproduced by pointing the output folder at a read-only directory.

**Fixed:** the reason is carried on the job and shown — "The render is
finished, but it could not be copied to …" — and it clears the moment filing
succeeds again.

---

## Checked and found sound

- **Cancellation** lands within about one phrase, and a cancelled job's files
  are cleaned up.
- **Undo** restores the original take byte for byte, not a re-synthesis.
- **`_set`** already guarded a job deleted mid-render; only the success paths
  used unguarded access, and those are fixed.
- **Phrase history** is bounded at 20 entries with its audio snapshots cleaned.
- **`_file_render`** never overwrites an earlier take.
- **Re-render re-files** the output copy; re-cloning correctly does not, since
  it changes the audio rather than the picture.
- **Path traversal** on every upload path, fixed earlier in the project and
  still holding.
- **Model downloads** resume, verify by SHA-256, refuse a second concurrent
  run, and leave no `.part` behind.
- **Settings** ignore unparseable values rather than storing them, and a knob
  belonging to an unused model reports itself inapplicable.

---

## Known and deliberate

- **The fit loop can take up to six model calls.** At Ollama's ~8s per line
  that is a slow minute; at Groq's 1.6s it is unnoticeable. The timeout is 90s
  per call, so a stalled provider can hold the request open. Bounded, but not
  fast.
- **One heavy operation at a time** (`PIPELINE_CONCURRENCY=1`). Editing a
  phrase while another project renders waits for the GPU rather than failing.
- **Two windows on one project** are not coordinated. The manifest is written
  by whoever saves last.
