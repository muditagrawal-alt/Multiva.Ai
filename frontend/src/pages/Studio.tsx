/* ===========================================================================
   Studio — the editor workspace.

   Laid out the way a non-linear editor is: a media pool on the left, a viewer
   in the middle, an inspector on the right, a timeline underneath and a
   status bar along the bottom. Panels are separated by a 1px gutter and the
   window never scrolls as a page.

   This is not decoration. The pipeline already produces the things those
   panels exist to show — a source clip, a reference clip, a rendered track
   with known segment boundaries, and eight named stages — and a docked
   layout shows all of them at once instead of one at a time down a page.
   =========================================================================== */

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Play, Pause, Stop, SkipBack, FilePlus, Warning, ArrowSquareOut, Trash,
  DownloadSimple, MusicNotes, Shuffle, X,
} from "@phosphor-icons/react";
import {
  getLanguages, getHealth, submitVideo, submitVoiceover, cancelJob, getJob,
  getPhrases, revisePhrase, phraseAudioUrl, rerenderVideo,
  getReferenceWindows, chooseReference,
  EXPORTS, exportUrl, langName,
  type Language, type JobStatus, type Health, type Phrase,
  type ReferenceWindow,
} from "@/lib/api";
import {
  Panel, Tool, Row, Sel, Sub, Stat, Lamp, TitleBar, StatusBar, timecode, bytes,
} from "@/components/console";
import { Timeline } from "@/components/timeline";
import { CloneProgress } from "@/components/progress";
import { stagesFor, readProgress, percent, type JobKind } from "@/lib/pipeline";
import { cx } from "@/lib/cx";

const MAX_MB = 200;

const FALLBACK: Language[] = [
  { code: "hi", name: "Hindi", engine: "indicf5" },
  { code: "mr", name: "Marathi", engine: "indicf5" },
  { code: "ta", name: "Tamil", engine: "indicf5" },
  { code: "te", name: "Telugu", engine: "indicf5" },
  { code: "kn", name: "Kannada", engine: "indicf5" },
  { code: "en", name: "English", engine: "xtts" },
];

type View = "idle" | "working" | "done" | "failed" | "cancelled";

