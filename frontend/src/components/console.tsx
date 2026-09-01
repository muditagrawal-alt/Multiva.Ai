/* ===========================================================================
   Console primitives — the furniture the editor layout is assembled from.

   Everything here is deliberately small and hard-edged. Panel headers are
   26px, controls are 22-24px, labels are 10px. A dense tool earns trust by
   showing state, not by breathing; the marketing pages do the breathing.

   Radii top out at 2px. Rounded cards are what made the old studio read as a
   web page rather than an application.
   =========================================================================== */

import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { Link } from "react-router-dom";
import { cx } from "@/lib/cx";

/* --- panels --------------------------------------------------------------- */

export function Panel({
  title, right, children, className, bodyClassName, scroll = true,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  scroll?: boolean;
}) {
  return (
    <section className={cx("flex min-h-0 min-w-0 flex-col bg-c-panel", className)}>
      <header className="raised flex h-[26px] shrink-0 items-center gap-2 border-b border-c-edge pl-2.5 pr-1.5">
        <h2 className="text-[10px] font-medium uppercase tracking-[0.14em] text-c-dim">
          {title}
        </h2>
        {right && <div className="ml-auto flex items-center gap-1">{right}</div>}
      </header>
      <div className={cx("min-h-0 flex-1", scroll && "overflow-y-auto", bodyClassName)}>
        {children}
      </div>
    </section>
  );
}

/* --- controls ------------------------------------------------------------- */

type ToolProps = {
  active?: boolean;
  primary?: boolean;
  icon?: ReactNode;
  children?: ReactNode;
} & ComponentPropsWithoutRef<"button">;

