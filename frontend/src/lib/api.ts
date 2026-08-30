/* ===========================================================================
   Typed client for the FastAPI service.

   Same-origin in production (FastAPI mounts the build at /app), proxied in dev
   by vite.config.ts. Nothing here needs a base URL or a CORS shim.
   =========================================================================== */

export type ProcessingStatus =
  | "uploaded"
  | "processing"
  | "done"
  | "failed"
  | string;

export interface VideoRecord {
  video_id?: string;
  id?: string;
  user_id?: string;
  title?: string;
  original_language?: string;
  target_language?: string;
  duration?: string;
  processing_status?: ProcessingStatus;
  original_url?: string;
  dubbed_url?: string;
  error_message?: string;
  created_at?: string;
  /** The local project behind this row, when its working files still exist. */
  job_id?: string | null;
  openable?: boolean;
}

export interface Language {
  code: string;
  name: string;
  engine: string;
}

export interface SyncReport {
  ok: boolean;
  video: number | null;
  audio: number | null;
  delta: number | null;
  reason: string;
}

export interface JobStatus {
  job_id: string;
  status: "queued" | "processing" | "done" | "failed" | "cancelled";
  step: string;
  /** Which pipeline produced this job. */
  kind?: "dub" | "voiceover";
  /** Length of a rendered voice-over, in seconds. */
  voiceover_seconds?: number | null;
  url?: string | null;
  video_id?: string | null;
  translated_script?: string | null;
  original_text?: string | null;
  sync?: SyncReport;
  source_language?: string;
  segment_count?: number;
  /** Transcript of the clip the voice was cloned from. */
  reference_text?: string;
  reference_seconds?: number;
  /** How close the dub sounds to the reference speaker, 0..1, normalised
      between a same-speaker ceiling and a different-speaker floor. */
  voice_match?: { score: number; cosine: number } | null;
  /** Whether this run stored the timings that transcript export needs. */
  has_transcript?: boolean;
  /** Whether the phrase timeline was kept, so the script can be revised. */
  editable?: boolean;
  /** The picture is behind the audio until it is re-rendered. */
  video_stale?: boolean;
  /** Length of the clip this project was built from. */
  video_duration?: number | null;
  /** Playback URLs, present once the job finishes. */
  reference_audio?: string;
  dub_audio?: string;
  error?: string;
}

export interface Health {
  status: string;
  db: boolean;
  r2: boolean;
  active_jobs: number;
  /** Whether script rewriting is configured. Off unless a key is set, and the
      only stage that sends text off this machine. */
  script_intelligence?: boolean;
}

export interface FitResult {
  index: number;
  changed: boolean;
  text: string;
  slot_seconds: number;
  spoken_seconds: number;
  was_seconds?: number;
  fits?: boolean;
  attempts: Array<{ text: string; seconds: number; source: string }>;
  /** Words that may have been dropped rather than rephrased. Advisory. */
  check?: string[];
  detail?: string;
}

export class ApiError extends Error {
  // Declared as a field rather than a constructor parameter property: the
  // project builds with `erasableSyntaxOnly`, which bans syntax that emits
  // runtime code from a type position.
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(
      "Cannot reach the API. Start it with: ../venv/bin/python -m uvicorn app:app --port 8000"
    );
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* response had no JSON body */
    }
    throw new ApiError(detail, res.status);
  }
  return res.status === 204 ? (null as T) : ((await res.json()) as T);
}

/* --- identity --------------------------------------------------------------
   The backend previously attributed every job to the literal string
   "user-123", so all visitors shared one library. A per-browser id keeps
   libraries separate. This is scoping, not authentication: the app runs
   locally and there is no login. */
const ID_KEY = "multiva.uid";
const NAME_KEY = "multiva.name";

export function userId(): string {
  let id = localStorage.getItem(ID_KEY);
  if (!id) {
    id = crypto.randomUUID?.() ?? String(Date.now());
    localStorage.setItem(ID_KEY, id);
  }
  return id;
}
export const userName = () => localStorage.getItem(NAME_KEY) ?? "";
export const setUserName = (v: string) => localStorage.setItem(NAME_KEY, v);

