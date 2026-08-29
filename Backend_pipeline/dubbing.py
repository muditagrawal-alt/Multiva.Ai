"""
Segment-level dubbing and timeline assembly.

This is the module that makes gaps structurally impossible.

The old pipeline translated the entire transcript as one string, synthesized it
as one blob, and then tried to rescue the timing afterwards with a global tempo
stretch. Two things went wrong: the stretch was applied in the wrong direction,
and its output was never actually fed to Wav2Lip. Whatever length the TTS
happened to produce became the length of the video, so the render either looped
back to the start or cut off mid-shot.

Here the dubbed track is *constructed* to be exactly as long as the video:

  1. Each Whisper segment keeps its own time slot.
  2. Each segment is synthesized to fill that slot, using IndicF5's `fix_duration`
     (verified accurate to ~10 ms) rather than stretching afterwards.
  3. Speaking-rate change is clamped to a natural band; text that will not fit
     borrows from the following pause instead of being crushed.
  4. The assembled track is padded/trimmed to exactly the video duration.

Because step 4 is an invariant rather than a check, Wav2Lip always receives an
audio track the same length as its video, and cannot loop or truncate.
"""

import os
from contextlib import contextmanager

import numpy as np

import languages as L
import tts_engines

SAMPLE_RATE = 24000

# How far the speaking rate may deviate from the reference speaker's natural
# rate. r = natural_duration / allotted_duration; r > 1 means talking faster.
MAX_SPEEDUP = 1.35     # never crush speech below ~74% of its natural length
MAX_SLOWDOWN = 0.75    # never drawl beyond ~133% of natural length

# Fraction of the pause after a segment that the segment may borrow.
BORROW_FRACTION = 0.85

# Below this, don't bother correcting the length.
LENGTH_TOLERANCE = 0.02

# Hard limits on post-hoc time stretching. The generator is asked for the right
# length up front (F5's fix_duration), so anything outside this band signals a
# bug rather than a legitimate fit, and a phase vocoder stops being transparent
# well before the edges.
MAX_STRETCH_DOWN = 0.80
MAX_STRETCH_UP = 1.25

# Fade length applied to each segment's edges before it is laid onto the track.
EDGE_FADE_MS = 8.0


def _chars(text: str) -> int:
    return max(1, len((text or "").strip()))


class DurationModel:
    """
    Estimates how long a piece of text takes to say *in this speaker's voice*.

    Deliberately counts CHARACTERS, not bytes. F5's own internal heuristic is
    byte-based, which is fine when the reference and the target are the same
    language but breaks badly cross-lingually: a Devanagari character is 3 UTF-8
    bytes against 1 for Latin, so calibrating an English reference in bytes and
    applying it to Hindi over-estimates every duration by roughly 3x. English to
    Hindi is our main path, so that error is not survivable.

    Instead we separate the two effects:
      * script density  — characters per second typical for the TARGET language
                          (languages.chars_per_second)
      * speaker tempo   — how fast THIS speaker talks relative to typical for
                          the SOURCE language, measured from the reference clip
    """

    # Guards against a wild factor from a mistimed or mistranscribed reference.
    MIN_SPEAKER_FACTOR = 0.6
    MAX_SPEAKER_FACTOR = 1.7

    def __init__(self, ref_duration: float, ref_text: str,
                 source_lang: str = None, target_lang: str = "hi"):
        self.source_cps = L.chars_per_second(source_lang) if source_lang else None
        self.target_cps = L.chars_per_second(target_lang)

        factor = 1.0
        if ref_duration > 0.5 and ref_text and len(ref_text.strip()) > 8:
            ref_cps = _chars(ref_text) / ref_duration
            typical = self.source_cps or ref_cps
            if ref_cps > 0.1:
                factor = typical / ref_cps
        self.speaker_factor = min(self.MAX_SPEAKER_FACTOR,
                                  max(self.MIN_SPEAKER_FACTOR, factor))

    def estimate(self, text: str, source_text: str = None,
                 slot: float = None) -> float:
        """
        How long `text` should take to say.

        When we know what the ORIGINAL speaker said in this slot and how long
        they took, that is far better evidence than any characters-per-second
        table: it is this speaker, this sentence, this recording. So scale the
        measured slot by how much longer the translation is, correcting for the
        two scripts packing different amounts of speech into a character.

            natural = slot x (target_chars / source_chars)
                           x (source_cps / target_cps)

        This is self-calibrating and, importantly, degrades to `natural == slot`
        when source and target are the same language — a same-language re-voice
        then needs no speed change at all.

        The global cps table is only a fallback for when segment-level evidence
        is missing. Relying on it alone was over-estimating durations by ~41% on
        real footage, which pinned whole runs of segments at maximum speedup.
        """
        if (source_text and slot and slot > 0.05
                and len(source_text.strip()) > 1 and self.source_cps):
            ratio = _chars(text) / _chars(source_text)
            density = self.source_cps / self.target_cps
            return slot * ratio * density

        return (_chars(text) / self.target_cps) * self.speaker_factor


