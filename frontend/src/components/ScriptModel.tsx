/* ===========================================================================
   Which model rewrites the script.

   Lives on the Projects screen because it is an application setting, not a
   property of a job. The panel's real work is making the privacy tradeoff
   legible: the difference between Ollama and a hosted key is not "cloud or
   not", it is whether one line of already-translated text leaves the machine.
   Video, audio and the cloned voice never do, under any setting.
   =========================================================================== */

import { useEffect, useState } from "react";
import { CaretDown, CaretRight, Check, Warning } from "@phosphor-icons/react";
import {
  getLlmSettings, saveLlmSettings, testLlmSettings, type LlmStatus,
} from "@/lib/api";
import { Tool, Lamp } from "./console";
import { cx } from "@/lib/cx";

const Label = ({ children }: { children: React.ReactNode }) => (
  <h3 className="mt-5 text-[10px] font-medium uppercase tracking-[0.14em] text-c-mute">
    {children}
  </h3>
);

export function ScriptModel() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<LlmStatus | null>(null);
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    getLlmSettings()
      .then((s) => {
        if (!live) return;
        setStatus(s);
        setProvider(s.provider);
        setModel(s.model);
      })
      .catch(() => {});
    return () => { live = false; };
  }, []);

  const spec = status?.providers?.[provider];
  // Switching provider should offer that provider's default, not carry the
  // previous provider's model name across.
  function pickProvider(next: string) {
    setProvider(next);
    setModel(status?.providers?.[next]?.default_model ?? "");
    setApiKey("");
    setNote("");
    setFailed(false);
  }

  async function save() {
    setBusy("save"); setNote(""); setFailed(false);
    try {
      const s = await saveLlmSettings({
        provider,
        model,
        // Undefined keeps any stored key; "" would delete it.
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setStatus(s);
      setApiKey("");
      setNote("Saved.");
    } catch (err) {
      setFailed(true);
      setNote((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function test() {
    setBusy("test"); setNote(""); setFailed(false);
    try {
      const r = await testLlmSettings();
      setStatus(r);
      setNote(`Answered: ${r.reply || "(nothing)"}`);
    } catch (err) {
      setFailed(true);
      setNote((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  const dirty =
    !!status && (provider !== status.provider || model !== status.model || !!apiKey);

  return (
    <section className="mt-8 border-t border-c-rule pt-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 text-left"
        aria-expanded={open}
      >
        {open ? <CaretDown size={11} className="text-c-mute" />
              : <CaretRight size={11} className="text-c-mute" />}
        <span className="text-[12px] text-c-text">Script model</span>
        <span className="ml-auto flex items-center gap-1.5 text-[11px] text-c-dim">
          <Lamp state={status?.enabled ? "good" : "bad"} />
          {status ? `${status.providers[status.provider]?.label ?? status.provider} · ${status.model}` : "checking"}
        </span>
      </button>

      {open && (
        <div className="mt-4 max-w-[640px]">
          <p className="text-[11px] leading-relaxed text-c-dim">
            Rewrites a line shorter so it can be spoken at a natural pace
            instead of being compressed. Optional: everything else works
            without it.
          </p>

          <Label>Provider</Label>
          <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {Object.entries(status?.providers ?? {}).map(([name, p]) => (
              <button
                key={name}
                type="button"
                onClick={() => pickProvider(name)}
                className={cx(
                  "rounded-[2px] border px-2 py-1.5 text-left text-[11px] transition-colors",
                  provider === name
                    ? "border-c-accent bg-c-accent-dim text-c-accent"
                    : "border-c-rule bg-c-panel text-c-dim hover:border-c-mute hover:text-c-text"
                )}
              >
                {p.label}
              </button>
            ))}
          </div>

          {spec && (
            <p className={cx(
              "mt-2 text-[10px] leading-relaxed",
              spec.needs_key ? "text-c-warn" : "text-c-good"
            )}>
              {spec.needs_key
                ? "Sends one line of translated text per rewrite. Video, audio and the cloned voice stay on this machine."
                : "Nothing leaves this machine."}
            </p>
          )}

          <Label>Model</Label>
          <div className="mt-2 flex gap-1.5">
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              list="model-suggestions"
              spellCheck={false}
              placeholder={spec?.default_model}
              className="recessed h-[26px] min-w-0 flex-1 rounded-[2px] border border-c-edge px-2 text-[11px] text-c-text placeholder:text-c-mute"
            />
            <datalist id="model-suggestions">
              {(provider === "ollama" && status?.installed?.length
                ? status.installed
                : spec?.suggested ?? []
              ).map((m) => <option key={m} value={m} />)}
            </datalist>
          </div>
          {provider === "ollama" && status?.installed?.length === 0 && (
            <p className="mt-1 text-[10px] text-c-warn">
              Ollama is not answering. Start it, then pull a model.
            </p>
          )}

          {spec?.needs_key && (
            <>
              <Label>API key</Label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder={status?.has_key && provider === status.provider
                  ? "A key is saved. Type to replace it."
                  : "Paste your key"}
                className="recessed mt-2 h-[26px] w-full rounded-[2px] border border-c-edge px-2 text-[11px] text-c-text placeholder:text-c-mute"
              />
              <p className="mt-1 text-[10px] leading-relaxed text-c-mute">
                Stored on this machine only, readable by your user account
                alone. It is never sent anywhere except to that provider.
              </p>
            </>
          )}

          <div className="mt-4 flex items-center gap-1.5">
            <Tool primary onClick={save} disabled={!!busy || !dirty}>
              {busy === "save" ? "Saving…" : "Save"}
            </Tool>
            <Tool onClick={test} disabled={!!busy || dirty}>
              {busy === "test" ? "Testing…" : "Test"}
            </Tool>
            {note && (
              <span className={cx(
                "ml-1 flex items-center gap-1.5 text-[11px]",
                failed ? "text-c-bad" : "text-c-good"
              )}>
                {failed ? <Warning size={11} weight="fill" /> : <Check size={11} weight="bold" />}
                <span className="console-text">{note}</span>
              </span>
            )}
            {dirty && !note && (
              <span className="ml-1 text-[10px] text-c-mute">Save to apply, then test.</span>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