export default function Studio() {
  const [langs, setLangs] = useState<Language[] | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [fileError, setFileError] = useState("");
  const [source, setSource] = useState("auto");
  const [target, setTarget] = useState("hi");
  const [view, setView] = useState<View>("idle");
  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  // Two jobs share this workspace: dub a video, or speak a typed script in the
  // same cloned voice. They differ only in what the inspector asks for.
  const [mode, setMode] = useState<JobKind>("dub");
  const [script, setScript] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const jobId = useRef<string>("");

  // Revising a finished dub: the phrase timeline, which one is selected, and
  // the draft text being typed into it.
  const [phrases, setPhrases] = useState<Phrase[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [phraseBusy, setPhraseBusy] = useState(false);
  const [phraseNote, setPhraseNote] = useState("");
  const [windows, setWindows] = useState<ReferenceWindow[] | null>(null);
  const [stale, setStale] = useState(false);
  const phrasePlayer = useRef<HTMLAudioElement | null>(null);

  // Pre-render options on the source.
  const [trimIn, setTrimIn] = useState<number | null>(null);
  const [trimOut, setTrimOut] = useState<number | null>(null);
  const [music, setMusic] = useState<File | null>(null);
  const [musicGain, setMusicGain] = useState(-18);

  // A project reopened from the Projects screen has no File object behind it,
  // only what the manifest recorded.
  const [opened, setOpened] = useState<{ name: string } | null>(null);
  const [params] = useSearchParams();

  const [tab, setTab] = useState<"source" | "dub">("source");
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  // The source clip's own length, which the media pool reports. Distinct
  // from `duration`, which follows whatever the transport is playing.
  const [clipDuration, setClipDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  // Either a <video> for a dub or an <audio> for a voice-over; the
  // transport only uses HTMLMediaElement members, so it drives both.
  const video = useRef<HTMLMediaElement | null>(null);
  const poll = useRef<number | null>(null);
  const clock = useRef<number | null>(null);

  /* --- lifecycle ---------------------------------------------------------- */

  useEffect(() => {
    getLanguages().then((l) => setLangs(l.length ? l : FALLBACK)).catch(() => setLangs(FALLBACK));
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  // Reopen a saved project. Its audio, phrase cache and rendered video are
  // already on disk; only the index had to be restored.
  useEffect(() => {
    const id = params.get("job");
    if (!id) return;
    let live = true;
    (async () => {
      try {
        const s = await getJob(id);
        if (!live) return;
        jobId.current = id;
        setJob(s);
        setMode(s.kind === "voiceover" ? "voiceover" : "dub");
        setOpened({ name: s.job_id });
        setStale(Boolean(s.video_stale));
        setView(s.status === "done" ? "done" : "idle");
        setTab(s.url || s.dub_audio ? "dub" : "source");
        if (s.editable) {
          getPhrases(id).then((t) => live && setPhrases(t.segments)).catch(() => {});
          getReferenceWindows(id).then((r) => live && setWindows(r.candidates)).catch(() => {});
        }
      } catch (err) {
        if (live) { setError((err as Error).message); setView("failed"); }
      }
    })();
    return () => { live = false; };
  }, [params]);

  useEffect(() => () => {
    if (poll.current) clearInterval(poll.current);
    if (clock.current) clearInterval(clock.current);
  }, []);

  // Object URLs leak one blob per pick unless the previous one is revoked.
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  /* --- transport ---------------------------------------------------------- */

  const seek = useCallback((s: number) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = s;
    setCurrent(s);
  }, []);

  const toggle = useCallback(() => {
    const v = video.current;
    if (!v || !v.src) return;
    if (v.paused) void v.play(); else v.pause();
  }, []);

  const stop = useCallback(() => {
    const v = video.current;
    if (!v) return;
    v.pause();
    v.currentTime = 0;
    setCurrent(0);
  }, []);

  // Space toggles playback, as it does in every editor — but not while the
  // caret is in a control, where space means "activate".
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(el.tagName)) return;
      if (e.code === "Space") { e.preventDefault(); toggle(); }
      if (e.code === "Home") { e.preventDefault(); seek(0); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle, seek]);

  /* --- media -------------------------------------------------------------- */

  const pick = useCallback((f: File | null) => {
    setFileError("");
    if (!f) return;
    if (f.size > MAX_MB * 1024 * 1024) {
      setFileError(`${(f.size / 1048576).toFixed(0)} MB exceeds the ${MAX_MB} MB limit.`);
      return;
    }
    setFile(f);
    setTab("source");
    setPreview((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(f); });
  }, []);

  function reset() {
    if (poll.current) clearInterval(poll.current);
    if (clock.current) clearInterval(clock.current);
    setView("idle"); setJob(null); setError(""); setElapsed(0);
    setFile(null); setTab("source"); setCancelling(false); setClipDuration(0);
    setPhrases(null); setSelected(null); setWindows(null); setStale(false);
    setOpened(null);
    setTrimIn(null); setTrimOut(null); setMusic(null); setPhraseNote("");
    setPreview((old) => { if (old) URL.revokeObjectURL(old); return ""; });
  }

  async function stopRun() {
    if (!jobId.current) return;
    setCancelling(true);
    try {
      await cancelJob(jobId.current);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  /** Follow a running job to its conclusion. Shared by render, re-clone and
      re-render, which all produce the same kind of job. */
  function watch(job_id: string) {
    if (poll.current) clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      try {
        const s = await getJob(job_id);
        setJob(s);
        if (s.status === "done") {
          clearInterval(poll.current!);
          clearInterval(clock.current!);
          setView("done");
          setTab("dub");
          setStale(Boolean(s.video_stale));
          if (s.editable) {
            getPhrases(job_id).then((t) => setPhrases(t.segments)).catch(() => setPhrases(null));
            getReferenceWindows(job_id).then((r) => setWindows(r.candidates)).catch(() => setWindows(null));
          }
          getHealth().then(setHealth).catch(() => {});
        }
        if (s.status === "failed") {
          clearInterval(poll.current!);
          clearInterval(clock.current!);
          setError(s.error ?? "The run failed without reporting a reason.");
          setView("failed");
        }
        if (s.status === "cancelled") {
          clearInterval(poll.current!);
          clearInterval(clock.current!);
          setView("cancelled");
          setCancelling(false);
        }
      } catch (err) {
        clearInterval(poll.current!);
        clearInterval(clock.current!);
        setError((err as Error).message);
        setView("failed");
      }
    }, 1600);
  }

  function selectPhrase(index: number) {
    const phrase = phrases?.find((ph) => ph.index === index);
    if (!phrase) return;
    setSelected(index);
    setDraft(phrase.text);
    setPhraseNote("");
    seek(phrase.start);
  }

  function playPhrase(index: number) {
    if (!jobId.current) return;
    // A phrase is auditioned on its own element so it does not disturb the
    // viewer's playhead.
    phrasePlayer.current?.pause();
    const audio = new Audio(phraseAudioUrl(jobId.current, index));
    phrasePlayer.current = audio;
    void audio.play().catch(() => setPhraseNote("That phrase has no audio yet."));
  }

  async function applyPhrase(body: { text?: string; seed?: number }) {
    if (selected == null || !jobId.current) return;
    setPhraseBusy(true);
    setPhraseNote("");
    try {
      const result = await revisePhrase(jobId.current, selected, body);
      setPhrases((list) =>
        list?.map((ph) =>
          ph.index === result.index
            ? { ...ph, text: result.text, seed: result.seed ?? null }
            : ph
        ) ?? null
      );
      setDraft(result.text);
      setStale(true);
      setPhraseNote(
        result.overruns
          ? `Spoken in ${result.spoken_seconds.toFixed(2)}s, which fills the ${result.slot_seconds.toFixed(2)}s slot. It was shortened to fit.`
          : `Spoken in ${result.spoken_seconds.toFixed(2)}s of a ${result.slot_seconds.toFixed(2)}s slot.`
      );
      // The rebuilt track has the same URL, so the waveform needs a nudge.
      setJob((j) => (j ? { ...j, dub_audio: `${j.dub_audio}?v=${Date.now()}` } : j));
    } catch (err) {
      setPhraseNote((err as Error).message);
    } finally {
      setPhraseBusy(false);
    }
  }

  async function applyReference(w: ReferenceWindow) {
    if (!jobId.current) return;
    setView("working");
    setElapsed(0);
    setPhraseNote("");
    clock.current = window.setInterval(() => setElapsed((n) => n + 1), 1000);
    try {
      await chooseReference(jobId.current, w.start, w.duration);
      watch(jobId.current);
    } catch (err) {
      if (clock.current) clearInterval(clock.current);
      setError((err as Error).message);
      setView("failed");
    }
  }

  async function rerender() {
    if (!jobId.current) return;
    setView("working");
    setElapsed(0);
    clock.current = window.setInterval(() => setElapsed((n) => n + 1), 1000);
    try {
      await rerenderVideo(jobId.current);
      watch(jobId.current);
    } catch (err) {
      if (clock.current) clearInterval(clock.current);
      setError((err as Error).message);
      setView("failed");
    }
  }

  async function render() {
    if (!file) { setFileError("Import a clip first."); return; }
    if (mode === "voiceover" && !script.trim()) {
      setFileError("Write a script for the voice to read.");
      return;
    }
    setView("working"); setJob(null); setError(""); setElapsed(0);
    setCancelling(false);

    clock.current = window.setInterval(() => setElapsed((n) => n + 1), 1000);

    try {
      const { job_id } = mode === "voiceover"
        ? await submitVoiceover(file, script, target)
        : await submitVideo(file, source, target, {
            trimStart: trimIn,
            trimEnd: trimOut,
            music,
            musicGain,
          });
      jobId.current = job_id;
      watch(job_id);
    } catch (err) {
      if (clock.current) clearInterval(clock.current);
      setError((err as Error).message);
      setView("failed");
    }
  }

  /* --- derived ------------------------------------------------------------ */

  const kind: JobKind = job?.kind ?? mode;
  const stages = stagesFor(kind);
  const progress = readProgress(job?.step, view === "done" ? "done" : undefined, kind);
  const stageIndex = progress.index;
  const engine = (langs ?? FALLBACK).find((l) => l.code === target)?.engine ?? "—";
  const src = tab === "dub" ? (job?.url ?? "") : preview;
  // A voice-over produces audio, not a video, so the dub tab plays a track.
  // In and out points are positions in the SOURCE clip. Once the dub is in the
  // viewer the ruler measures the output instead, where those numbers mean
  // nothing, so the markers are only drawn over the source.
  const badRange =
    trimIn != null && trimOut != null && trimOut - trimIn < 1;
  const selectedPhrase = phrases?.find((ph) => ph.index === selected) ?? null;
  const voiceTrack = kind === "voiceover" && tab === "dub" ? (job?.dub_audio ?? "") : "";

  const state: { lamp: "idle" | "run" | "good" | "bad"; text: string } =
    view === "working"
      ? { lamp: "run", text: cancelling ? "Stopping" : stageIndex >= 0 ? stages[stageIndex].label : "Queued" }
      : view === "done" ? { lamp: "good", text: "Render complete" }
      : view === "failed" ? { lamp: "bad", text: "Render failed" }
      : view === "cancelled" ? { lamp: "idle", text: "Stopped" }
      : { lamp: "idle", text: file ? "Ready to render" : "No clip imported" };

  /* --- render ------------------------------------------------------------- */

  return (
    <div
      className="console grid"
      style={{ gridTemplateRows: "30px minmax(0,1fr) 170px 22px" }}
    >
      <TitleBar
        active="dub"
        right={
          <div className="flex items-center gap-0.5" role="group" aria-label="Job type">
            <Tool
              active={mode === "dub"}
              onClick={() => setMode("dub")}
              disabled={view === "working"}
            >
              Dub video
            </Tool>
            <Tool
              active={mode === "voiceover"}
              onClick={() => setMode("voiceover")}
              disabled={view === "working"}
            >
              Voice-over
            </Tool>
          </div>
        }
      />

      {/* ---------------- three-panel row ---------------- */}
      <div
        className="grid min-h-0 gap-px bg-c-edge"
        style={{ gridTemplateColumns: "236px minmax(0,1fr) 262px" }}
      >
        {/* ---- media pool ---- */}
        <Panel
          title="Media"
          right={
            <Tool
              icon={<FilePlus size={12} />}
              onClick={() => document.getElementById("clip")?.click()}
              title="Import a clip"
            >
              Import
            </Tool>
          }
        >
          <input
            id="clip" type="file" className="sr-only"
            accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
          />

          {!file && !opened ? (
            <label
              htmlFor="clip"
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files?.[0] ?? null); }}
              className={cx(
                "m-2 grid cursor-pointer place-items-center gap-1.5 rounded-[2px] border border-dashed px-3 py-8 text-center",
                "transition-colors duration-150",
                dragging ? "border-c-accent bg-c-accent-dim/25" : "border-c-rule hover:border-c-accent"
              )}
            >
              <FilePlus size={18} className="text-c-mute" />
              <span className="text-[11px] text-c-dim">Drop a clip, or click to browse</span>
              <span className="text-[10px] leading-snug text-c-mute">
                MP4, MOV, WebM · up to {MAX_MB} MB<br />One front-facing speaker
              </span>
            </label>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setTab("source")}
                className={cx(
                  "flex w-full items-center gap-2 border-b border-c-edge px-2 py-1.5 text-left transition-colors",
                  tab === "source" ? "bg-c-accent-dim/40" : "hover:bg-c-hover"
                )}
              >
                <div className="h-[26px] w-[46px] shrink-0 overflow-hidden rounded-[2px] bg-black">
                  {preview ? (
                    <video src={preview} muted playsInline preload="metadata" className="h-full w-full object-cover" />
                  ) : job?.url ? (
                    <video src={job.url} muted playsInline preload="metadata" className="h-full w-full object-cover" />
                  ) : null}
                </div>
                <span className="console-text min-w-0 flex-1 truncate text-[11px] text-c-text">
                  {file?.name ?? job?.job_id ?? "project"}
                </span>
              </button>

              {job?.url && (
                <button
                  type="button"
                  onClick={() => setTab("dub")}
                  className={cx(
                    "flex w-full items-center gap-2 border-b border-c-edge px-2 py-1.5 text-left transition-colors",
                    tab === "dub" ? "bg-c-accent-dim/40" : "hover:bg-c-hover"
                  )}
                >
                  <div className="grid h-[26px] w-[46px] shrink-0 place-items-center rounded-[2px] bg-black text-[9px] text-c-good">
                    DUB
                  </div>
                  <span className="min-w-0 flex-1 truncate text-[11px] text-c-text">
                    {target}_{file?.name ?? "output"}
                  </span>
                </button>
              )}

              <Sub>Clip</Sub>
              <Stat k="Duration" v={timecode(clipDuration || job?.video_duration || 0)} />
              {file && <Stat k="Size" v={bytes(file.size)} />}
              {file && <Stat k="Type" v={file.type || "unknown"} />}

              <div className="p-2">
                <Tool icon={<Trash size={12} />} onClick={reset} className="w-full">
                  Clear
                </Tool>
              </div>
            </>
          )}

          {fileError && (
            <p role="alert" className="mx-2 mb-2 flex items-start gap-1.5 rounded-[2px] bg-[#3a1f1c] px-2 py-1.5 text-[10px] leading-snug text-c-bad">
              <Warning size={12} className="mt-px shrink-0" />
              {fileError}
            </p>
          )}
        </Panel>

        {/* ---- viewer ---- */}
        <Panel
          title="Viewer"
          scroll={false}
          bodyClassName="flex flex-col"
          right={
            <>
              <Tool active={tab === "source"} onClick={() => setTab("source")}>Source</Tool>
              <Tool
                active={tab === "dub"}
                onClick={() => setTab("dub")}
                disabled={!job?.url && !job?.dub_audio}
              >
                {mode === "voiceover" ? "Voice-over" : "Dub"}
              </Tool>
            </>
          }
        >
          <div className="relative min-h-0 flex-1 bg-c-void">
            {voiceTrack ? (
              <div className="absolute inset-0 grid place-content-center justify-items-center gap-2 px-6 text-center">
                <audio
                  ref={video as React.RefObject<HTMLAudioElement>}
                  key={voiceTrack}
                  src={voiceTrack}
                  className="sr-only"
                  onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
                  onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                  onEnded={() => setPlaying(false)}
                />
                <span className="text-[10px] uppercase tracking-[0.14em] text-c-mute">
                  Voice-over
                </span>
                <span className="tnum text-[22px] text-c-text">{timecode(duration)}</span>
                <span className="max-w-[38ch] text-[11px] leading-relaxed text-c-dim">
                  {langName(target)} in the cloned voice. Use the transport below,
                  or scrub the A2 track.
                </span>
              </div>
            ) : src ? (
              <video
                ref={video as React.RefObject<HTMLVideoElement>}
                key={src}
                src={src}
                playsInline
                className="absolute inset-0 h-full w-full object-contain"
                onLoadedMetadata={(e) => {
                  const d = e.currentTarget.duration || 0;
                  setDuration(d);
                  if (tab === "source") setClipDuration(d);
                }}
                onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onEnded={() => setPlaying(false)}
              />
            ) : (
              <div className="absolute inset-0 grid place-content-center justify-items-center gap-1 text-center">
                <span className="text-[11px] text-c-dim">No clip in the viewer</span>
                <span className="max-w-[34ch] text-[10px] leading-relaxed text-c-mute">
                  Import a video on the left, choose an output language, then render.
                  Nothing leaves this machine.
                </span>
              </div>
            )}
          </div>

          {/* transport */}
          <div className="raised flex h-[28px] shrink-0 items-center gap-1 border-t border-c-edge px-2">
            <Tool icon={<SkipBack size={12} weight="fill" />} onClick={() => seek(0)} disabled={!src && !voiceTrack} aria-label="Go to start" />
            <Tool
              icon={playing ? <Pause size={12} weight="fill" /> : <Play size={12} weight="fill" />}
              onClick={toggle}
              disabled={!src && !voiceTrack}
              aria-label={playing ? "Pause" : "Play"}
            />
            <Tool icon={<Stop size={12} weight="fill" />} onClick={stop} disabled={!src && !voiceTrack} aria-label="Stop" />

            <span className="tnum ml-2 text-[11px] text-c-text">{timecode(current)}</span>
            <span className="text-[11px] text-c-mute">/</span>
            <span className="tnum text-[11px] text-c-mute">{timecode(duration)}</span>

            <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-c-mute">
              {voiceTrack ? "Voice-over" : tab === "dub" ? "Rendered dub" : "Source"}
            </span>
          </div>
        </Panel>

        {/* ---- inspector ---- */}
        <Panel title="Inspector" bodyClassName="flex flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto py-1">
            <Sub>Input</Sub>
            {mode === "dub" ? (
              <Row label="Spoken" hint="Override only when detection gets it wrong">
                <Sel value={source} onChange={(e) => setSource(e.target.value)}>
                  <option value="auto">Detect automatically</option>
                  {(langs ?? FALLBACK).map((l) => (
                    <option key={l.code} value={l.code}>{l.name}</option>
                  ))}
                </Sel>
              </Row>
            ) : (
              <p className="px-2.5 py-1 text-[10px] leading-relaxed text-c-mute">
                The clip on the left is only the voice to copy. Its words are
                ignored; the script below is what gets spoken.
              </p>
            )}

            {mode === "voiceover" && (
              <>
                <Sub>Script</Sub>
                <div className="px-2.5 py-1.5">
                  <label htmlFor="script" className="sr-only">Script to speak</label>
                  <textarea
                    id="script"
                    value={script}
                    onChange={(e) => setScript(e.target.value)}
                    rows={9}
                    spellCheck={false}
                    placeholder="Type what the voice should say. A blank line becomes a longer pause."
                    className="console-text recessed w-full resize-y rounded-[2px] border border-c-edge p-2 font-indic text-[12px] leading-relaxed text-c-text placeholder:text-c-mute"
                  />
                  <p className="tnum mt-1 text-right text-[10px] text-c-mute">
                    {script.trim().length} characters
                  </p>
                </div>
              </>
            )}

            <Sub>Output</Sub>
            <Row label="Language">
              <Sel value={target} onChange={(e) => setTarget(e.target.value)} disabled={langs === null}>
                {(langs ?? FALLBACK).map((l) => (
                  <option key={l.code} value={l.code}>{l.name}</option>
                ))}
              </Sel>
            </Row>
            <Stat k="Voice model" v={engine} />

            {mode === "dub" && (
              <>
                <Sub>Source range</Sub>
                <div className="flex items-center gap-1.5 px-2.5 py-1.5">
                  {/* Set from the playhead rather than typed: the ruler is
                      already scrubbable, so the clip itself is the control. */}
                  <Tool onClick={() => setTrimIn(current)} disabled={!file}>Set in</Tool>
                  <Tool onClick={() => setTrimOut(current)} disabled={!file}>Set out</Tool>
                  {(trimIn != null || trimOut != null) && (
                    <Tool
                      onClick={() => { setTrimIn(null); setTrimOut(null); }}
                      title="Use the whole clip"
                      aria-label="Clear in and out points"
                      icon={<X size={11} />}
                    />
                  )}
                </div>
                <Stat k="In" v={trimIn != null ? timecode(trimIn) : "start"} />
                <Stat k="Out" v={trimOut != null ? timecode(trimOut) : "end"} />
                {trimIn != null && trimOut != null && trimOut - trimIn < 1 && (
                  <p className="px-2.5 py-1 text-[10px] leading-relaxed text-c-bad">
                    That range is under a second. Widen it before rendering.
                  </p>
                )}

                <Sub>Music</Sub>
                <div className="px-2.5 py-1.5">
                  <input
                    id="music" type="file" className="sr-only"
                    accept="audio/*,.mp3,.wav,.m4a,.aac"
                    onChange={(e) => setMusic(e.target.files?.[0] ?? null)}
                  />
                  <label
                    htmlFor="music"
                    className="raised flex h-[22px] cursor-pointer items-center gap-1.5 rounded-[2px] border border-c-rule px-2 text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-text"
                  >
                    <MusicNotes size={11} />
                    <span className="min-w-0 flex-1 truncate">
                      {music ? music.name : "Add a bed"}
                    </span>
                  </label>
                </div>
                {music && (
                  <>
                    <Row label="Level">
                      <input
                        type="range" min={-40} max={-6} step={1}
                        value={musicGain}
                        onChange={(e) => setMusicGain(Number(e.target.value))}
                        className="w-full accent-c-accent"
                        aria-label="Music level in decibels"
                      />
                    </Row>
                    <Stat k="" v={`${musicGain} dB under the voice`} />
                    <div className="px-2.5 pb-1.5">
                      <Tool onClick={() => setMusic(null)} className="w-full">
                        Remove bed
                      </Tool>
                    </div>
                  </>
                )}
              </>
            )}

            <Sub>Run</Sub>
            {view !== "idle" && (
              <CloneProgress
                progress={progress}
                view={view}
                stages={stages}
                label={kind === "voiceover" ? "Voice-over rendered" : "Voice cloned and dub rendered"}
                error={error}
                onRetry={render}
                onCancel={view === "working" && !cancelling ? stopRun : undefined}
              />
            )}
            {job ? (
              <>
                <Stat k="Job" v={job.job_id.slice(0, 8)} />
                <Stat k="Detected" v={job.source_language ?? "—"} />
                <Stat k="Segments" v={job.segment_count ?? "—"} />
                <Stat k="Reference" v={job.reference_seconds ? `${job.reference_seconds}s` : "—"} />
                {job.voice_match && (
                  <Stat
                    k="Voice match"
                    v={`${Math.round(job.voice_match.score * 100)}%`}
                    tone={job.voice_match.score >= 0.7 ? "good" : job.voice_match.score >= 0.45 ? "warn" : "bad"}
                  />
                )}
                {job.sync && (
                  <Stat
                    k="A/V drift"
                    v={job.sync.delta != null ? `${job.sync.delta > 0 ? "+" : ""}${job.sync.delta.toFixed(2)}s` : job.sync.reason}
                    tone={job.sync.ok ? "good" : "warn"}
                  />
                )}
              </>
            ) : view === "idle" ? (
              <p className="px-2.5 py-1 text-[10px] leading-relaxed text-c-mute">
                Populated once a render starts.
              </p>
            ) : null}

            {stale && view !== "working" && (
              <div className="mx-2.5 my-1.5 rounded-[2px] border border-[#7a4410] bg-[#2a1d10] px-2 py-1.5">
                <p className="text-[10px] leading-relaxed text-c-accent">
                  The audio has changed. The picture still carries the previous take.
                </p>
                <Tool onClick={rerender} className="mt-1.5 w-full">
                  Re-render the video
                </Tool>
              </div>
            )}

            {phrases && selectedPhrase && (
              <>
                <Sub>Phrase {selectedPhrase.index + 1} of {phrases.length}</Sub>
                {selectedPhrase.source_text && (
                  <p className="console-text px-2.5 pb-1 pt-1 text-[10px] leading-relaxed text-c-mute">
                    {selectedPhrase.source_text}
                  </p>
                )}
                <div className="px-2.5 py-1">
                  <label htmlFor="phrase" className="sr-only">Phrase text</label>
                  <textarea
                    id="phrase"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={3}
                    spellCheck={false}
                    className="console-text recessed w-full resize-y rounded-[2px] border border-c-edge p-2 font-indic text-[12px] leading-relaxed text-c-text"
                  />
                  <div className="mt-1.5 flex gap-1.5">
                    <Tool
                      primary
                      onClick={() => applyPhrase({ text: draft })}
                      disabled={phraseBusy || !draft.trim() || draft === selectedPhrase.text}
                      className="flex-1"
                    >
                      {phraseBusy ? "Speaking…" : "Re-speak"}
                    </Tool>
                    <Tool
                      onClick={() => applyPhrase({ text: draft, seed: Math.floor(Math.random() * 100000) })}
                      disabled={phraseBusy}
                      title="Another take of the same words"
                      icon={<Shuffle size={11} />}
                    />
                    <Tool
                      onClick={() => playPhrase(selectedPhrase.index)}
                      title="Play this phrase"
                      icon={<Play size={11} weight="fill" />}
                    />
                  </div>
                </div>
                <Stat k="Slot" v={`${selectedPhrase.duration.toFixed(2)}s`} />
                {selectedPhrase.seed != null && (
                  <Stat k="Seed" v={selectedPhrase.seed} />
                )}
                {phraseNote && (
                  <p className="console-text px-2.5 pb-1.5 text-[10px] leading-relaxed text-c-dim">
                    {phraseNote}
                  </p>
                )}
              </>
            )}

            {windows && windows.length > 0 && view !== "working" && (
              <>
                <Sub>Reference window</Sub>
                <p className="px-2.5 pb-1 pt-1 text-[10px] leading-relaxed text-c-mute">
                  The voice is cloned from one window of the original. Pick a
                  different one and every phrase is spoken again.
                </p>
                <ul className="px-2.5 pb-1.5">
                  {windows.map((w) => (
                    <li key={`${w.start}-${w.duration}`} className="mb-1.5">
                      <div className="recessed rounded-[2px] border border-c-edge p-2">
                        <div className="flex items-baseline gap-2">
                          <span className="tnum text-[11px] text-c-text">
                            {timecode(w.start)}
                          </span>
                          <span className="tnum text-[10px] text-c-mute">
                            +{w.duration.toFixed(2)}s
                          </span>
                        </div>
                        <p className="console-text mt-1 line-clamp-2 text-[10px] leading-relaxed text-c-dim">
                          {w.text}
                        </p>
                        <div className="mt-1.5 flex gap-1.5">
                          <Tool onClick={() => { setTab("source"); seek(w.start); }}>
                            Hear it
                          </Tool>
                          <Tool onClick={() => applyReference(w)} className="flex-1">
                            Clone from this
                          </Tool>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}

            {view === "done" && job && (
              <>
                <Sub>Export</Sub>
                {job.has_transcript ? (
                  <div className="grid gap-1 px-2.5 py-1.5">
                    {EXPORTS.map((e) => (
                      <a
                        key={e.kind}
                        href={exportUrl(job.job_id, e.kind)}
                        download
                        className="raised flex h-[22px] items-center gap-1.5 rounded-[2px] px-2 text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-text active:translate-y-px"
                      >
                        <DownloadSimple size={11} /> {e.label}
                      </a>
                    ))}
                  </div>
                ) : (
                  <p className="px-2.5 py-1 text-[10px] leading-relaxed text-c-mute">
                    This run finished before transcript export existed, so it
                    has no stored timings. Re-render to enable it.
                  </p>
                )}
              </>
            )}

            <Sub>Pipeline</Sub>
            {/* A voice-over never translates and never touches the video, so
                listing those models here would be describing a run that does
                not happen. */}
            <Stat k="Transcribe" v="faster-whisper" />
            {mode === "dub" && <Stat k="Translate" v="NLLB-200" />}
            <Stat k="Clone" v={engine} />
            {mode === "dub" && <Stat k="Lip sync" v="Wav2Lip" />}
            <p className="px-2.5 pb-1 pt-1.5 text-[10px] leading-relaxed text-c-mute">
              {mode === "dub"
                ? "All four run on this machine. Synthesis is the slow stage, at roughly six times realtime."
                : "Both run on this machine. The reference clip is only read for its voice, never for its words."}
            </p>

            {job?.reference_text && (
              <>
                <Sub>Reference transcript</Sub>
                <p className="console-text px-2.5 py-1.5 text-[11px] leading-relaxed text-c-dim">
                  {job.reference_text}
                </p>
              </>
            )}

            {job?.translated_script && (
              <>
                <Sub>Translated script</Sub>
                <p className="console-text font-indic px-2.5 py-1.5 text-[12px] leading-relaxed text-c-dim">
                  {job.translated_script}
                </p>
              </>
            )}
          </div>

          {/* Actions stay pinned; they must not scroll out of reach. */}
          <div className="raised shrink-0 space-y-1.5 border-t border-c-edge p-2">
            <Tool
              primary
              onClick={render}
              disabled={!file || view === "working" || badRange}
              title={!file && opened ? "Import a clip to render something new" : undefined}
              className="h-[26px] w-full"
            >
              {view === "working" ? "Rendering…" : mode === "voiceover" ? "Render voice-over" : "Render dub"}
            </Tool>
            {job?.url && (
              <a
                href={job.url}
                download
                className="raised flex h-[22px] items-center justify-center gap-1.5 rounded-[2px] text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-text"
              >
                <ArrowSquareOut size={12} /> Open output
              </a>
            )}
          </div>
        </Panel>
      </div>

      {/* ---------------- timeline ---------------- */}
      <div className="border-t border-c-edge bg-c-panel">
        <div className="raised flex h-[26px] items-center gap-2 border-b border-c-edge pl-2.5 pr-2">
          <h2 className="text-[10px] font-medium uppercase tracking-[0.14em] text-c-dim">Timeline</h2>
          <span className="tnum ml-auto text-[11px] text-c-text">{timecode(current)}</span>
        </div>
        <Timeline
          duration={duration}
          current={current}
          onSeek={seek}
          sourceName={file?.name}
          referenceUrl={job?.reference_audio}
          referenceSeconds={job?.reference_seconds}
          dubUrl={job?.dub_audio}
          segments={job?.segment_count}
          stageIndex={stageIndex}
          stages={stages.map((st) => st.block)}
          finished={view === "done"}
          phrases={phrases ?? undefined}
          selected={selected}
          onSelectPhrase={selectPhrase}
          trimStart={tab === "source" ? trimIn : null}
          trimEnd={tab === "source" ? trimOut : null}
        />
      </div>

      {/* ---------------- status bar ---------------- */}
      <StatusBar>
        <span className="flex items-center gap-1.5 text-c-dim">
          <Lamp state={state.lamp} pulse={view === "working"} />
          {state.text}
        </span>

        {view === "working" && (
          <span className="tnum">
            {percent(progress.fraction)}% · stage {Math.max(0, stageIndex) + 1} of {stages.length}
          </span>
        )}

        <span className="ml-auto flex items-center gap-3">
          {view !== "idle" && <span className="tnum">elapsed {timecode(elapsed).slice(0, 8)}</span>}
          {health && <span className="tnum">jobs {health.active_jobs}</span>}
          <span>{health?.db ? "db ok" : "db offline"}</span>
        </span>
      </StatusBar>
    </div>
  );
}