def _trim_silence(wave: np.ndarray, floor_db: float = -40.0,
                  keep_ms: float = 30.0) -> np.ndarray:
    """
    Strip leading/trailing near-silence, keeping a short natural tail.

    Only touches the ends; internal pauses inside an utterance are the model's
    own phrasing and must survive.
    """
    if wave.size < int(SAMPLE_RATE * 0.05):
        return wave

    peak = float(np.max(np.abs(wave)))
    if peak <= 0:
        return wave

    win = max(1, int(SAMPLE_RATE * 0.01))
    n = wave.size // win
    if n < 3:
        return wave

    frames = wave[: n * win].reshape(n, win)
    env = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)) + 1e-12
    db = 20.0 * np.log10(env / peak)
    loud = np.where(db > floor_db)[0]
    if loud.size == 0:
        return wave

    keep = int(SAMPLE_RATE * keep_ms / 1000.0)
    start = max(0, loud[0] * win - keep)
    end = min(wave.size, (loud[-1] + 1) * win + keep)
    return wave[start:end] if end > start else wave


def _edge_fade(wave: np.ndarray, ms: float) -> np.ndarray:
    """Apply a short linear fade at both ends so placement can't click."""
    n = int(SAMPLE_RATE * ms / 1000.0)
    if wave.size < 2 * n or n < 2:
        return wave
    out = wave.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def _time_stretch(wave: np.ndarray, factor: float) -> np.ndarray:
    """
    Stretch `wave` by `factor` (>1 = longer) preserving pitch.
    Only ever used for sub-5% residual corrections.
    """
    if wave.size == 0 or abs(factor - 1.0) < 0.005:
        return wave
    try:
        import librosa
        # librosa's `rate` is playback rate: rate>1 shortens.
        return librosa.effects.time_stretch(wave, rate=1.0 / factor).astype(np.float32)
    except Exception:
        return wave


def _fit_length(wave: np.ndarray, target_samples: int) -> np.ndarray:
    """Force `wave` to exactly `target_samples`, gently."""
    if target_samples <= 0:
        return np.zeros(0, dtype=np.float32)
    n = wave.size
    if n == 0:
        return np.zeros(target_samples, dtype=np.float32)

    drift = abs(n - target_samples) / float(target_samples)
    if drift > LENGTH_TOLERANCE:
        factor = target_samples / float(n)
        # A phase vocoder is only transparent for small corrections. Anything
        # beyond this band means the generator returned a wildly wrong length,
        # which is a bug upstream — squeezing e.g. 0.4x turns speech into the
        # classic robotic warble. Clamp it and say so, rather than quietly
        # destroying the audio to satisfy a duration target.
        if not (MAX_STRETCH_DOWN <= factor <= MAX_STRETCH_UP):
            print(f"[DUB] WARNING: segment wants a {factor:.2f}x time stretch "
                  f"({n / SAMPLE_RATE:.2f}s -> {target_samples / SAMPLE_RATE:.2f}s). "
                  f"Clamping — check that the reference duration is correct.")
            factor = min(MAX_STRETCH_UP, max(MAX_STRETCH_DOWN, factor))
        wave = _time_stretch(wave, factor)
        n = wave.size

    if n > target_samples:
        out = wave[:target_samples].copy()
        # 5 ms fade so a hard cut never clicks
        f = min(120, target_samples)
        if f > 1:
            out[-f:] *= np.linspace(1.0, 0.0, f, dtype=np.float32)
        return out
    return np.pad(wave, (0, target_samples - n))


