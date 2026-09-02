# First run, settings, and getting the models

Three phases: downloading models from inside the app, the first-run sequence,
and a settings panel behind a gear.

Baseline: **94 checks green, voice match 0.720**, all of PLAN.md done.

---

## Checked before planning

- **The Setup page already does most of the first run.** It picks a model per
  stage, sets the output folder, and embeds the script-model panel. It is
  reached automatically: `Home` redirects to `/setup` when
  `/api/settings/engines` reports `configured: false`. What is missing is the
  downloads, the project name, and a way back to any of it later.
- **The engine catalogue already knows what is present.** `engines.catalog()`
  marks every option `ready` plus a `source` of `download`, `manual` or
  `builtin`. Nothing can download from it yet.
- **The Wav2Lip weights are marked `manual` and no longer need to be.** That
  was true when nothing knew where to fetch them; `scripts/download_models.py`
  now has verified mirrors and SHA-256s for both files.
- **New project goes straight to the studio.** It is a `<Link to="/studio">`
  with no name and no project created — the name only exists after a render.

---

## Phase 5 — Getting the models  ·  done

The answer to "how does a user download models without opening a terminal":
the app does it, on the setup page, with progress. The script stays for people
who prefer it, but it stops being the only way.

### 5.1 One downloader, two front ends — refactor

- [x] Move the fetch-and-verify logic out of `scripts/download_models.py` into
      `Backend_pipeline/models.py`: the Hub snapshots, the mirrored `.pth`
      files, the SHA-256 check, the resume and per-mirror retry
- [x] `scripts/download_models.py` becomes a thin CLI over it, so the terminal
      path keeps working exactly as it does now
- [x] Wav2Lip stops being `manual`: it has verified mirrors now

*Touches:* new `Backend_pipeline/models.py`, `scripts/download_models.py`,
`engines.py`

### 5.2 Download endpoints — additive

- [x] `GET /api/models` — every model, its size, whether it is present, and
      whether it is required
- [x] `POST /api/models/download` — start fetching one or all missing models,
      in the background, refusing a second run while one is in flight
- [x] `GET /api/models/progress` — which file, how many bytes, which item of
      how many, and any error
- [x] Cancellable, and safe to close the window mid-download: partial files
      are `.part` and resume

*Touches:* `app.py`

### 5.3 Downloads on the setup page — additive

- [x] Each stage lists its options with size and a present/missing mark
- [x] A **Download** button per model and a **Download everything missing**
      for the impatient
- [x] A progress bar with the real byte count, not a fake timer
- [x] Free disk space shown against what is still needed
- [x] Choosing a model that is not present offers to fetch it rather than
      failing later at render time

*Touches:* `Setup.tsx`, `api.ts`

---

## Phase 6 — The first run  ·  done

### 6.1 The sequence — behaviour

First launch:

1. Splash while the engine loads *(exists)*
2. **Setup** — models, script model, where projects are saved
3. **Projects** — empty, with one thing to do
4. **New project** — asks for a name, then opens the studio

Every launch after: splash → Projects.

- [x] Setup gains a **Finish setup** action that marks setup done and moves to
      Projects, so it is a step with an end rather than a page you escape
- [x] Setup is skipped on later launches but always reachable
- [x] Nothing is required: keeping every default and pressing through is a
      supported first run

*Touches:* `Setup.tsx`, `Home.tsx`, `engines.py`

### 6.2 Projects are named when they are made — behaviour

- [x] **New project** opens a dialog asking for a name, with the language pair
- [x] The name reaches the studio and is sent with the render, so it is set
      from the beginning rather than after the fact
- [x] Rename from the Projects list and from the studio title bar
- [x] An empty name falls back to the clip's file name rather than blocking

*Touches:* `Home.tsx`, `Studio.tsx`, `api.ts`

---

## Phase 7 — Settings  ·  done

### 7.1 The gear — additive

- [x] Gear in the top-right of both Projects and the studio
- [x] Opens a floating panel over the current page, not a route: settings are
      something you adjust while looking at your work, not somewhere you go
- [x] Escape and click-outside close it; focus returns to the gear

*Touches:* `console.tsx`, new `components/Settings.tsx`, `Home.tsx`, `Studio.tsx`

### 7.2 What is in it

Sections, in the order someone would look for them:

- [x] **Models** — the four stages, each with its options, present/missing, and
      a download button. The same component the setup page uses
- [x] **Script model** — Ollama or a hosted key. The existing panel, moved in
- [x] **Locations** — where finished projects are saved, and where working
      files live
- [x] **Storage** — how much the working directory is holding, how long
      abandoned runs survive before they are swept, and a sweep-now button
- [x] **Output** — encode quality (CRF) and encoder preset
- [x] **Performance** — Wav2Lip batch sizes (compute device left alone)
- [x] **About** — versions, model licences, and the non-commercial warning on
      XTTS

Everything above is a knob that already exists as an environment variable or a
stored setting. Nothing here invents a capability; it exposes what the pipeline
is already reading.

- [x] A restart notice where one is genuinely needed, because engine choices
      are read at import

*Touches:* `Settings.tsx`, `app.py` for the storage and output settings

---

## Must not break

- [x] All existing checks — now 105
- [x] Voice cloning quality (0.720)
- [x] The five outputs, including the three standalone ones
- [x] Cut, copy, paste, undo and the fit colours
- [x] Render / re-render / up-to-date states
- [x] The Media · Edit · Deliver pages
- [x] Ollama and Groq both reachable
- [x] A fresh clone still setting up from the command line
- [x] The desktop app finding its checkout

---

## Sequencing

Phase 5 first: the setup page cannot honestly ask someone to choose a model
until it can also get it for them. Phase 6 is the flow around it. Phase 7
reuses phase 5's model component, so it is last and mostly assembly.
