#!/usr/bin/env python3
"""
Measure how well a script model shortens a line that overruns its slot.

This is the one stage where the model choice is visible in the finished dub:
when a translated phrase is spoken longer than the gap it has to fit, the
fitter asks the model to say the same thing in fewer characters. A model that
cannot do that leaves the pipeline compressing audio instead, which is what
makes a dub sound robotic.

Run it against each provider and compare:

    python scripts/fit_bench.py                        # whatever is configured
    python scripts/fit_bench.py --provider ollama --model qwen2.5:7b
    python scripts/fit_bench.py --provider groq --model llama-3.3-70b-versatile

Nothing is written to your settings; the provider is overridden for this
process only.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "Backend_pipeline"))

# Lines that actually overran a slot in real dubs, with the English they came
# from. Hindi is the hard case: it is where the local models fall down.
CASES = [
    ("hi", "Hindi",
     "21वीं सदी में भारत का ध्यान तकनीक और नवाचार पर केंद्रित है।",
     "In the 21st century India's focus is on technology and innovation."),
    ("hi", "Hindi",
     "हमें यह सुनिश्चित करना होगा कि हर बच्चे को अच्छी शिक्षा मिले।",
     "We must ensure that every child receives a good education."),
    ("hi", "Hindi",
     "पिछले 5 वर्षों में हमने 30 प्रतिशत से अधिक की वृद्धि दर्ज की है।",
     "Over the last 5 years we have recorded growth of more than 30 percent."),
    ("en", "English",
     "We are absolutely committed to making sure that every single one of "
     "our customers has a genuinely excellent experience.",
     ""),
]

TARGET_RATIO = 0.70


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", help="ollama, groq, anthropic, openai, google, grok, custom")
    ap.add_argument("--model", help="model id for that provider")
    ap.add_argument("--ratio", type=float, default=TARGET_RATIO,
                    help=f"fraction of the original to aim for (default {TARGET_RATIO})")
    args = ap.parse_args()

    # Override for this process only - the user's settings file is not touched.
    if args.provider:
        os.environ["MULTIVA_LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["MULTIVA_LLM_MODEL"] = args.model

    # A key in .env is the documented place to put one, so read it here too.
    import localenv
    localenv.load(os.path.join(ROOT, ".env"))

    import llm

    cfg = llm.load_settings()
    print(f"\n  Fit bench  ->  {cfg['provider']} / {cfg['model']}")
    print(f"  Asking for {int(args.ratio * 100)}% of the original length")
    print("  " + "-" * 68)

    if not llm.configured():
        print("\n  That model is not reachable.")
        if cfg["provider"] == "ollama":
            print(f"  Start Ollama and pull it:  ollama pull {cfg['model']}")
        else:
            print(f"  No API key found for {cfg['provider']}. Put one in .env "
                  f"or set it in the studio's Script model panel.")
        return 1

    kept, shortened, elapsed = 0, 0, 0.0
    for code, language, line, source in CASES:
        began = time.time()
        try:
            out = llm.shorten(line, language, args.ratio, source_text=source)
        except Exception as e:                                   # noqa: BLE001
            print(f"\n  [{language}] FAILED: {type(e).__name__}: {str(e)[:90]}")
            continue
        took = time.time() - began
        elapsed += took

        got = len(out) / len(line)
        lost = llm.missing_numbers(line, out)
        dropped = llm.dropped_words(line, out)
        if got < 0.98:
            shortened += 1
        if not lost:
            kept += 1

        print(f"\n  [{language}]  {len(line)} -> {len(out)} chars "
              f"({got:.0%} of original, wanted {args.ratio:.0%})  {took:.1f}s")
        print(f"    before: {line}")
        print(f"    after : {out}")
        if lost:
            print(f"    !! DROPPED NUMBERS: {lost}  <- would corrupt the dub")
        elif dropped:
            print(f"    words not carried over (advisory): {dropped[:4]}")

    n = len(CASES)
    print("\n  " + "-" * 68)
    print(f"  Shortened at all      : {shortened}/{n}")
    print(f"  Numbers preserved     : {kept}/{n}")
    print(f"  Average time per line : {elapsed / max(1, n):.1f}s")
    print("\n  A model that scores below 3/4 on shortening will leave the")
    print("  pipeline compressing audio instead, which is audible.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