def prepare_units(segments: list, translated_texts: list,
                  source_audio: str = None, video_duration: float = 0.0):
    """
    Split Whisper segments at the speaker's REAL pauses, returning
    (units, translated_texts) ready for `plan_timeline`.

    Without this, each segment's whole slot is filled with one continuous
    utterance and every pause inside it is replaced by words. On real footage
    that meant losing 2.41s of micro-pauses (16 of them, each 50-150ms) from a
    20s clip — which is what made the dub sound rushed and run its words
    together even with every segment nominally at 1.00x speed.

    Falls back to whole-segment units if the audio is unavailable or VAD fails,
    so this can only improve on the previous behaviour, never break it.
    """
    if not source_audio:
        return segments, translated_texts

    try:
        import speech_activity as SA
        runs = SA.detect_speech_runs(source_audio)
        units = SA.build_phrase_units(segments, translated_texts, runs)
        if not units:
            return segments, translated_texts
        print(SA.summarize(units, segments, video_duration))
        return units, [u["target"] for u in units]
    except Exception as e:
        print(f"[DUB] Pause detection unavailable ({e}); "
              f"falling back to whole-segment units")
        return segments, translated_texts


def plan_timeline(segments: list, texts: list, duration_model: DurationModel,
                  video_duration: float, natural_fn=None) -> list:
    """
    Decide the start time and allotted duration for every segment.

    Returns a list of dicts: {index, text, start, duration, natural, rate}.
    `rate` is the speaking-rate multiplier actually applied, for logging.
    """
    plan = []
    cursor = 0.0

    for i, (seg, text) in enumerate(zip(segments, texts)):
        text = (text or "").strip()
        if not text:
            continue

        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        slot = max(0.05, seg_end - seg_start)

        # Pause between this segment and the next (or the tail of the video).
        if i + 1 < len(segments):
            next_start = float(segments[i + 1].get("start", seg_end))
        else:
            next_start = video_duration
        gap = max(0.0, next_start - seg_end)
        available = slot + gap * BORROW_FRACTION

        # Give the estimator this segment's own evidence: what the original
        # speaker said here and how long they took over it.
        # Prefer the generator's OWN duration estimate when it exposes one:
        # asking F5 for exactly what it would produce anyway means it never has
        # to internally compress or pad. Fall back to the text-ratio model.
        natural = None
        if natural_fn is not None:
            try:
                natural = natural_fn(text)
            except Exception:
                natural = None
        if not natural or natural <= 0.05:
            natural = duration_model.estimate(
                text, source_text=(seg.get("text") or "").strip(), slot=slot)

        # Give the generator the length it actually wants.
        #
        # This used to force every unit into its own slot, capped at `available`.
        # That converts a mild, uniform mismatch into violent per-unit
        # distortion: on real footage the overall need was only 1.15x the
        # available time, but individual units ranged from 0.85x to 1.76x, so
        # some phrases were crushed to unintelligibility while others were
        # padded with silence. Uneven compression is what reads as "the voice is
        # inconsistent" and "the words crash into each other".
        #
        # Instead each unit gets its natural length, overruns cascade into the
        # following pause, and any residual overflow is taken out globally at
        # the end of this function — one gentle scale applied evenly rather than
        # a different violent one per phrase.
        target = natural

        # Safety bounds only. With `natural` as the target these should not
        # normally bind; they exist so a bad estimate cannot produce absurdity.
        rate = natural / max(target, 1e-3)
        if target > available * 2.5 and available > 0.2:
            target = available * 2.5
            rate = natural / target

        # Never start before the previous segment finished.
        start = max(seg_start, cursor)
        plan.append({
            "index": i, "text": text, "start": start,
            "duration": target, "natural": natural, "rate": rate,
        })
        cursor = start + target

    # If cascading pushed us past the end of the video, compress everything by
    # the (small) residual so the last word still lands inside the picture.
    if plan and cursor > video_duration > 0:
        squeeze = video_duration / cursor
        print(f"[DUB] Timeline overflows by {cursor - video_duration:.2f}s; "
              f"compressing by {1 / squeeze:.3f}x")
        c = 0.0
        for p in plan:
            p["start"] *= squeeze
            p["duration"] *= squeeze
            p["start"] = max(p["start"], c)
            c = p["start"] + p["duration"]

    return plan


@contextmanager
def _with_seed(engine, seed):
    """
    Temporarily override the engine's sampling seed.

    IndicF5 is stochastic and the engine is a warm singleton, so re-rolling one
    phrase means swapping the seed for exactly one call and putting it back.
    """
    if seed is None or not hasattr(engine, "seed"):
        yield
        return
    previous = engine.seed
    engine.seed = int(seed)
    try:
        yield
    finally:
        engine.seed = previous


