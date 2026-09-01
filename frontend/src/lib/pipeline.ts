/* ===========================================================================
   The render pipeline, as the backend actually reports it.

   app.py writes a `step` string at each stage (see `_set(job_id, step=...)`).
   Two of those stages also carry a real counter in parentheses:

       synthesizing_voice (7/12)
       lip_syncing (240/512 frames)

   So progress here is not a timer counting to 100. Stage position is exact,
   and inside the two longest stages the fraction is exact too. The only
   estimated part is how much wall time each stage takes relative to the
   others, which is what `weight` holds.
   =========================================================================== */

export interface Stage {
  /** The literal `step` value app.py reports. */
  key: string;
  /** Sentence form, for the status line. */
  label: string;
  /** Short form, for the timeline block. */
  block: string;
  /**
   * Share of total wall time. Calibrated from measured runs on Apple silicon:
   * synthesis dominates at roughly six times realtime and lip sync runs at
   * about 1.4x, so equal weighting would park the bar mid-way for minutes.
   */
  weight: number;
}

export const DUB_STAGES: Stage[] = [
  { key: "validating",         label: "Checking the file",        block: "CHECK", weight: 0.01 },
  { key: "extracting_audio",   label: "Extracting audio",         block: "AUDIO", weight: 0.02 },
  { key: "transcribing",       label: "Transcribing",             block: "ASR",   weight: 0.10 },
  { key: "selecting_reference",label: "Choosing a voice reference",block: "REF",   weight: 0.04 },
  { key: "translating",        label: "Translating",              block: "MT",    weight: 0.06 },
  { key: "synthesizing_voice", label: "Cloning the voice",        block: "TTS",   weight: 0.52 },
  { key: "lip_syncing",        label: "Re-syncing lips",          block: "LIPS",  weight: 0.20 },
  { key: "verifying",          label: "Checking sync",            block: "SYNC",  weight: 0.03 },
  { key: "uploading_result",   label: "Finishing",                block: "OUT",   weight: 0.02 },
];

/**
 * A voice-over supplies its own words and has no video to stay in sync with,
 * so it skips translation, timeline fitting and lip sync. Synthesis is nearly
 * the whole run.
 */
export const VOICEOVER_STAGES: Stage[] = [
  { key: "validating",         label: "Checking the file",        block: "CHECK", weight: 0.02 },
  { key: "extracting_audio",   label: "Extracting audio",         block: "AUDIO", weight: 0.03 },
  { key: "transcribing",       label: "Reading the reference",    block: "ASR",   weight: 0.15 },
  { key: "selecting_reference",label: "Choosing a voice reference",block: "REF",   weight: 0.05 },
  { key: "synthesizing_voice", label: "Speaking the script",      block: "TTS",   weight: 0.75 },
];

export type JobKind =
  | "dub"
  | "voiceover"
  | "audio"
  | "subtitles"
  | "subtitles_translated";

/**
 * The dub pipeline stopped at a stage, with the weights renormalised so the
 * bar still fills to one. These outputs run exactly the dub's stages and then
 * stop, so describing them as a slice is the truth rather than a convenience.
 */
function upTo(key: string): Stage[] {
  const cut = DUB_STAGES.slice(0, DUB_STAGES.findIndex((s) => s.key === key) + 1);
  const total = cut.reduce((n, s) => n + s.weight, 0) || 1;
  return cut.map((s) => ({ ...s, weight: s.weight / total }));
}

export const SUBTITLE_STAGES = upTo("transcribing");
export const TRANSLATED_SUBTITLE_STAGES = upTo("translating");
export const AUDIO_DUB_STAGES = upTo("synthesizing_voice");

export const stagesFor = (kind: JobKind): Stage[] =>
  kind === "voiceover" ? VOICEOVER_STAGES
    : kind === "subtitles" ? SUBTITLE_STAGES
      : kind === "subtitles_translated" ? TRANSLATED_SUBTITLE_STAGES
        : kind === "audio" ? AUDIO_DUB_STAGES
          : DUB_STAGES;

export interface Progress {
  /** Index into STAGES, or -1 before the first report. */
  index: number;
  /** 0..1 across the whole run. */
  fraction: number;
  /** Exact position inside the current stage when it reports one. */
  detail: string;
  done: number | null;
  total: number | null;
}

function cumulative(stages: Stage[]): number[] {
  return stages.reduce<number[]>((acc, st, i) => {
    acc.push((acc[i - 1] ?? 0) + st.weight);
    return acc;
  }, []);
}

/**
 * Turn a raw `step` string into a position. Handles both counter shapes the
 * backend emits and the bare stage name.
 */
export function readProgress(
  step: string | undefined,
  status?: string,
  kind: JobKind = "dub"
): Progress {
  const stages = stagesFor(kind);

  if (status === "done") {
    return { index: stages.length - 1, fraction: 1, detail: "", done: null, total: null };
  }

  const raw = step ?? "";
  const key = raw.replace(/\s*\(.*$/, "").trim();
  const index = stages.findIndex((st) => st.key === key);
  if (index < 0) {
    // "queued", "complete", "cancelled", "error", or a stage added to the
    // backend since this table was written. Report no position rather than
    // guess one.
    return { index: -1, fraction: 0, detail: "", done: null, total: null };
  }

  const inside = raw.match(/\((\d+)\s*\/\s*(\d+)/);
  const done = inside ? Number(inside[1]) : null;
  const total = inside ? Number(inside[2]) : null;
  const within = done != null && total ? Math.min(1, done / total) : 0;

  const cum = cumulative(stages);
  const before = index === 0 ? 0 : cum[index - 1];
  const fraction = Math.min(1, before + stages[index].weight * within);

  return {
    index,
    fraction,
    detail: raw.match(/\(([^)]*)\)/)?.[1] ?? "",
    done,
    total,
  };
}

export const percent = (fraction: number) => Math.round(fraction * 100);
