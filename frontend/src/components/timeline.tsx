/* ===========================================================================
   Timeline panel.

   Four tracks against one ruler:
     PROC   the eight pipeline stages, filling as the backend reports them
     V1     the source clip
     A1     the reference clip the voice was cloned from
     A2     the rendered dub, divided at its segment boundaries

   The ruler is seekable and the playhead is driven by the viewer's
   currentTime, so this is the transport rather than a picture of one.
   =========================================================================== */

import { useEffect, useRef, useState } from "react";
import { loadPeaks } from "@/lib/peaks";
import { fitOf, type Fit, type Phrase } from "@/lib/api";
import { cx } from "@/lib/cx";
import { timecode } from "./console";

const GUTTER = 62;      // px, track-name column
const ROW = 30;         // px, one track row

/* --- waveform ------------------------------------------------------------- */

function Wave({ url, color }: { url: string; color: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [peaks, setPeaks] = useState<Float32Array | null>(null);

  useEffect(() => {
    let live = true;
    loadPeaks(url).then((p) => { if (live && p) setPeaks(p.values); });
    return () => { live = false; };
  }, [url]);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !peaks) return;

    const draw = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const dpr = Math.min(devicePixelRatio || 1, 2);
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      if (w === 0 || h === 0) return;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;

      const g = canvas.getContext("2d");
      if (!g) return;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);
      g.fillStyle = color;

      const mid = h / 2;
      // One column per device pixel column keeps the shape honest at any width
      // instead of stretching a fixed bucket count.
      for (let x = 0; x < w; x++) {
        const peak = peaks[Math.floor((x / w) * peaks.length)] ?? 0;
        const half = Math.max(0.5, peak * (h / 2 - 1));
        g.fillRect(x, mid - half, 1, half * 2);
      }
    };

    draw();
    const ro = new ResizeObserver(draw);
    if (canvas.parentElement) ro.observe(canvas.parentElement);
    return () => ro.disconnect();
  }, [peaks, color]);

  return <canvas ref={ref} className="absolute inset-0 h-full w-full" aria-hidden />;
}

/* --- track scaffolding ---------------------------------------------------- */