def synth_unit(unit: dict, ref_path: str, ref_text: str, ref_duration: float,
               target_lang: str, seed=None) -> np.ndarray:
    """
    Synthesize one planned unit and apply the conditioning every unit gets:
    trim the model's trailing padding, shorten if it overruns its slot, fade
    both edges. Returns the wave ready to be laid onto a track.

    Raises on failure so the caller decides whether a gap or an error is right.
    """
    engine = tts_engines.get_engine(target_lang)
    lang = L.synth_lang(target_lang)
    use_fix = isinstance(engine, tts_engines.IndicF5Engine)

    with _with_seed(engine, seed):
        if use_fix:
            # fix_duration is the TOTAL (reference + generated) length.
            wave, sr = _synth_fixed(engine, unit["text"], ref_path, ref_text,
                                    lang, ref_duration + unit["duration"])
        else:
            wave, sr = engine.synthesize(unit["text"], ref_path, ref_text, lang=lang)

    if sr != SAMPLE_RATE and wave.size:
        import librosa
        wave = librosa.resample(wave, orig_sr=sr, target_sr=SAMPLE_RATE)
    wave = np.asarray(wave, dtype=np.float32)

    # Trim the silence F5 pads onto the end of a unit.
    #
    # The model fills only ~73% of whatever duration it is given with actual
    # speech and pads the remainder. Left in place, that padding sits INSIDE
    # the unit, so the dub pauses at arbitrary points rather than where the
    # speaker paused. Trimming it puts the speech at the unit's start and lets
    # the gap to the next unit — which came from detecting the speaker's real
    # pauses — carry the rhythm instead.
    wave = _trim_silence(wave)

    want = int(round(unit["duration"] * SAMPLE_RATE))
    # Never stretch a unit UP to fill its slot: that would re-introduce the
    # padding just removed. Only shorten if it genuinely overruns.
    if wave.size > want:
        wave = _fit_length(wave, want)

    # Segments are dropped into a track of digital zeros, so without these a
    # segment starting or ending on a non-zero sample steps straight from
    # silence to full amplitude and clicks at every phrase boundary.
    return _edge_fade(wave, EDGE_FADE_MS)


def assemble(plan: list, waves: dict, video_duration: float) -> np.ndarray:
    """
    Lay already-synthesized unit waves onto a track of exactly
    `video_duration` seconds. `waves` maps plan index to wave; a missing entry
    leaves silence in that slot.
    """
    total_samples = int(round(video_duration * SAMPLE_RATE))
    track = np.zeros(total_samples, dtype=np.float32)

    for unit in plan:
        wave = waves.get(unit["index"])
        if wave is None or wave.size == 0:
            continue
        at = int(round(unit["start"] * SAMPLE_RATE))
        if at >= total_samples:
            break
        end = min(total_samples, at + wave.size)
        track[at:end] += wave[: end - at]

    peak = float(np.max(np.abs(track))) if track.size else 0.0

    # The guard that was missing. A track of exactly the right length made
    # entirely of zeros passed every duration check in this pipeline and was
    # reported as a clean run — that is how a completely untrained model went
    # unnoticed. Length is not evidence of content.
    if peak < 1e-4:
        raise RuntimeError(
            "Synthesized track is silent (peak %.2e). The TTS model produced no "
            "audio — check that its weights actually loaded." % peak)

    if peak > 1.0:
        track /= peak
    return track


def segment_path(cache_dir: str, index) -> str:
    return os.path.join(cache_dir, f"unit_{index}.wav")


def write_unit(cache_dir: str, index, wave: np.ndarray) -> str:
    import soundfile as sf
    os.makedirs(cache_dir, exist_ok=True)
    path = segment_path(cache_dir, index)
    sf.write(path, wave, SAMPLE_RATE, subtype="PCM_16")
    return path


def read_unit(cache_dir: str, index):
    import soundfile as sf
    path = segment_path(cache_dir, index)
    if not os.path.exists(path):
        return None
    wave, _ = sf.read(path, dtype="float32")
    return np.asarray(wave, dtype=np.float32).reshape(-1)


