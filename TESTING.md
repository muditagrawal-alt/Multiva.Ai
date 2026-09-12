# Testing Multiva end to end

A walkthrough from first launch to a finished dub, in a sandbox that cannot
touch your real settings or your existing projects.

Everything happens in `~/Multiva-Test/`. Delete that folder and the test never
happened.

**Roughly 30 minutes**, most of it waiting on one render.

---

## Before you start

```bash
cd /Users/muditagrawal/Projects/Multiva.Ai
```

Nothing else is required. The sandbox shares the model cache, so nothing is
re-downloaded.

If you want the script model (optional — only used to shorten a line that
overruns):

```bash
ollama serve &          # if it is not already running
ollama pull qwen2.5:7b
```

---

## Part 1 — First run

```bash
./run.sh --sandbox ~/Multiva-Test
```

**Expect:** a few lines in the terminal, then the desktop window opens on the
**Setup** screen.

Check as you go:

- [ ] The terminal says `First-run mode. Your real settings are untouched.`
- [ ] The window opens on **Setup**, not Projects — this is what a new user sees
- [ ] **There is no Studio tab** in the top bar. There is no project yet, so
      there is nowhere for it to lead
- [ ] Under **Models on this machine**, all 7 show a green tick and
      "everything is here"
- [ ] Each of the four stages (speech recognition, translation, lip sync, voice)
      shows its options with sizes
- [ ] **Where finished videos go** points at `~/Multiva-Test/projects`, not your
      real Movies folder

Now press **Finish setup**.

- [ ] You land on **Projects**, and it is empty
- [ ] Still no Studio tab

> If the models showed as missing, press **Download everything missing** and
> watch the progress bar. It reports real megabytes, not a timer, and it
> resumes if interrupted.

---

## Part 2 — Your first project

Press **New project**.

- [ ] A dialog asks for a name
- [ ] Type `First test` and press **Create and open**
- [ ] The studio opens, and **now** the Studio tab exists in the top bar
- [ ] The title bar shows `First test`

You are on the **Media** page. Along the bottom you should see
**Media · Edit · Deliver** — Edit and Deliver are dimmed, because there is
nothing to edit and no clip to deliver yet.

Press **Import** and choose:

```
~/Multiva-Test/clips/sample-english-17s.mp4
```

- [ ] A thumbnail appears in the media list showing an actual frame, not a
      black box
- [ ] Duration reads about 17 seconds
- [ ] **Deliver** stops being dimmed

---

## Part 3 — Dub it

Go to **Deliver**. You should see five outputs, each saying what it runs.

Pick **Dub video**, leave the output language on Hindi, press **Render dub**.

**This takes about 4–6 minutes.** Watch the timeline: the progress bar walks
through transcribing, translating, cloning the voice, then lip sync.

- [ ] Stages advance in order and the frame counter moves during lip sync
- [ ] When it finishes you land on **Deliver** automatically, with the video
      playing in the viewer
- [ ] **Voice match** shows a percentage — 70% or higher is good
- [ ] **Saved to** shows a path inside `~/Multiva-Test/projects`
- [ ] The file is really there:

```bash
ls -lh ~/Multiva-Test/projects/
```

- [ ] Play it. The Hindi should be in the speaker's voice, and the mouth should
      roughly track the words

---

## Part 4 — Edit a phrase

Go to **Edit**. The timeline now has coloured phrase blocks.

- [ ] Each block has a stripe underneath: green fits its slot, amber is tight,
      red overruns
- [ ] Click one. The inspector shows its words, its slot, and how long it
      actually takes to say

Try each of these:

- [ ] **Change the words** and press Re-speak. The audio updates in seconds
- [ ] **Re-roll** (a new seed) gives a different take of the same words
- [ ] **⌘C** then click another phrase and **⌘V** — the words move across and
      are spoken in the new slot
- [ ] **⌘X** silences a phrase. Its block goes hatched
- [ ] **⌘Z** undoes it, and the *original take* comes back — not a fresh
      synthesis of the same words

After any edit:

- [ ] An amber note says the picture still carries the previous take
- [ ] The main button now reads **Re-render picture**, not Render — it will
      redo only lip sync, a couple of minutes rather than the whole pipeline
- [ ] Press it. When it finishes, the button reads **Up to date** and is disabled

**This is the behaviour that used to be wrong** — pressing Render after an edit
re-ran everything from scratch.

---

## Part 5 — The cheap outputs

Make a new project (`Subtitle test`), import the same clip, go to **Deliver**.

- [ ] **Subtitles** finishes in about 30 seconds, not minutes
- [ ] **Translated subtitles** takes about the same
- [ ] Under Export, download `dub.srt` and open it — real Hindi with timings

These exist so an SRT does not cost a full pipeline run.

---

## Part 6 — Settings

Click the **gear** in the top right.

- [ ] It opens over your work, not as a separate page
- [ ] **Escape** closes it
- [ ] Models, script model, locations, storage, pipeline knobs, about
- [ ] Under **Storage**, "working files hold" shows a size, and
      **Clear abandoned runs now** works
- [ ] Change the lip sync model to something else in the Models dropdown, then
      reopen settings — the **Lip sync batch** and **Face detection batch**
      knobs should disappear, because they belong to Wav2Lip. Change it back
      and they return

---

## Part 7 — It remembers

Quit the app (Ctrl-C in the terminal), then:

```bash
./run.sh --sandbox ~/Multiva-Test
```

- [ ] It goes **straight to Projects** — no setup screen this time
- [ ] Both projects are listed, with their names and languages
- [ ] Open one. It reopens on Deliver with the finished video

---

## Part 8 — Your real setup is untouched

```bash
./run.sh
```

- [ ] Your own projects are there, not the test ones
- [ ] Output folder is your real one

---

## When you are done

```bash
rm -rf ~/Multiva-Test
```

Models are shared, so nothing you need is deleted.

---

## What to report back

For anything that goes wrong, the useful details are:

1. **Which step** (the part number above)
2. **What you saw** versus what this file said to expect
3. **The terminal output** — the engine prints every stage there
4. If a render failed, the job's error line from the studio

The most useful thing you can tell me is anything that felt confusing rather
than broken. Crashes are easy to find; a screen that does not explain itself is
not.

---

## Known limits, so you do not report them as bugs

- **English, Spanish, French and the other non-Indian languages are marked
  unavailable.** XTTS is not installed by default: its licence is
  non-commercial and its dependency tree is enormous. The twelve Indian
  languages work out of the box. To enable the rest:
  `pip install "TTS>=0.22.0"`. Subtitles work in every language regardless.
- **About 10× realtime.** The 46-second clip takes about 8 minutes; a
  2-minute video about 20. Voice synthesis is two thirds of it.
- **One heavy job at a time.** Editing a phrase while a render runs will say
  the engine is busy rather than queueing.
- **Talking-head video only.** Wav2Lip needs a visible, roughly front-facing
  face.
- **One voice per video.** Whisper does not diarize.
