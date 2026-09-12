/* ===========================================================================
   Render progress, shown inside the inspector's Run section.

   Deliberately built from the existing console primitives so it reads as part
   of the studio rather than a widget bolted onto it: same 10px caps, same
   Lamp, same 2px radii, same hairlines.
   =========================================================================== */

import { Check, ArrowClockwise, Warning, Prohibit } from "@phosphor-icons/react";
import { percent, type Progress, type Stage } from "@/lib/pipeline";
import { Lamp } from "./console";
import { cx } from "@/lib/cx";

/** Determinate track. Scales on the X axis rather than animating width, so the
    browser never re-lays-out the panel on each poll. */
function Bar({ fraction, tone }: { fraction: number; tone: "run" | "good" | "bad" }) {
  return (
    <div className="recessed h-[3px] w-full overflow-hidden rounded-[1px]">
      <div
        className={cx(
          "h-full origin-left transition-transform duration-700 ease-out",
          tone === "good" ? "bg-c-good" : tone === "bad" ? "bg-c-bad" : "bg-c-accent"
        )}
        style={{ transform: `scaleX(${Math.max(0.004, fraction)})` }}
      />
    </div>
  );
}

export function CloneProgress({
  progress, view, stages, label, error, onRetry, onCancel,
}: {
  progress: Progress;
  view: "working" | "done" | "failed" | "cancelled";
  /** The stage list for this job kind; a voice-over runs fewer of them. */
  stages: Stage[];
  /** What finished, in the completion line. */
  label: string;
  error?: string;
  onRetry?: () => void;
  onCancel?: () => void;
}) {
  /* ---- finished ---- */
  if (view === "done") {
    return (
      <div className="px-2.5 py-2">
        <Bar fraction={1} tone="good" />
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-c-good">
          <Check size={12} weight="bold" /> {label}
        </p>
        <p className="mt-0.5 pl-[18px] text-[10px] text-c-mute">Ready to play.</p>
      </div>
    );
  }

  /* ---- cancelled ---- */
  if (view === "cancelled") {
    return (
      <div className="px-2.5 py-2">
        <Bar fraction={progress.fraction} tone="run" />
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-c-dim">
          <Prohibit size={12} /> Stopped
        </p>
        <p className="mt-0.5 pl-[18px] text-[10px] text-c-mute">
          Nothing was written. Render again when you are ready.
        </p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="raised mt-2 flex h-[22px] w-full items-center justify-center gap-1.5 rounded-[2px] text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-text active:translate-y-px"
          >
            <ArrowClockwise size={12} /> Run it again
          </button>
        )}
      </div>
    );
  }

  /* ---- failed ---- */
  if (view === "failed") {
    const at = progress.index >= 0 ? stages[progress.index].label.toLowerCase() : null;
    return (
      <div className="px-2.5 py-2">
        <Bar fraction={progress.fraction} tone="bad" />
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-c-bad">
          <Warning size={12} weight="fill" />
          {at ? `Stopped while ${at}` : "The run failed"}
        </p>
        {error && (
          <p className="console-text mt-1.5 rounded-[2px] bg-[#3a1f1c] px-2 py-1.5 text-[10px] leading-relaxed text-c-bad">
            {error}
          </p>
        )}
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="raised mt-2 flex h-[22px] w-full items-center justify-center gap-1.5 rounded-[2px] text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-text active:translate-y-px"
          >
            <ArrowClockwise size={12} /> Retry this render
          </button>
        )}
      </div>
    );
  }

  /* ---- running ---- */
  const queued = progress.index < 0;
  const current = queued ? null : stages[progress.index];

  return (
    <div className="px-2.5 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-c-text">
          {current ? current.label : "Queued"}
        </span>
        <span className="tnum text-[11px] text-c-accent">{percent(progress.fraction)}%</span>
      </div>

      <div className="mt-1.5">
        <Bar fraction={progress.fraction} tone="run" />
      </div>

      <ol className="mt-2.5">
        {stages.map((s: Stage, i: number) => {
          const state = queued ? "pending" : i < progress.index ? "done" : i === progress.index ? "active" : "pending";
          return (
            <li
              key={s.key}
              className={cx(
                "flex items-center gap-2 py-[3px] text-[11px]",
                state === "active" ? "text-c-text" : state === "done" ? "text-c-dim" : "text-c-mute"
              )}
            >
              <span className="flex w-3 shrink-0 justify-center">
                {state === "done" ? (
                  <Check size={11} weight="bold" className="text-c-good" />
                ) : (
                  <Lamp state={state === "active" ? "run" : "idle"} pulse={state === "active"} />
                )}
              </span>
              <span className="min-w-0 flex-1 truncate">{s.label}</span>
              {/* Only the two stages the backend counts show a count. */}
              {state === "active" && progress.done != null && progress.total != null && (
                <span className="tnum shrink-0 text-[10px] text-c-accent">
                  {progress.done}/{progress.total}
                </span>
              )}
            </li>
          );
        })}
      </ol>

      {onCancel && (
        <button
          type="button"
          onClick={onCancel}
          className="raised mt-2 flex h-[22px] w-full items-center justify-center gap-1.5 rounded-[2px] text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-bad active:translate-y-px"
        >
          <Prohibit size={12} /> Stop render
        </button>
      )}
    </div>
  );
}
