/* ===========================================================================
   Settings, in a panel over whatever you were looking at.

   Not a route: these are things you adjust while looking at your work, not
   somewhere you go. Everything here is a knob the pipeline already reads, and
   a knob belonging to a model this project is not using is not shown at all —
   the backend says which ones apply, so choosing a different lip sync engine
   takes its settings with it.
   =========================================================================== */

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, DownloadSimple, X } from "@phosphor-icons/react";
import {
  getAdvanced, saveAdvanced, sweepNow,
  getModels, downloadModels, modelProgress, cancelModels,
  getEngines, saveEngines,
  type AdvancedSettings, type ModelInventory, type ModelProgress,
  type EngineSettings,
} from "@/lib/api";
import { ScriptModel } from "@/components/ScriptModel";
import { Tool, Sub, Stat } from "@/components/console";
import { cx } from "@/lib/cx";

function bytes(n: number): string {
  if (n < 1e6) return `${Math.round(n / 1e3)} KB`;
  if (n < 1e9) return `${Math.round(n / 1e6)} MB`;
  return `${(n / 1e9).toFixed(1)} GB`;
}

export function Settings({ onClose }: { onClose: () => void }) {
  const [adv, setAdv] = useState<AdvancedSettings | null>(null);
  const [inv, setInv] = useState<ModelInventory | null>(null);
  const [prog, setProg] = useState<ModelProgress | null>(null);
  const [engines, setEnginesState] = useState<EngineSettings | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const panel = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [a, m, e] = await Promise.all([getAdvanced(), getModels(), getEngines()]);
      setAdv(a); setInv(m); setProg(m.progress); setEnginesState(e);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Escape closes, and focus lands inside so the panel is reachable from the
  // keyboard the moment it opens.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
    }
    window.addEventListener("keydown", onKey);
    panel.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (!prog?.running) return;
    const id = window.setInterval(async () => {
      try {
        const p = await modelProgress();
        setProg(p);
        if (!p.running) setInv(await getModels());
      } catch { /* the next tick tries again */ }
    }, 1000);
    return () => window.clearInterval(id);
  }, [prog?.running]);

  async function setKnob(key: string, raw: string) {
    const spec = adv?.tunables[key];
    if (!spec) return;
    const value = spec.kind === "str" ? raw : Number(raw);
    if (spec.kind !== "str" && Number.isNaN(value)) return;
    try {
      const { tunables } = await saveAdvanced({ [key]: value });
      setAdv((a) => (a ? { ...a, tunables } : a));
      setNote("Saved.");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function chooseEngine(stage: string, id: string) {
    try {
      setEnginesState(await saveEngines({ [stage]: id }));
      // Stage choices decide which knobs apply, so the knobs have to be
      // re-read rather than left describing the previous model.
      setAdv(await getAdvanced());
      setNote("Saved. Restart Multiva for the stage models to take effect.");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const knobs = Object.entries(adv?.tunables ?? {}).filter(([, t]) => t.applies);

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/50"
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={panel}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        className="console flex h-full w-full max-w-[420px] flex-col border-l border-c-edge bg-c-panel outline-none"
      >
        <header className="raised flex h-[30px] shrink-0 items-center gap-2 border-b border-c-edge px-2.5">
          <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-c-dim">
            Settings
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            className="ml-auto rounded-[2px] p-1 text-c-mute transition-colors hover:bg-c-hover hover:text-c-text"
          >
            <X size={12} weight="bold" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto pb-6">
          {error && (
            <p className="m-2.5 rounded-[2px] border border-[#6e2f26] bg-[#251310] p-2 text-[11px] leading-relaxed text-c-bad">
              {error}
            </p>
          )}

          {/* ---- models ---- */}
          <Sub>Models</Sub>
          {engines && Object.entries(engines.stages).map(([stage, spec]) => (
            <div key={stage} className="px-2.5 py-1.5">
              <div className="flex items-baseline gap-2">
                <span className="text-[11px] text-c-text">{spec.label}</span>
                {spec.pinned && (
                  <span className="text-[9px] uppercase tracking-[0.1em] text-c-warn">
                    fixed by environment
                  </span>
                )}
              </div>
              <select
                value={spec.current}
                disabled={spec.pinned}
                onChange={(e) => chooseEngine(stage, e.target.value)}
                className="mt-1 h-[24px] w-full rounded-[2px] border border-c-rule bg-c-well px-1.5 text-[11px] text-c-text outline-none focus:border-c-accent disabled:opacity-50"
              >
                {spec.options.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label} · {o.size}{o.ready ? "" : " · not downloaded"}
                  </option>
                ))}
              </select>
            </div>
          ))}

          {inv && (
            <div className="px-2.5 pb-1.5 pt-1">
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-c-mute">
                  {inv.missing === 0
                    ? "All model files present"
                    : `${inv.missing} model file(s) missing`}
                </span>
                {inv.missing > 0 && !prog?.running && (
                  <Tool className="ml-auto" onClick={async () => {
                    try { setProg(await downloadModels()); }
                    catch (err) { setError((err as Error).message); }
                  }}>
                    Download them
                  </Tool>
                )}
                {prog?.running && (
                  <Tool className="ml-auto" onClick={() => cancelModels().then(setProg).catch(() => {})}>
                    Stop
                  </Tool>
                )}
              </div>
              {prog?.running && (
                <div className="mt-1.5">
                  <div className="flex items-baseline gap-2 text-[10px] text-c-mute">
                    <span className="text-c-text">{prog.label || "Starting…"}</span>
                    <span className="tnum ml-auto">
                      {prog.of > 0
                        ? `${Math.round(prog.bytes / 1e6)} / ${Math.round(prog.of / 1e6)} MB`
                        : "working"}
                    </span>
                  </div>
                  <div className="mt-1 h-[3px] overflow-hidden rounded-[2px] bg-c-edge">
                    <div
                      className="h-full bg-c-accent transition-[width] duration-300"
                      style={{
                        width: prog.of > 0 ? `${Math.min(100, (prog.bytes / prog.of) * 100)}%` : "100%",
                        opacity: prog.of > 0 ? 1 : 0.4,
                      }}
                    />
                  </div>
                </div>
              )}
              {!prog?.running && (
                <ul className="mt-1.5 grid gap-0.5">
                  {inv.models.map((m) => (
                    <li key={m.id} className="flex items-center gap-1.5 text-[10px]">
                      {m.present
                        ? <Check size={9} className="shrink-0 text-c-good" />
                        : <DownloadSimple size={9} className="shrink-0 text-c-warn" />}
                      <span className="truncate text-c-dim">{m.label}</span>
                      <span className="tnum ml-auto shrink-0 text-c-mute">{m.size}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* ---- script model ---- */}
          <Sub>Script model</Sub>
          <div className="[&_h2]:hidden">
            <ScriptModel />
          </div>

          {/* ---- locations ---- */}
          <Sub>Locations</Sub>
          <Stat k="Finished projects" v={adv?.output_dir ?? "—"} />
          <Stat k="Working files" v={adv?.workdir ?? "—"} />
          <p className="px-2.5 pb-1 pt-0.5 text-[10px] leading-relaxed text-c-mute">
            Where finished projects are saved is set on the models screen.
          </p>

          {/* ---- storage ---- */}
          <Sub>Storage</Sub>
          <Stat k="Working files hold" v={adv ? bytes(adv.workdir_bytes) : "—"} />
          <div className="px-2.5 py-1.5">
            <Tool
              onClick={async () => {
                try {
                  const r = await sweepNow();
                  setAdv((a) => (a ? { ...a, workdir_bytes: r.workdir_bytes } : a));
                  setNote(r.removed
                    ? `Removed ${r.removed} abandoned item(s).`
                    : "Nothing to clear.");
                } catch (err) { setError((err as Error).message); }
              }}
              className="w-full"
            >
              Clear abandoned runs now
            </Tool>
            <p className="pt-1 text-[10px] leading-relaxed text-c-mute">
              Only runs that never became a project. Saved projects are never
              touched.
            </p>
          </div>

          {/* ---- the pipeline's knobs, minus any that do not apply ---- */}
          <Sub>Pipeline</Sub>
          {knobs.length === 0 && (
            <p className="px-2.5 py-1 text-[10px] text-c-mute">
              Nothing adjustable for the models currently chosen.
            </p>
          )}
          {knobs.map(([key, t]) => (
            <div key={key} className="px-2.5 py-1.5">
              <div className="flex items-baseline gap-2">
                <label htmlFor={`k-${key}`} className="text-[11px] text-c-text">
                  {t.label}
                </label>
                {t.pinned && (
                  <span className="text-[9px] uppercase tracking-[0.1em] text-c-warn">
                    fixed by environment
                  </span>
                )}
                <span className="tnum ml-auto text-[10px] text-c-mute">
                  default {t.default}
                </span>
              </div>
              <input
                id={`k-${key}`}
                defaultValue={String(t.value)}
                disabled={t.pinned}
                inputMode={t.kind === "str" ? "text" : "decimal"}
                onBlur={(e) => {
                  if (e.target.value !== String(t.value)) setKnob(key, e.target.value);
                }}
                onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
                className="mt-1 h-[24px] w-full rounded-[2px] border border-c-rule bg-c-well px-1.5 text-[11px] text-c-text outline-none focus:border-c-accent disabled:opacity-50"
              />
              <p className="pt-0.5 text-[10px] leading-relaxed text-c-mute">{t.why}</p>
            </div>
          ))}

          {/* ---- about ---- */}
          <Sub>About</Sub>
          <p className="px-2.5 py-1 text-[10px] leading-relaxed text-c-mute">
            Everything runs on this machine. The only stage that can leave it is
            the script model, and only when you point it at a hosted provider.
            XTTS, used for languages IndicF5 does not cover, is under a
            non-commercial licence.
          </p>
        </div>

        {note && (
          <footer className={cx(
            "raised shrink-0 border-t border-c-edge px-2.5 py-1.5 text-[10px]",
            note.startsWith("Saved") ? "text-c-warn" : "text-c-dim"
          )}>
            {note}
          </footer>
        )}
      </div>
    </div>
  );
}