/* --- endpoints ------------------------------------------------------------ */
export const getHealth = () => request<Health>("/api/health");
export const getLanguages = () => request<Language[]>("/languages");
export const getJob = (id: string) => request<JobStatus>(`/jobs/${id}/status`);

export const listVideos = () =>
  request<VideoRecord[]>(`/videos/?user_id=${encodeURIComponent(userId())}`);

export const removeVideo = (id: string) =>
  request<{ status: string }>(`/videos/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });

export interface RenderOptions {
  /** In and out points on the source, in seconds. */
  trimStart?: number | null;
  trimEnd?: number | null;
  /** A bed laid under the finished dub. */
  music?: File | null;
  musicGain?: number;
}

export async function submitVideo(
  file: File,
  sourceLang: string,
  targetLang: string,
  options: RenderOptions = {}
): Promise<{ job_id: string }> {
  const params = new URLSearchParams({
    original_language: sourceLang,
    target_language: targetLang,
    user_id: userId(),
  });
  if (options.trimStart != null) params.set("trim_start", String(options.trimStart));
  if (options.trimEnd != null) params.set("trim_end", String(options.trimEnd));
  if (options.music) params.set("music_gain", String(options.musicGain ?? -18));

  const body = new FormData();
  body.append("file", file);
  if (options.music) body.append("music", options.music);

  return request<{ job_id: string }>(`/process_video/?${params}`, {
    method: "POST",
    body,
  });
}

/* --- revising a finished dub ---------------------------------------------- */

export interface Phrase {
  index: number;
  start: number;
  duration: number;
  text: string;
  source_text: string;
  seed?: number | null;
}

export interface PhraseTimeline {
  video_duration: number;
  video_stale: boolean;
  target_language: string;
  segments: Phrase[];
}

export interface PhraseResult {
  index: number;
  text: string;
  seed?: number | null;
  spoken_seconds: number;
  slot_seconds: number;
  /** The phrase filled its slot and was shortened to fit. */
  overruns: boolean;
  track_seconds: number;
}

export const getPhrases = (id: string) =>
  request<PhraseTimeline>(`/jobs/${encodeURIComponent(id)}/segments`);

export const revisePhrase = (
  id: string,
  index: number,
  body: { text?: string; seed?: number }
) =>
  request<PhraseResult>(`/jobs/${encodeURIComponent(id)}/segments/${index}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/** Rewrite a phrase until it fits its slot when spoken. */
export const fitPhrase = (id: string, index: number) =>
  request<FitResult>(`/jobs/${encodeURIComponent(id)}/segments/${index}/fit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });

export const phraseAudioUrl = (id: string, index: number) =>
  `/jobs/${encodeURIComponent(id)}/segments/${index}/audio`;

export const rerenderVideo = (id: string) =>
  request<{ job_id: string; status: string }>(
    `/jobs/${encodeURIComponent(id)}/rerender`, { method: "POST" });

export interface ReferenceWindow {
  start: number;
  duration: number;
  score: number;
  text: string;
}

export const getReferenceWindows = (id: string) =>
  request<{ current: { start?: number; duration?: number; text: string };
            candidates: ReferenceWindow[] }>(
    `/jobs/${encodeURIComponent(id)}/reference/candidates`);

export const chooseReference = (id: string, start: number, duration: number) =>
  request<{ job_id: string; status: string }>(
    `/jobs/${encodeURIComponent(id)}/reference`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start, duration }),
    });

/* --- engines -------------------------------------------------------------- */

export interface EngineOption {
  id: string;
  label: string;
  size: string;
  note?: string;
  /** A reason not to pick this, shown in place of the note. */
  warn?: string;
  /** False means it is not on disk yet. */
  ready: boolean;
  /** How it is obtained: fetched from the Hub, or placed by hand. */
  source: "download" | "manual" | "builtin";
  /** Where a manual file belongs, relative to Backend_pipeline. */
  local?: string;
}

export interface EngineStage {
  label: string;
  why: string;
  default: string;
  current: string;
  /** Fixed by an environment variable; the UI cannot change it. */
  pinned: boolean;
  options: EngineOption[];
}

export interface EngineSettings {
  configured: boolean;
  stages: Record<string, EngineStage>;
}

export const getEngines = () => request<EngineSettings>("/api/settings/engines");

export const saveEngines = (choices: Record<string, string>) =>
  request<EngineSettings & { restart_required: boolean }>(
    "/api/settings/engines", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(choices),
    });

/* --- script intelligence -------------------------------------------------- */

export interface LlmProvider {
  label: string;
  needs_key: boolean;
  /** Needs a base URL as well as a key: any OpenAI-compatible endpoint. */
  needs_url?: boolean;
  default_model: string;
  suggested: string[];
  note: string;
}

export interface LlmStatus {
  enabled: boolean;
  provider: string;
  model: string;
  /** True when the model runs on this machine and nothing is sent anywhere. */
  local: boolean;
  has_key: boolean;
  ollama_host: string;
  custom_url: string;
  providers: Record<string, LlmProvider>;
  /** Models Ollama has actually pulled. Empty for hosted providers. */
  installed: string[];
}

export const getLlmSettings = () => request<LlmStatus>("/api/settings/llm");

export const saveLlmSettings = (body: {
  provider: string;
  model: string;
  /** Omit to keep the stored key; send "" to delete it. */
  api_key?: string;
  ollama_host?: string;
  custom_url?: string;
}) =>
  request<LlmStatus>("/api/settings/llm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const testLlmSettings = () =>
  request<LlmStatus & { ok: boolean; reply: string }>(
    "/api/settings/llm/test", { method: "POST" });

/** Ask a running job to stop. Cooperative: it lands at the next stage report. */
export const cancelJob = (id: string) =>
  request<{ status: string; cancelled: boolean }>(
    `/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });

