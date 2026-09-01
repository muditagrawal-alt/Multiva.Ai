/* ===========================================================================
   Home — the project manager.

   This is what opens when the application starts. It is the first workspace,
   not a page in front of the application: no headline, no positioning line, no
   capability showcase, no CTA. The work is the content.

   It is also the single project browser in the app. There is no separate
   library page, because two views listing the same rows is one view too many.
   =========================================================================== */

import { useCallback, useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import {
  Plus, ArrowClockwise, Trash, DownloadSimple, PencilSimple, Warning,
} from "@phosphor-icons/react";
import {
  listVideos, removeVideo, getEngines, langName, relativeTime,
  type VideoRecord,
} from "@/lib/api";
import { Tool, Lamp, TitleBar, StatusBar } from "@/components/console";
import { cx } from "@/lib/cx";

type View = "loading" | "list" | "empty" | "offline";

const idOf = (r: VideoRecord) => r.video_id ?? r.id ?? "";

const lamp = (status?: string) =>
  status === "done" ? "good" : status === "failed" ? "bad" : "run";

export default function Home() {
  const [view, setView] = useState<View>("loading");
  const [rows, setRows] = useState<VideoRecord[]>([]);
  const [problem, setProblem] = useState("");
  const [confirming, setConfirming] = useState("");
  const [actionError, setActionError] = useState("");
  // null until known: a launch should not flash the projects grid before
  // deciding whether this is a first run.
  const [configured, setConfigured] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    setView("loading");
    setActionError("");
    try {
      // /videos/ now answers 503 when the database is unreachable rather than
      // returning an empty list, so an empty result genuinely means empty.
      const data = await listVideos();
      setRows(data);
      setView(data.length ? "list" : "empty");
    } catch (err) {
      setProblem((err as Error).message);
      setView("offline");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    let live = true;
    getEngines()
      .then((d) => live && setConfigured(d.configured))
      // If the check itself fails, show the projects screen rather than
      // trapping someone in setup because one endpoint is unreachable.
      .catch(() => live && setConfigured(true));
    return () => { live = false; };
  }, []);

  async function destroy(id: string) {
    setActionError("");
    try {
      await removeVideo(id);
      const left = rows.filter((r) => idOf(r) !== id);
      setRows(left);
      setConfirming("");
      if (!left.length) setView("empty");
    } catch (err) {
      setActionError((err as Error).message);
    }
  }

  const newProject = (size: "header" | "empty") => (
    <Link
      to="/studio"
      className={cx(
        "inline-flex items-center gap-1.5 rounded-[2px] bg-c-accent font-medium text-[#17120b]",
        "transition-colors hover:bg-[#ffa040] active:translate-y-px",
        size === "header" ? "h-[24px] px-2.5 text-[11px]" : "h-[30px] px-4 text-[12px]"
      )}
    >
      <Plus size={size === "header" ? 11 : 12} weight="bold" /> New project
    </Link>
  );

  if (configured === false) return <Navigate to="/setup" replace />;

  return (
    <div className="console grid" style={{ gridTemplateRows: "30px minmax(0,1fr) 22px" }}>
      <TitleBar
        active="projects"
        canOpenStudio={false}
        right={
          <Link
            to="/setup"
            className="rounded-[2px] px-2 py-[3px] text-[10px] uppercase tracking-[0.12em] text-c-mute transition-colors hover:bg-c-hover hover:text-c-text"
          >
            Models
          </Link>
        }
      />

      {/* Home carries no panel chrome. The studio is dense because it is a
          workspace; this is where you arrive, and it should breathe more. */}
      <main className="min-h-0 overflow-y-auto bg-c-void">
        <div className="mx-auto w-full max-w-[1240px] px-5 py-7 sm:px-8">
          <div className="flex items-center gap-3 border-b border-c-rule pb-3">
            <h1 className="text-[13px] font-medium tracking-[0.02em] text-c-text">Projects</h1>
            {view === "list" && (
              <span className="tnum text-[11px] text-c-mute">{rows.length}</span>
            )}
            <div className="ml-auto flex items-center gap-1.5">
              <Tool
                icon={<ArrowClockwise size={12} />}
                onClick={load}
                disabled={view === "loading"}
                title="Refresh"
                className="h-[24px] border border-c-rule"
              >
                Refresh
              </Tool>
              {/* One primary action on screen: the header carries it once
                  there is a list, the empty state carries it otherwise. */}
              {view === "list" && newProject("header")}
            </div>
          </div>

          {view === "loading" && (
            <ul className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {Array.from({ length: 8 }, (_, i) => (
                <li
                  key={i}
                  className="h-[104px] animate-pulse rounded-[2px] border border-c-edge bg-c-well motion-reduce:animate-none"
                />
              ))}
            </ul>
          )}

          {view === "offline" && (
            <div className="mt-5 flex max-w-[56ch] items-start gap-2.5 rounded-[2px] border border-[#5c2b24] bg-[#2a1714] p-3.5">
              <Warning size={14} className="mt-px shrink-0 text-c-bad" />
              <div>
                <h2 className="text-[12px] font-medium text-c-bad">Cannot list your projects</h2>
                <p className="console-text mt-1.5 text-[11px] leading-relaxed text-c-dim">{problem}</p>
                <button
                  type="button"
                  onClick={load}
                  className="raised mt-3 inline-flex h-[24px] items-center gap-1.5 rounded-[2px] px-2.5 text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-text active:translate-y-px"
                >
                  <ArrowClockwise size={11} /> Try again
                </button>
              </div>
            </div>
          )}

          {view === "empty" && (
            <div className="flex min-h-[46vh] flex-col items-center justify-center gap-1 text-center">
              <h2 className="text-[15px] text-c-text">No projects yet</h2>
              <p className="max-w-[42ch] text-[12px] leading-relaxed text-c-dim">
                Import a clip in the studio and pick an output language. The
                finished dub is filed here.
              </p>
              <div className="mt-6">{newProject("empty")}</div>
            </div>
          )}

          {view === "list" && (
            <>
              <ul className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {rows.map((r) => {
                  const id = idOf(r);
                  const failed = r.processing_status === "failed";
                  return (
                    <li
                      key={id}
                      className={cx(
                        "group relative flex min-w-0 flex-col rounded-[2px] border bg-c-panel p-3.5",
                        "transition-colors duration-150",
                        confirming === id
                          ? "border-[#5c2b24]"
                          : "border-c-rule hover:border-c-mute focus-within:border-c-mute"
                      )}
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <Lamp state={lamp(r.processing_status)} />
                        <h2 className="console-text min-w-0 flex-1 truncate text-[12px] text-c-text">
                          {r.title || id.slice(0, 8)}
                        </h2>
                      </div>

                      <p className="mt-2.5 truncate text-[11px] text-c-dim">
                        {langName(r.original_language)} to {langName(r.target_language)}
                      </p>
                      <p className="tnum mt-1 truncate text-[10px] text-c-mute">
                        {r.duration || "unknown length"}
                        {r.created_at ? ` · ${relativeTime(r.created_at)}` : ""}
                      </p>

                      {failed && r.error_message && (
                        <p className="console-text mt-2 line-clamp-2 text-[10px] leading-relaxed text-c-bad">
                          {r.error_message}
                        </p>
                      )}

                      {confirming === id ? (
                        <div className="mt-3 flex gap-1.5">
                          <Tool
                            onClick={() => destroy(id)}
                            className="flex-1 bg-[#5c2b24] text-c-bad hover:bg-[#6d332b]"
                          >
                            Delete
                          </Tool>
                          <Tool onClick={() => setConfirming("")}>Cancel</Tool>
                        </div>
                      ) : (
                        <div className="mt-auto flex gap-1.5 pt-3">
                          {r.openable && r.job_id ? (
                            <Link
                              to={`/studio?job=${encodeURIComponent(r.job_id)}`}
                              className="raised inline-flex h-[22px] items-center gap-1.5 rounded-[2px] border border-c-rule px-2.5 text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-text"
                            >
                              <PencilSimple size={11} /> Open
                            </Link>
                          ) : r.dubbed_url ? (
                            <a
                              href={r.dubbed_url}
                              download
                              className="raised inline-flex h-[22px] items-center gap-1.5 rounded-[2px] border border-c-rule px-2.5 text-[11px] text-c-dim transition-colors hover:bg-c-hover hover:text-c-text"
                              title="The working files for this project are gone; only the finished video is left."
                            >
                              <DownloadSimple size={11} /> Download
                            </a>
                          ) : null}
                          <Tool
                            icon={<Trash size={11} />}
                            onClick={() => setConfirming(id)}
                            title="Delete project"
                            aria-label={`Delete ${r.title || "project"}`}
                            className="border border-c-rule"
                          />
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>

              {actionError && (
                <p role="alert" className="console-text mt-4 max-w-[56ch] rounded-[2px] bg-[#2a1714] px-3 py-2 text-[11px] leading-relaxed text-c-bad">
                  {actionError}
                </p>
              )}
            </>
          )}
        </div>
      </main>

      <StatusBar>
        <span className="flex items-center gap-1.5 text-c-dim">
          <Lamp
            state={view === "offline" ? "bad" : view === "loading" ? "run" : "good"}
            pulse={view === "loading"}
          />
          {view === "loading" ? "Loading"
            : view === "offline" ? "Database unreachable"
            : view === "empty" ? "No projects"
            : `${rows.length} project${rows.length === 1 ? "" : "s"}`}
        </span>
      </StatusBar>
    </div>
  );
}
