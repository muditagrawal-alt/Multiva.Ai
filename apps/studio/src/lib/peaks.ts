/* ===========================================================================
   Real waveform peaks.

   The landing page draws a stylised waveform from a seeded PRNG, which is
   honest there — it illustrates a claim. A timeline is different: a drawn
   waveform is read as a measurement of the audio, so this decodes the actual
   file rather than inventing a shape.
   =========================================================================== */

let ctx: AudioContext | null = null;

function audio(): AudioContext | null {
  if (ctx) return ctx;
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  ctx = new Ctor();
  return ctx;
}

export interface Peaks {
  /** Max absolute amplitude per bucket, 0..1. */
  values: Float32Array;
  seconds: number;
}

const cache = new Map<string, Promise<Peaks | null>>();

/**
 * Decode `url` and reduce it to `buckets` peak values. Results are cached per
 * URL because the same track is drawn in several lanes and redrawn on resize.
 */
export function loadPeaks(url: string, buckets = 1200): Promise<Peaks | null> {
  const key = `${url}#${buckets}`;
  const hit = cache.get(key);
  if (hit) return hit;

  const job = (async (): Promise<Peaks | null> => {
    const ac = audio();
    if (!ac) return null;
    try {
      const res = await fetch(url);
      if (!res.ok) return null;
      const buf = await ac.decodeAudioData(await res.arrayBuffer());
      const src = buf.getChannelData(0);
      const per = src.length / buckets;
      const values = new Float32Array(buckets);
      for (let b = 0; b < buckets; b++) {
        const from = Math.floor(b * per);
        const to = Math.min(src.length, Math.floor((b + 1) * per));
        let peak = 0;
        for (let i = from; i < to; i++) {
          const v = src[i] < 0 ? -src[i] : src[i];
          if (v > peak) peak = v;
        }
        values[b] = peak;
      }
      return { values, seconds: buf.duration };
    } catch {
      // A track that will not decode simply has no waveform; the clip block
      // still renders with its duration.
      return null;
    }
  })();

  cache.set(key, job);
  return job;
}