function Track({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className="flex border-b border-c-edge" style={{ height: ROW }}>
      <div
        className="raised flex shrink-0 items-center border-r border-c-edge px-2 text-[10px] font-medium tracking-[0.1em] text-c-mute"
        style={{ width: GUTTER }}
      >
        {name}
      </div>
      <div className="recessed relative min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** A clip block. `from`/`to` are fractions of the timeline width. */
function Clip({
  from = 0, to = 1, tone = "neutral", label, children,
}: {
  from?: number;
  to?: number;
  tone?: "neutral" | "accent" | "good";
  label?: string;
  children?: React.ReactNode;
}) {
  const tones = {
    neutral: "border-[#3d434b] bg-[#2b3038]",
    accent: "border-[#7a4410] bg-[#3a2a14]",
    good: "border-[#2f5f49] bg-[#1e3a2e]",
  };
  return (
    <div
      className={cx("absolute inset-y-[2px] overflow-hidden rounded-[2px] border", tones[tone])}
      style={{ left: `${from * 100}%`, width: `${Math.max(0, to - from) * 100}%` }}
    >
      {children}
      {label && (
        <span className="pointer-events-none absolute left-1.5 top-0.5 text-[9px] font-medium tracking-wide text-c-dim mix-blend-plus-lighter">
          {label}
        </span>
      )}
    </div>
  );
}

/* --- ruler ---------------------------------------------------------------- */

/** Pick a tick spacing that yields roughly 6-10 labels for any duration. */
function tickStep(duration: number): number {
  const target = duration / 8;
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  return steps.find((s) => s >= target) ?? 900;
}

function Ruler({
  duration, onSeek,
}: { duration: number; onSeek: (s: number) => void }) {
  const step = tickStep(duration || 1);
  const ticks: number[] = [];
  // Nothing is measurable before a clip loads, and t / 0 would place the first
  // tick at NaN%.
  if (duration > 0) for (let t = 0; t <= duration; t += step) ticks.push(t);

  return (
    <div className="flex h-[19px] shrink-0 border-b border-c-edge">
      <div className="raised shrink-0 border-r border-c-edge" style={{ width: GUTTER }} />
      <div
        className={cx(
          "relative min-w-0 flex-1 bg-c-raise",
          duration > 0 && "cursor-ew-resize"
        )}
        onPointerDown={(e) => {
          if (!duration) return;
          const box = e.currentTarget.getBoundingClientRect();
          const seek = (clientX: number) =>
            onSeek(Math.min(duration, Math.max(0, ((clientX - box.left) / box.width) * duration)));
          seek(e.clientX);
          // Scrubbing continues outside the element until the pointer is released.
          e.currentTarget.setPointerCapture(e.pointerId);
          const move = (ev: PointerEvent) => seek(ev.clientX);
          const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
          };
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", up);
        }}
      >
        {ticks.map((t) => (
          <div key={t} className="absolute inset-y-0" style={{ left: `${(t / duration) * 100}%` }}>
            <div className="h-[5px] w-px bg-c-rule" />
            <span className="tnum absolute left-1 top-[3px] text-[9px] text-c-mute">
              {timecode(t).slice(3, 8)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* --- panel ---------------------------------------------------------------- */

export interface TimelineProps {
  duration: number;
  current: number;
  onSeek: (s: number) => void;
  sourceName?: string;
  referenceUrl?: string;
  referenceSeconds?: number;
  dubUrl?: string;
  segments?: number;
  /** Index of the running stage, and the stage labels. -1 when not running. */
  stageIndex: number;
  stages: string[];
  finished: boolean;
  /** The editable phrase timeline, once a dub has produced one. */
  phrases?: Phrase[];
  selected?: number | null;
  onSelectPhrase?: (index: number) => void;
  /** In and out points on the source, before a render. */
  trimStart?: number | null;
  trimEnd?: number | null;
}

const FIT_TONE: Record<Fit, string> = {
  fits:     "border-b-2 border-b-c-good/70",
  tight:    "border-b-2 border-b-c-warn/80",
  overruns: "border-b-2 border-b-c-bad",
  silent:   "border-b-2 border-b-c-mute/50 bg-[repeating-linear-gradient(45deg,transparent,transparent_3px,rgba(255,255,255,0.05)_3px,rgba(255,255,255,0.05)_6px)]",
  unknown:  "",
};

const FIT_LABEL: Record<Fit, string> = {
  fits: "fits its slot",
  tight: "slightly over its slot",
  overruns: "overruns its slot",
  silent: "silent",
  unknown: "length unknown",
};

export function Timeline({
  duration, current, onSeek, sourceName, referenceUrl, referenceSeconds,
  dubUrl, segments, stageIndex, stages, finished,
  phrases, selected, onSelectPhrase, trimStart, trimEnd,
}: TimelineProps) {
  const pos = duration > 0 ? Math.min(1, current / duration) : 0;
  const refTo = duration > 0 && referenceSeconds ? Math.min(1, referenceSeconds / duration) : 0;

  return (
    <div className="flex min-h-0 flex-col">
      <Ruler duration={duration} onSeek={onSeek} />

      <div className="relative min-h-0 flex-1 overflow-y-auto">
        <Track name="PROC">
          {stages.map((label, i) => {
            const done = finished || i < stageIndex;
            const active = !finished && i === stageIndex;
            return (
              <div
                key={label}
                className={cx(
                  "absolute inset-y-[2px] overflow-hidden rounded-[1px] border-r border-c-edge",
                  done ? "bg-[#2f5f49]" : active ? "bg-c-accent" : "bg-[#23262b]"
                )}
                style={{ left: `${(i / stages.length) * 100}%`, width: `${(1 / stages.length) * 100}%` }}
                title={label}
              >
                <span
                  className={cx(
                    "absolute inset-0 flex items-center truncate px-1.5 text-[9px] tracking-wide",
                    active ? "font-medium text-[#17120b]" : done ? "text-[#a8d6c0]" : "text-c-mute"
                  )}
                >
                  {label}
                </span>
              </div>
            );
          })}
        </Track>

        <Track name="V1">
          {sourceName ? (
            <>
              <Clip label={sourceName} />
              {/* Everything outside the in and out points is dimmed, so the
                  part that will actually be dubbed is unambiguous. */}
              {duration > 0 && trimStart != null && trimStart > 0 && (
                <div
                  className="pointer-events-none absolute inset-y-0 left-0 bg-c-void/70"
                  style={{ width: `${Math.min(1, trimStart / duration) * 100}%` }}
                />
              )}
              {duration > 0 && trimEnd != null && trimEnd < duration && (
                <div
                  className="pointer-events-none absolute inset-y-0 right-0 bg-c-void/70"
                  style={{ width: `${Math.max(0, 1 - trimEnd / duration) * 100}%` }}
                />
              )}
            </>
          ) : (
            <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-c-mute">
              no clip
            </span>
          )}
        </Track>

        <Track name="A1 REF">
          {referenceUrl ? (
            <Clip from={0} to={refTo || 1} tone="accent" label={`reference · ${referenceSeconds ?? "?"}s`}>
              <Wave url={referenceUrl} color="#c8801f" />
            </Clip>
          ) : (
            <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-c-mute">
              chosen during the run
            </span>
          )}
        </Track>

        <Track name="A2 DUB">
          {dubUrl ? (
            <Clip tone="good" label={segments ? `${segments} segments` : undefined}>
              <Wave url={dubUrl} color="#5cbf92" />
              {/* Real phrase blocks once the timeline is editable, so a click
                  selects the phrase actually under the cursor rather than an
                  evenly divided guess. */}
              {phrases && duration > 0
                ? phrases.map((ph) => {
                    const fit = fitOf(ph);
                    return (
                    <button
                      key={ph.index}
                      type="button"
                      title={
                        fit === "silent" ? `Silent — ${ph.text}`
                          : ph.spoken
                            ? `${ph.spoken.toFixed(2)}s in a ${ph.duration.toFixed(2)}s slot — ${ph.text}`
                            : ph.text
                      }
                      onClick={(e) => { e.stopPropagation(); onSelectPhrase?.(ph.index); }}
                      className={cx(
                        "absolute inset-y-0 border-l border-[#0e0f11]/70 transition-colors",
                        // A stripe along the bottom says whether the line fits
                        // the gap it has to land in. Knowing that used to take
                        // a click per phrase.
                        FIT_TONE[fit],
                        selected === ph.index
                          ? "bg-c-accent/25 ring-1 ring-inset ring-c-accent"
                          : "hover:bg-white/[0.07]"
                      )}
                      style={{
                        left: `${(ph.start / duration) * 100}%`,
                        width: `${Math.max(0.4, (ph.duration / duration) * 100)}%`,
                      }}
                    >
                      <span className="sr-only">
                        {`Phrase ${ph.index + 1}, ${FIT_LABEL[fit]}: ${ph.text}`}
                      </span>
                    </button>
                    );
                  })
                : segments && segments > 1
                ? Array.from({ length: segments - 1 }, (_, i) => (
                    <div
                      key={i}
                      className="absolute inset-y-0 w-px bg-[#0e0f11]/70"
                      style={{ left: `${((i + 1) / segments) * 100}%` }}
                    />
                  ))
                : null}
            </Clip>
          ) : (
            <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-c-mute">
              rendered output
            </span>
          )}
        </Track>

        {/* Playhead spans the tracks but not the name gutter. */}
        {duration > 0 && (
          <div
            className="pointer-events-none absolute inset-y-0 z-10"
            style={{ left: `calc(${GUTTER}px + (100% - ${GUTTER}px) * ${pos})` }}
          >
            <div className="h-full w-px bg-c-accent" />
            <div className="absolute -left-[3px] top-0 size-0 border-x-[3.5px] border-t-[5px] border-x-transparent border-t-c-accent" />
          </div>
        )}
      </div>
    </div>
  );
}
