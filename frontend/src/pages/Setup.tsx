/* ===========================================================================
   Model setup.

   Shown once, after the engine finishes loading and before the projects
   screen, and reachable from Projects afterwards. Every option listed here is
   one the pipeline can actually load; anything that would need code that does
   not exist is absent rather than greyed out.

   Two things it refuses to hide: how large a model is when it has not been
   downloaded yet, and that stage changes only take effect on restart, because
   each stage's module reads its model once at import.
   =========================================================================== */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Check, DownloadSimple, Warning } from "@phosphor-icons/react";
import {
  getEngines, saveEngines, type EngineSettings,
} from "@/lib/api";
import { ScriptModel } from "@/components/ScriptModel";
import { Tool, Lamp, TitleBar, StatusBar } from "@/components/console";
import { cx } from "@/lib/cx";

export default function Setup() {
  const navigate = useNavigate();
  const [data, setData] = useState<EngineSettings | null>(null);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [folder, setFolder] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    getEngines()
      .then((d) => {
        if (!live) return;
        setData(d);
        setFolder(d.output_dir);
        setChoice(
          Object.fromEntries(
            Object.entries(d.stages).map(([k, v]) => [k, v.current])
          )
        );
      })
      .catch((err) => live && setError((err as Error).message));
    return () => { live = false; };
  }, []);

  async function persist() {
    setBusy(true); setError("");
    try {
      const d = await saveEngines({ ...choice, output_dir: folder });
      setData(d);
      setSaved(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const dirty = !!data && (
    Object.entries(choice).some(([k, v]) => data.stages[k]?.current !== v) ||
    folder !== data.output_dir
  );
  const firstRun = data ? !data.configured : false;

  // A choice that is not downloaded costs a wait on the next run, and that is
  // worth saying before the person leaves this screen rather than after.
  const pending = data
    ? Object.entries(choice).filter(([stage, id]) =>
        !data.stages[stage]?.options.find((o) => o.id === id)?.ready)
    : [];

  return (
    <div className="console grid" style={{ gridTemplateRows: "30px minmax(0,1fr) 22px" }}>
      <TitleBar active="projects" />

      <main className="min-h-0 overflow-y-auto bg-c-void">
        <div className="mx-auto w-full max-w-[900px] px-5 py-8 sm:px-8">
          <h1 className="text-[15px] text-c-text">
            {firstRun ? "Choose your models" : "Models"}
          </h1>
          <p className="mt-1.5 max-w-[68ch] text-[11px] leading-relaxed text-c-dim">
            The defaults are what every measurement in this project was made
            with. Change them if you want to trade accuracy for speed, or the
            other way round. Everything runs on this machine.
          </p>

          {error && (
            <p role="alert" className="console-text mt-4 rounded-[2px] bg-[#2a1714] px-3 py-2 text-[11px] text-c-bad">
              {error}
            </p>
          )}

          {!data && (
            <div className="mt-6 space-y-3">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-[86px] animate-pulse rounded-[2px] border border-c-edge bg-c-well motion-reduce:animate-none" />
              ))}
            </div>
          )}

          {data && Object.entries(data.stages).map(([stage, spec]) => (
            <section key={stage} className="mt-7">
              <div className="flex items-baseline gap-2">
                <h2 className="text-[12px] text-c-text">{spec.label}</h2>
                {spec.pinned && (
                  <span className="text-[10px] text-c-warn">
                    fixed by an environment variable
                  </span>
                )}
              </div>
              <p className="mt-1 max-w-[70ch] text-[11px] leading-relaxed text-c-dim">
                {spec.why}
              </p>

              <ul className="mt-2.5 grid gap-1.5 sm:grid-cols-2">
                {spec.options.map((o) => {
                  const on = choice[stage] === o.id;
                  return (
                    <li key={o.id}>
                      <button
                        type="button"
                        disabled={spec.pinned || (!o.ready && o.source === "manual")}
                        onClick={() => setChoice((c) => ({ ...c, [stage]: o.id }))}
                        className={cx(
                          "flex w-full flex-col gap-1 rounded-[2px] border p-2.5 text-left transition-colors",
                          "disabled:opacity-50",
                          on
                            ? "border-c-accent bg-c-accent-dim/50"
                            : "border-c-rule bg-c-panel hover:border-c-mute"
                        )}
                      >
                        <span className="flex w-full items-center gap-2">
                          <span className={cx("text-[11px]", on ? "font-medium text-c-text" : "text-c-text")}>
                            {o.label}
                          </span>
                          {o.id === spec.default && (
                            <span className="text-[9px] uppercase tracking-[0.12em] text-c-mute">
                              default
                            </span>
                          )}
                          <span className="tnum ml-auto text-[10px] text-c-mute">{o.size}</span>
                          {o.ready
                            ? <Check size={10} className="text-c-good" />
                            : <DownloadSimple size={10} className="text-c-warn" />}
                        </span>
                        <span className={cx(
                          "text-[10px] leading-relaxed",
                          o.warn ? "text-c-warn"
                            : on ? "text-c-text/80"
                            : "text-c-mute"
                        )}>
                          {o.warn ?? o.note}
                        </span>
                        {!o.ready && (
                          <span className="text-[10px] leading-relaxed text-c-warn">
                            {o.source === "download"
                              ? "Not downloaded. It will be fetched on the next run."
                              : `Not on disk. Wav2Lip does not publish this one, so it has to be placed at ${o.local} by hand.`}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}

          {data && (
            <section className="mt-7">
              <h2 className="text-[12px] text-c-text">Where finished videos go</h2>
              <p className="mt-1 max-w-[70ch] text-[11px] leading-relaxed text-c-dim">
                Every dub and voice-over is copied here when it finishes, named
                after the clip and the language. The studio keeps its own working
                copy, so moving or deleting these will not break a project.
              </p>
              <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                <input
                  value={folder}
                  onChange={(e) => setFolder(e.target.value)}
                  spellCheck={false}
                  placeholder={data.default_output_dir}
                  className="recessed h-[28px] min-w-0 flex-1 rounded-[2px] border border-c-edge px-2 text-[11px] text-c-text placeholder:text-c-mute"
                />
                {folder !== data.default_output_dir && (
                  <Tool onClick={() => setFolder(data.default_output_dir)}>
                    Use the default
                  </Tool>
                )}
              </div>
              <p className="mt-1 text-[10px] text-c-mute">
                Created on first render. If it cannot be written to, renders fall
                back to {data.default_output_dir}.
              </p>
            </section>
          )}

          {/* The script model has its own storage and its own key handling, so
              it stays its own component rather than being folded in here. */}
          <ScriptModel />

          <div className="sticky bottom-0 -mx-5 mt-8 border-t border-c-rule bg-c-void px-5 py-4 sm:-mx-8 sm:px-8">
            <div className="flex flex-wrap items-center gap-2">
              <Tool primary onClick={persist} disabled={busy || !dirty} className="h-[26px]">
                {busy ? "Saving…" : "Save models"}
              </Tool>
              <button
                type="button"
                onClick={() => navigate("/")}
                className="raised inline-flex h-[26px] items-center gap-1.5 rounded-[2px] border border-c-rule px-3 text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-text active:translate-y-px"
              >
                {firstRun && !saved ? "Skip, use the defaults" : "Go to projects"}
                <ArrowRight size={11} weight="bold" />
              </button>

              {saved && (
                <span className="flex items-center gap-1.5 text-[11px] text-c-warn">
                  <Warning size={11} weight="fill" />
                  Saved. Restart Multiva for the stage models to take effect.
                </span>
              )}
              {!saved && pending.length > 0 && (
                <span className="flex items-center gap-1.5 text-[11px] text-c-dim">
                  <Lamp state="run" />
                  {pending.length} selected model{pending.length === 1 ? "" : "s"} will download on the next run.
                </span>
              )}
            </div>
          </div>
        </div>
      </main>

      <StatusBar>
        <span className="flex items-center gap-1.5 text-c-dim">
          <Lamp state={data ? "good" : "run"} pulse={!data} />
          {data ? (data.configured ? "Models configured" : "First run") : "Loading"}
        </span>
      </StatusBar>
    </div>
  );
}
