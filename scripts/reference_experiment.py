#!/usr/bin/env python3
"""
Does the automatic reference window actually produce the best clone?

`reference_audio.py` says in its own comments that this single choice drives
most of the cloning quality, and the selection is a heuristic score that has
never been validated. This renders the same dub from each of the top candidate
windows and measures the result.

Method
------
Everything up to synthesis is done once and shared: extract, transcribe,
translate, plan the timeline. Only the reference and the synthesis change per
variant, so the comparison isolates the one variable.

Each variant's dub is scored against the SAME target - the speaker's own source
audio. Scoring a dub against its own reference would let a window flatter
itself; scoring every variant against the speaker answers the question that
actually matters, which is whether the dub sounds like the person.

    python scripts/reference_experiment.py <video> [--lang hi] [--top 4]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Backend_pipeline"))

import av_sync                                               # noqa: E402
import dubbing                                               # noqa: E402
import languages as L                                        # noqa: E402
import reference_audio                                       # noqa: E402
from speech_to_text_v2 import transcribe_audio               # noqa: E402
from translation_v2 import translate_segments, fix_code_switching  # noqa: E402
from eval_harness import SpeakerScorer                       # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--lang", default="hi")
    ap.add_argument("--top", type=int, default=4)
    ap.add_argument("--out", default="/tmp/ref_experiment")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.video))[0]
    target = L.normalize(args.lang)

    print(f"\n  Reference window experiment - {os.path.basename(args.video)}")
    print("  " + "=" * 68)

    # ---- shared prefix: everything that must not vary between variants ----
    dur = av_sync.duration(args.video)
    stt_audio = av_sync.extract_audio(
        args.video, os.path.join(args.out, f"{stem}_stt.wav"), 16000, 1)
    print(f"  Source: {dur:.2f}s")

    print("  Transcribing...")
    result = transcribe_audio(stt_audio)
    segments = result.get("segments") or []
    source_lang = L.normalize(result.get("language") or "en")
    if not segments:
        print("  No speech found.")
        return 1

    print(f"  Translating {len(segments)} segments {source_lang} -> {target}...")
    translated = fix_code_switching(
        translate_segments(segments, source_lang, target), target)

    windows = reference_audio.candidate_windows(segments, dur, limit=args.top)
    auto = reference_audio.find_best_window(segments, dur)
    auto_start = round(max(0.0, auto[0] - 0.10), 2) if auto else None
    if not windows:
        print("  No candidate windows.")
        return 1

    scorer = SpeakerScorer()
    speaker = scorer.embed(stt_audio)          # the fixed target for every variant
    if speaker is None:
        print("  Could not embed the source audio.")
        return 1

    print(f"\n  {len(windows)} candidate windows. Auto-selected starts at "
          f"{auto_start}s.\n")
    print(f"  {'#':<3}{'window':>16}{'score':>9}{'sounds like':>13}{'ref quality':>13}  text")
    print("  " + "-" * 84)

    rows = []
    for i, w in enumerate(windows):
        ref_path = os.path.join(args.out, f"{stem}_ref{i}.wav")
        reference_audio.extract_reference(
            args.video, w["start"], w["duration"], ref_path, sample_rate=24000)
        actual = av_sync.duration(ref_path)
        ref_text = w["text"]

        reference = {"path": ref_path, "text": ref_text, "duration": actual}
        t0 = time.time()
        try:
            plan = dubbing.build_dubbed_track(
                segments, translated, reference, target, dur,
                os.path.join(args.out, f"{stem}_dub{i}.wav"),
                source_lang=source_lang, source_audio=stt_audio)
        except Exception as e:                               # noqa: BLE001
            print(f"  {i:<3}{w['start']:>7.2f}s +{w['duration']:<6.2f}  FAILED: {e}")
            continue

        dub_emb = scorer.embed(os.path.join(args.out, f"{stem}_dub{i}.wav"))
        # The comparison that matters: does the dub sound like the speaker.
        sounds_like = scorer.cosine(speaker, dub_emb)
        # How good the window itself is as a prompt, for context.
        ref_quality = scorer.cosine(speaker, scorer.embed(ref_path))

        mark = " <- auto" if auto_start is not None and abs(w["start"] - auto_start) < 0.05 else ""
        rows.append((i, w, sounds_like, ref_quality, actual, mark))
        print(f"  {i:<3}{w['start']:>7.2f}s +{w['duration']:<6.2f}"
              f"{w['score']:>9.3f}{sounds_like:>13.4f}{ref_quality:>13.4f}"
              f"  {ref_text[:30]}{mark}  ({time.time()-t0:.0f}s)")

    if not rows:
        return 1

    print("\n  " + "=" * 68)
    best = max(rows, key=lambda r: r[2])
    auto_row = next((r for r in rows if r[5]), None)
    print(f"  Best window     : #{best[0]} at {best[1]['start']:.2f}s "
          f"-> {best[2]:.4f}")
    if auto_row:
        delta = best[2] - auto_row[2]
        print(f"  Auto-selected   : #{auto_row[0]} at {auto_row[1]['start']:.2f}s "
              f"-> {auto_row[2]:.4f}")
        if best[0] == auto_row[0]:
            print("  VERDICT: the heuristic picked the best window.")
        else:
            print(f"  VERDICT: the heuristic left {delta:.4f} on the table "
                  f"({delta / max(auto_row[2], 1e-6):.1%} relative).")
    spread = max(r[2] for r in rows) - min(r[2] for r in rows)
    print(f"  Spread across {len(rows)} windows: {spread:.4f}")
    print(f"  Heuristic score vs outcome correlate: "
          f"{'yes' if best[1]['score'] == max(r[1]['score'] for r in rows) else 'NO'}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
