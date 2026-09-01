# Resolve-style studio rebuild

Fourteen items across five phases: page-based navigation, honest render
semantics, timeline editing, and three standalone outputs that no longer
require running the whole pipeline.

Baseline before any of this starts: **67 checks green, voice match 0.720**.
After phase 4: **94 checks green**, same voice match. Branch is now `main`.

---

## Checked before planning

Facts established against the repository, not assumed. Two of them changed the
plan.

- **`Frontend/` and `frontend/` are the same directory.** This filesystem is
  case-insensitive, so the "old frontend" visible in the contributors' history
  is the live one. Deleting it would have destroyed the working app.
  `stat -f %i` returns inode 38030961 for both.
- **`main` is fully contained in `studio-rebuild`.** Making this branch the
  default is a fast-forward: no commit and no file is lost.
  `git merge-base --is-ancestor main studio-rebuild` returns true.
- **The three contributors wrote 20 real commits** — Aditya 7, Parshant 7,
  Nikhil 6, between 2026-02-20 and 2026-04-19 — and all are ancestors of this
  branch. Their code (`Database/`, the old HTML frontend, `Demo.py`) is already
  deleted; the commits are not. Needs a decision, see 0.2.
- **There is no transcribe-only path.** Transcription happens only inside a dub
  job, so an SRT today costs a full pipeline run — Whisper, NLLB, IndicF5 and
  Wav2Lip — for something Whisper alone does in seconds. This is what makes the
  standalone tabs worth building.
- **Render and re-render are already different code paths.** `render()`
  (Studio.tsx:371) calls `submitVideo` and starts a new job from scratch;
  `rerender()` (Studio.tsx:356) calls `rerenderVideo` and only redoes lip sync.
  The cheap one is buried in the phrase panel.

---

## What Resolve actually does

The part worth copying is the page model, not the look.

Resolve puts a page bar along the bottom of the window — Media, Cut, Edit,
Fusion, Color, Fairlight, Deliver — and each page is a complete workspace with
its own panels, viewer and toolset. The project follows you across pages: you
are never switching documents, you are switching what you are doing to the same
one. Deliver is where you choose what comes out, and it holds a render queue
rather than a single button.

Multiva has the same shape hiding in it — import and trim, then edit phrases,
then produce something. Today all three are stacked in one scrolling sidebar,
which is why the render button and the re-render button ended up in different
places with no relationship between them.

| Resolve page | Multiva equivalent | Holds |
|---|---|---|
| Media | Media | Import, project name, clip inspection, in and out points |
| Edit | Edit | Phrase timeline, cut/copy/paste, re-roll, reference window |
| Deliver | Deliver | Output type, render, the finished file, exports |

The five outputs live on Deliver as presets, which is exactly where Resolve puts
them. A subtitle-only job never needs the Edit page, so that page dims rather
than disappearing — the same way Fusion dims on an audio project.

---

## Phase 0 — Repository  ·  done

Independent of the code, and the only irreversible work. Done first and
separately so a mistake here cannot be confused with a mistake in the app.

### 0.1 Make `studio-rebuild` the default branch, retire `main` — irreversible

- [x] Rename on GitHub, move the default, then delete the old ref
- [x] Delete last, so there is a rollback target until the switch is confirmed

Verified safe: `main` is an ancestor of `studio-rebuild`, so the new default
contains every commit the old one had.

*Touches:* git refs on origin. No working-tree change.

### 0.2 Contributor list — needs a decision

GitHub builds that list from commit authorship on the default branch. Those 20
commits are real work by three people, so this is not repo hygiene.

- **A. Leave history alone.** Their names stay. Nothing is rewritten.
- **B. Re-root the published history.** Start the default branch at the first
  rebuild commit, so the pre-rebuild era — including their commits and the code
  they wrote, all of which is already deleted — is not carried forward. Nothing
  is reassigned; the old history stops being published. *Recommended.*
- **C. Rewrite authorship.** Change the author on their commits. This reassigns
  their work, and should be chosen explicitly rather than done quietly.

The `Co-Authored-By: Claude` trailers are separate — 188 of them, my
attribution rather than a person's. Stop adding the trailer now; strip the
existing ones under B or C.

- [x] Decision recorded: **B — re-rooted at the first rebuild commit**
- [x] Carried out

*Touches:* git history on origin. Force-push required for B and C.

### 0.3 Delete the stray `frEnd` file — safe

- [x] `git rm frEnd`

One byte, committed by accident in April, referenced by nothing. The only
genuinely dead thing at the repository root. **`Frontend/` is not dead** — it is
the live app under a different capitalisation.

---

## Phase 1 — Backend  ·  done

The tabs and the render changes both need capabilities that do not exist yet.
Built and tested before any UI depends on them.

### 1.1 Projects have names — additive

- [x] `name` field in the manifest, defaulting to the file name so existing
      projects keep working