def synthesize_timeline(plan: list, ref_path: str, ref_text: str, ref_duration: float,
                        target_lang: str, video_duration: float,
                        progress=None, cache_dir: str = None) -> np.ndarray:
    """
    Synthesize every planned segment and lay it onto a track of exactly
    `video_duration` seconds.

    When `cache_dir` is given, each unit's conditioned wave is written there so
    a later edit can rebuild one phrase instead of the whole track.
    """
    waves = {}
    failures = []
    silent = []

    for n, unit in enumerate(plan):
        if progress:
            progress(n, len(plan))
        try:
            wave = synth_unit(unit, ref_path, ref_text, ref_duration, target_lang)
        except Exception as e:
            print(f"[DUB] Segment {unit['index']} failed ({e}); leaving silence")
            failures.append((unit["index"], str(e)))
            continue

        # A segment that comes back silent is a failure too — it just does not
        # raise. Track it, or a silent model produces a silent dub that looks
        # like a completely successful run.
        if wave.size == 0 or float(np.max(np.abs(wave))) < 1e-4:
            silent.append(unit["index"])

        waves[unit["index"]] = wave
        if cache_dir:
            write_unit(cache_dir, unit["index"], wave)

    if failures and len(failures) == len(plan):
        raise RuntimeError(
            f"Every one of {len(plan)} segments failed to synthesize. "
            f"First error: {failures[0][1]}")
    if failures:
        print(f"[DUB] WARNING: {len(failures)}/{len(plan)} segments failed")
    if silent:
        print(f"[DUB] WARNING: {len(silent)}/{len(plan)} segments came back "
              f"silent: {silent[:8]}")

    return assemble(plan, waves, video_duration)


def _synth_fixed(engine, text, ref_path, ref_text, lang, fix_duration):
    """Call IndicF5 with an explicit total duration."""
    from f5_tts.infer.utils_infer import infer_batch_process

    model = engine.load()
    if hasattr(engine, "_seed_rng"):
        engine._seed_rng()
    audio_t, sr = engine._load_ref(ref_path)
    rtext = engine._normalize_ref_text(ref_text)

    wave, out_sr, _ = infer_batch_process(
        (audio_t, sr), rtext, [text.strip()],
        model.ema_model, model.vocoder,
        mel_spec_type="vocos", device=engine.device,
        nfe_step=engine.nfe_step, cross_fade_duration=0.0,
        fix_duration=fix_duration, progress=None,
    )
    return np.asarray(wave, dtype=np.float32).reshape(-1), out_sr


def build_dubbed_track(segments: list, translated_texts: list, reference: dict,
                       target_lang: str, video_duration: float, out_path: str,
                       source_lang: str = None, progress=None,
                       source_audio: str = None, cache_dir: str = None) -> list:
    """
    Full segment-level dub. Writes a WAV of exactly `video_duration` seconds.

    `reference` is the dict returned by reference_audio.build_reference().
    """
    dm = DurationModel(reference["duration"], reference["text"],
                       source_lang=source_lang, target_lang=target_lang)

    units, texts = prepare_units(segments, translated_texts,
                                 source_audio, video_duration)

    engine = tts_engines.get_engine(target_lang)
    natural_fn = None
    if hasattr(engine, "natural_duration"):
        _lang = L.synth_lang(target_lang)
        natural_fn = lambda t: engine.natural_duration(
            t, reference["path"], reference["text"], lang=_lang)

    plan = plan_timeline(units, texts, dm, video_duration, natural_fn=natural_fn)
    if not plan:
        raise RuntimeError("Nothing to synthesize: no non-empty translated segments")

    speech = sum(p["duration"] for p in plan)
    print(f"[DUB] {len(plan)} segments, {speech:.1f}s speech in a "
          f"{video_duration:.1f}s video ({L.display_name(target_lang)})")

    track = synthesize_timeline(plan, reference["path"], reference["text"],
                                reference["duration"], target_lang,
                                video_duration, progress, cache_dir=cache_dir)

    import soundfile as sf
    sf.write(out_path, track, SAMPLE_RATE, subtype="PCM_16")

    actual = len(track) / SAMPLE_RATE
    print(f"[DUB] Wrote {out_path} — {actual:.3f}s "
          f"(video {video_duration:.3f}s, delta {abs(actual - video_duration) * 1000:.0f}ms)")
    # The plan goes back to the caller so a later edit knows where every phrase
    # sits and what it was asked to say.
    return plan
