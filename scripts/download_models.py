#!/usr/bin/env python3
"""
Pre-download every model Multiva needs.

The studio can do this for you on its setup page; this is the same code with a
command line in front of it, for people who would rather not click.

    python scripts/download_models.py            # everything
    python scripts/download_models.py --core     # required models only
    python scripts/download_models.py --check    # report only, download nothing

Works on macOS, Windows and Linux.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "Backend_pipeline"))

import models  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Download Multiva's models")
    ap.add_argument("--core", action="store_true",
                    help="skip optional models (evaluation harness)")
    ap.add_argument("--check", action="store_true",
                    help="report what is present without downloading")
    args = ap.parse_args()

    rows = [r for r in models.inventory() if r["required"] or not args.core]

    print("\n  Multiva models")
    print("  " + "-" * 62)
    print(f"  Cache: {models.cache_root()}")
    print(f"  Free disk: {models.free_space_gb():.1f} GB\n")

    for r in rows:
        mark = "present" if r["present"] else "MISSING"
        tag = "" if r["required"] else "  (optional)"
        print(f"  [{mark:>7}] {r['label']:<20} {r['size']:>7}   {r['id']}{tag}")
        if r["why"]:
            print(f"            {r['why']}")

    todo = [r for r in rows if not r["present"]]
    if args.check:
        print(f"\n  {len(todo)} item(s) still to download.\n")
        return 0
    if not todo:
        print("\n  Everything is already downloaded. You can start the studio.\n")
        return 0

    if models.free_space_gb() < 8:
        print(f"\n  Only {models.free_space_gb():.1f} GB free. "
              f"About 8 GB is needed. Aborting.\n")
        return 1

    print(f"\n  Downloading {len(todo)} item(s). This is resumable; if it is")
    print("  interrupted, run the command again and it continues.\n")

    weights = {w[1]: w for w in models.WEIGHTS}
    for i, r in enumerate(todo, 1):
        print(f"  ({i}/{len(todo)}) {r['label']} - {r['size']}")
        try:
            if r["kind"] == "hub":
                models.fetch_hub(r["id"])
                print("          done\n")
            else:
                label, rel, size, want_bytes, want_sha, mirrors = weights[r["id"]]
                if not models.fetch_weight(label, os.path.join(models.ROOT, rel),
                                           size, want_bytes, want_sha, mirrors):
                    return 1
        except Exception as exc:                             # noqa: BLE001
            print(f"          FAILED: {exc}\n")
            print("  Fix the error above and re-run. "
                  "Already-downloaded models are kept.\n")
            return 1

    print("  All models downloaded. Start the studio next.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