/** Speak a typed script in the voice of an uploaded reference clip. */
export async function submitVoiceover(
  file: File,
  script: string,
  language: string
): Promise<{ job_id: string }> {
  const params = new URLSearchParams({
    language,
    user_id: userId(),
  });
  const body = new FormData();
  body.append("file", file);
  body.append("script", script);
  return request<{ job_id: string }>(`/voiceover/?${params}`, {
    method: "POST",
    body,
  });
}

/* --- display helpers ------------------------------------------------------ */
const LANG_NAMES: Record<string, string> = {
  hi: "Hindi", mr: "Marathi", ta: "Tamil", te: "Telugu", kn: "Kannada",
  bn: "Bengali", gu: "Gujarati", ml: "Malayalam", pa: "Punjabi", or: "Odia",
  as: "Assamese", ur: "Urdu", en: "English", es: "Spanish", fr: "French",
  de: "German", zh: "Chinese", ja: "Japanese", ko: "Korean", ar: "Arabic",
};
export const langName = (code?: string) =>
  (code && LANG_NAMES[code]) || code?.toUpperCase() || "Unknown";

/** File kinds the backend can build from a finished job's stored timings. */
export const EXPORTS = [
  { kind: "dub.srt", label: "Subtitles (SRT)", needsTranslation: true },
  { kind: "dub.vtt", label: "Subtitles (VTT)", needsTranslation: true },
  { kind: "source.srt", label: "Source subtitles", needsTranslation: false },
  { kind: "translation.txt", label: "Translated script", needsTranslation: true },
  { kind: "transcript.txt", label: "Source transcript", needsTranslation: false },
] as const;

export const exportUrl = (jobId: string, kind: string) =>
  `/jobs/${encodeURIComponent(jobId)}/export/${encodeURIComponent(kind)}`;

/** "4 minutes ago" reads better than a date on a project you just made. */
export function relativeTime(iso?: string): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  return formatDate(iso);
}

export const formatDate = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString(undefined, {
        day: "numeric", month: "short", year: "numeric",
      });
};