export function Tool({ active, primary, icon, children, className, ...rest }: ToolProps) {
  return (
    <button
      type="button"
      className={cx(
        "inline-flex h-[22px] shrink-0 items-center justify-center gap-1.5 rounded-[2px] px-2",
        "text-[11px] leading-none transition-colors duration-150",
        "disabled:pointer-events-none disabled:opacity-35",
        // 1px down on press: the whole control moves, not just its colour.
        "active:translate-y-px",
        children ? "min-w-[22px]" : "w-[22px] px-0",
        primary
          ? "bg-c-accent font-medium text-[#17120b] hover:bg-[#ffa040]"
          : active
            ? "bg-c-accent-dim text-c-accent shadow-[inset_0_1px_0_rgb(255_255_255/0.06)]"
            : "raised text-c-dim hover:bg-c-hover hover:text-c-text",
        className
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  );
}

/* Inspector row: fixed-width label column so every control lines up on one
   vertical edge, the way a properties panel does. */
export function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 px-2.5 py-[5px]">
      <span className="w-[86px] shrink-0 pt-[5px] text-right text-[11px] text-c-mute" title={hint}>
        {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export const Sel = ({ className, ...rest }: ComponentPropsWithoutRef<"select">) => (
  <select
    className={cx(
      "recessed h-[24px] w-full rounded-[2px] border border-c-edge px-2 text-[11px]",
      "text-c-text transition-colors hover:border-c-rule",
      className
    )}
    {...rest}
  />
);

/* --- readouts ------------------------------------------------------------- */

/** Small caps section divider inside a panel body. */
export const Sub = ({ children }: { children: ReactNode }) => (
  <div className="mt-1 border-b border-t border-c-edge bg-c-well/60 px-2.5 py-1 text-[10px] uppercase tracking-[0.14em] text-c-mute">
    {children}
  </div>
);

/** Label/value line. Values are tabular so they do not shift as they update. */
export const Stat = ({ k, v, tone }: { k: string; v: ReactNode; tone?: "good" | "warn" | "bad" }) => (
  <div className="flex items-baseline gap-2 px-2.5 py-[3px] text-[11px]">
    <span className="w-[86px] shrink-0 text-right text-c-mute">{k}</span>
    <span
      className={cx(
        "tnum console-text min-w-0 truncate",
        tone === "good" ? "text-c-good" : tone === "warn" ? "text-c-warn" : tone === "bad" ? "text-c-bad" : "text-c-text"
      )}
    >
      {v}
    </span>
  </div>
);

const LAMP = {
  idle: "bg-c-mute",
  run: "bg-c-accent",
  good: "bg-c-good",
  bad: "bg-c-bad",
} as const;

export const Lamp = ({ state, pulse }: { state: keyof typeof LAMP; pulse?: boolean }) => (
  <span
    aria-hidden
    className={cx(
      "size-[6px] shrink-0 rounded-full",
      LAMP[state],
      pulse && "motion-safe:animate-pulse"
    )}
  />
);

/** Hours:minutes:seconds.centiseconds. No fake frame numbers — the pipeline
    never reports a frame rate, so claiming one would be invented precision. */
export function timecode(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--:--.--";
  const s = Math.max(0, seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  const cs = Math.floor((s % 1) * 100);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(h)}:${p(m)}:${p(sec)}.${p(cs)}`;
}

export const bytes = (n: number) =>
  n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(2)} GB` : `${(n / (1 << 20)).toFixed(1)} MB`;

/* --- window chrome -------------------------------------------------------- */

const Mark = () => (
  <svg viewBox="0 0 24 24" className="size-[13px]" fill="none" aria-hidden>
    <rect x="1" y="9" width="3" height="6" rx="1.5" fill="currentColor" opacity=".35" />
    <rect x="6" y="5" width="3" height="14" rx="1.5" fill="currentColor" opacity=".6" />
    <rect x="11" y="2" width="3" height="20" rx="1.5" className="fill-c-accent" />
    <rect x="16" y="6" width="3" height="12" rx="1.5" fill="currentColor" opacity=".6" />
    <rect x="21" y="10" width="3" height="4" rx="1.5" fill="currentColor" opacity=".35" />
  </svg>
);

const PAGES = [
  { to: "/", key: "projects", label: "Projects" },
  { to: "/studio", key: "dub", label: "Studio" },
] as const;

/** The window's top strip: identity, page tabs, and an optional right slot. */
export function TitleBar({
  active, right, canOpenStudio = true,
}: {
  active: "projects" | "dub";
  right?: ReactNode;
  /** The studio is a workspace for one project, so it is only reachable by
      creating or opening one. Switching to it from the tab strip would open
      an empty one with nothing to work on. */
  canOpenStudio?: boolean;
}) {
  return (
    <header className="raised flex items-center gap-3 border-b border-c-edge px-2.5">
      <span className="flex items-center gap-2 text-c-text">
        <Mark />
        <span className="text-[11px] font-semibold tracking-[0.12em]">MULTIVA</span>
      </span>

      <div className="h-3.5 w-px bg-c-rule" />

      <nav className="flex items-center gap-0.5">
        {PAGES.map((p) =>
          p.key === active ? (
            <span
              key={p.key}
              aria-current="page"
              className="rounded-[2px] bg-c-accent-dim px-2 py-[3px] text-[10px] font-medium uppercase tracking-[0.12em] text-c-accent"
            >
              {p.label}
            </span>
          ) : p.key === "dub" && !canOpenStudio ? (
            <span
              key={p.key}
              title="Create or open a project to work in the studio"
              aria-disabled="true"
              className="cursor-default rounded-[2px] px-2 py-[3px] text-[10px] uppercase tracking-[0.12em] text-c-mute/40"
            >
              {p.label}
            </span>
          ) : (
            <Link
              key={p.key}
              to={p.to}
              className="rounded-[2px] px-2 py-[3px] text-[10px] uppercase tracking-[0.12em] text-c-mute transition-colors hover:bg-c-hover hover:text-c-text"
            >
              {p.label}
            </Link>
          )
        )}
      </nav>

      {right && <div className="ml-auto flex items-center gap-3">{right}</div>}
    </header>
  );
}

/** The window's bottom strip. */
export function StatusBar({ children }: { children: ReactNode }) {
  return (
    <footer className="raised flex items-center gap-3 border-t border-c-edge px-2.5 text-[10px] text-c-mute">
      {children}
    </footer>
  );
}