- [x] `PATCH /jobs/{id}` to rename
- [x] Set at import, editable afterwards
- [x] Shown in the studio title bar (Projects list still to do), in place of the job
      id that is now hidden

*Touches:* `project.py`, `app.py`

### 1.2 Three standalone outputs — additive

Each is a subset of the pipeline that already runs, stopping early rather than
doing anything new.

- [x] **Subtitles** — stops after Whisper · 32s
- [x] **Translated subtitles** — stops after NLLB · 36s
- [x] **Audio dub** — stops before Wav2Lip · 136s, for a speaker not on camera

Implemented as a `kind` on the job, the way `voiceover` already is, so status,
cancellation, projects and exports keep working unchanged.

*Touches:* `app.py`, `dubbing.py`

### 1.3 Clearing a phrase — additive

- [x] `DELETE /jobs/{id}/segments/{i}` — holds silence for the slot
- [x] Undoable like any other edit; marks the picture stale

Cut needs somewhere for the audio to go. Empty text is currently rejected
outright, so this is a distinct operation rather than a loosened validation.

*Touches:* `app.py`

---

## Phase 2 — Studio shell  ·  done

Rearranging panels that already exist. No panel is rewritten; each moves to the
page it belongs on.

### 2.1 Page bar along the bottom — layout

- [x] Media · Edit · Deliver, always visible, in the existing console chrome
- [x] Pages that cannot apply yet are dimmed with a reason on hover — Edit
      before a render, Deliver before a clip
- [x] Top tab strip keeps Projects and Studio only

*Touches:* `console.tsx`, `Studio.tsx`

### 2.2 Output presets on Deliver — replaces a control

- [x] Dub/Voice-over segmented control becomes five presets: Dub, Audio dub,
      Subtitles, Translated subtitles, Voice-over
- [x] Each states what it runs and roughly what it costs, so the cheap ones are
      visibly cheap

*Touches:* `Studio.tsx`, `api.ts`, `pipeline.ts`

---

## Phase 3 — Render semantics  ·  done

The change requested first, and the one most likely to break something, so it
lands after the shell is stable.

### 3.1 Render stops meaning "start over" — behaviour change

The button reads the project's state instead of always submitting a new job.

- [x] Nothing rendered yet → **Render**, full pipeline
- [x] Rendered, audio edited since → **Re-render picture**, lip sync only
- [x] Rendered and current → **disabled**, labelled up to date
- [x] Starting from scratch moves to an explicit **Render from source** behind a
      confirm, because it discards every phrase edit

That is exactly what the button does today without saying so.

*Touches:* `Studio.tsx`

### 3.2 The finished output, in the window — additive

- [x] Deliver shows the result rather than a path
- [x] Video playing inline for a dub; audio player for a voice-over or audio
      dub; subtitle text for a subtitle job
- [x] File location shown (reveal-in-folder not added)

The player exists already; it has never had a page of its own to sit on.

*Touches:* `Studio.tsx`

---

## Phase 4 — Timeline editing  ·  done

Built last: depends on 1.3 for cut, and on the Edit page existing.

### 4.1 Cut, copy and paste on phrases — additive

- [x] **Copy** takes a phrase's words and seed
- [x] **Paste** puts them on the selected phrase and re-speaks it in that slot,
      through the existing revise endpoint — so the length check, the number
      guard and undo all apply unchanged
- [x] **Cut** copies and then clears, using 1.3
- [x] Bound to ⌘X, ⌘C, ⌘V, Delete and ⌘Z onto the existing undo stack
- [x] Never captured while a text field has focus

*Touches:* `timeline.tsx`, `Studio.tsx`

### 4.2 Phrases show whether they fit — additive

- [x] Colour each block by spoken length against its slot: fits, tight, overruns
- [x] Expose spoken seconds in the segments response

The single most useful thing the app knows about a dub, and it currently takes a
click per phrase to find out.

*Touches:* `timeline.tsx`, `app.py`

---

## Must not break

Checked against `scripts/selftest.py` after every phase. All green at 94
checks, voice match 0.720 — unchanged from the 67-check baseline.

- [x] Dub a video end to end
- [x] Voice cloning quality (0.720 baseline)
- [x] Voice-over from a script
- [x] Editing a phrase's words
- [x] Re-rolling a delivery with a new seed
- [x] Undo, including audio restoration
- [x] Choosing a reference window
- [x] Re-rendering the picture
- [x] Trimming with in and out points
- [x] The music bed
- [x] All six export formats
- [x] Cancelling a render
- [x] Projects persisting and reopening
- [x] Fitting a line with either provider
- [x] Ollama and Groq both reachable
- [x] The desktop app finding its checkout

---

## Sequencing

Phase 0 is irreversible and independent. Phase 1 is what phases 2 and 4 are
built on. Phase 3 changes behaviour that is relied on, so it lands once the
shell around it has stopped moving.

**Item 0.2 is the only one that does not start without an answer.**
