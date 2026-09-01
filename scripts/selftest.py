#!/usr/bin/env python3
"""
End-to-end self test.

Exercises the whole pipeline against a running engine: a real dub, the phrase
editor, undo, exports, a voice-over, and cancellation. Every check is against
observable behaviour, not internal state, so this passes only if the thing a
user would do actually works.

    python scripts/selftest.py path/to/clip.mp4
    python scripts/selftest.py path/to/clip.mp4 --quick   # skip the renders

Requires the engine to be running:
    cd Backend_pipeline && ../venv/bin/python -m uvicorn app:app --port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv("MULTIVA_URL", "http://127.0.0.1:8000")
passed, failed, notes = 0, 0, []


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        notes.append(name)
        print(f"  FAIL  {name}  {detail}")
    return ok


def call(method, path, body=None, files=None, raw=False):
    """Returns (status, parsed_or_bytes). Never raises on an HTTP error."""
    url = f"{BASE}{path}"
    data, headers = None, {}
    if files:
        boundary = "----multiva" + str(int(time.time() * 1000))
        parts = []
        for key, (fname, content) in files.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(
                f'Content-Disposition: form-data; name="{key}"; filename="{fname}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n".encode())
            parts.append(content)
            parts.append(b"\r\n")
        for key, value in (body or {}).items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            parts.append(str(value).encode())
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            payload = r.read()
            if raw:
                return r.status, payload
            try:
                return r.status, json.loads(payload)
            except json.JSONDecodeError:
                return r.status, payload
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except json.JSONDecodeError:
            return e.code, payload
    except Exception as e:                                   # noqa: BLE001
        return 0, {"detail": str(e)}


def tone_wav(seconds=6, rate=22050, freq=220.0):
    """A quiet sine tone, so the music-bed path has something real to mix."""
    import math
    import struct
    import wave
    from io import BytesIO

    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(int(rate * seconds))))
    return buf.getvalue()


def wait_for(job, timeout=900):
    """Block until a job leaves the processing state."""
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        code, d = call("GET", f"/jobs/{job}/status")
        if code != 200:
            return "gone", d
        if d["step"] != last:
            last = d["step"]
            print(f"        {d['status']:10} {last}")
        if d["status"] in ("done", "failed", "cancelled"):
            return d["status"], d
        time.sleep(4)
    return "timeout", {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", nargs="?", help="a short talking-head video")
    ap.add_argument("--quick", action="store_true", help="skip anything that renders")
    ap.add_argument("--lang", default="hi")
    args = ap.parse_args()

    print(f"\n  Multiva self test  ->  {BASE}\n")

    # ---- engine ----------------------------------------------------------
    print("  Engine")
    code, health = call("GET", "/api/health")
    check("health responds", code == 200, str(health)[:60])
    check("no cloud storage reported", isinstance(health, dict)
          and "db" not in health and "r2" not in health, str(health)[:80])
    code, boot = call("GET", "/api/boot")
    check("models loaded", code == 200 and boot.get("ready"), str(boot)[:60])
    if isinstance(boot, dict) and boot.get("notes"):
        print(f"        note: {boot['notes']}")

    print("\n  Settings")
    code, eng = call("GET", "/api/settings/engines")
    check("engine catalogue", code == 200 and "stages" in eng)
    code, mind = call("GET", "/api/settings/llm")
    check("script model settings", code == 200 and "provider" in mind)
    check("no API key ever returned", "keys" not in json.dumps(mind)
          and "sk-" not in json.dumps(mind))
    code, _ = call("POST", "/api/settings/llm", {"provider": "not-a-provider"})
    check("unknown provider rejected", code == 400, f"got {code}")

    if args.quick or not args.clip:
        if not args.clip:
            print("\n  No clip given; skipping everything that renders.")
        return report()

    if not os.path.isfile(args.clip):
        print(f"\n  No such file: {args.clip}")
        return 1
    clip = open(args.clip, "rb").read()
    name = os.path.basename(args.clip)

    # ---- a real dub ------------------------------------------------------
    print("\n  Dub")
    code, sub = call("POST", f"/process_video/?original_language=auto"
                             f"&target_language={args.lang}&user_id=selftest",
                     files={"file": (name, clip)})
    if not check("accepted", code == 200 and "job_id" in sub, str(sub)[:80]):
        return report()
    job = sub["job_id"]
    status, d = wait_for(job)
    if not check("render completed", status == "done", d.get("error", "")):
        return report()

    check("audio and video agree", bool(d.get("sync", {}).get("ok")),
          str(d.get("sync")))
    check("voice match scored", d.get("voice_match") is not None)
    if d.get("voice_match"):
        print(f"        voice match {d['voice_match']['score']:.3f}")
    check("render served locally", str(d.get("url", "")).startswith("/jobs/"),
          str(d.get("url")))
    code, _ = call("GET", f"/jobs/{job}/video", raw=True)
    check("render downloads", code == 200, f"got {code}")

    # ---- the phrase timeline --------------------------------------------
    print("\n  Phrase timeline")
    code, tl = call("GET", f"/jobs/{job}/segments")
    check("timeline listed", code == 200 and tl.get("segments"))
    phrases = tl.get("segments", [])
    thin = [p for p in phrases if len(p["text"].split()) < 2]
    check("no one-word phrases", not thin, f"{[p['text'] for p in thin]}")
    print(f"        {len(phrases)} phrases")

    original = phrases[0]["text"]
    code, before = call("GET", f"/jobs/{job}/segments/0/audio", raw=True)
    check("phrase audio plays", code == 200)

    code, rev = call("POST", f"/jobs/{job}/segments/0", {"text": "यह एक परीक्षण है।"})
    check("phrase rewritten", code == 200 and rev.get("text") == "यह एक परीक्षण है।",
          str(rev)[:70])
    check("picture marked stale", bool(rev.get("video_stale")))
    check("undo is offered", rev.get("can_undo", 0) >= 1)

    code, un = call("POST", f"/jobs/{job}/undo")
    check("undo restores the text", code == 200 and un.get("text") == original,
          f"got {un.get('text')!r}")
    check("undo restores the audio", bool(un.get("audio_restored")))
    _, after = call("GET", f"/jobs/{job}/segments/0/audio", raw=True)
    check("restored take is the original take", before == after,
          "a re-synthesis would differ")
    code, _ = call("POST", f"/jobs/{job}/undo")
    check("undo stops at the bottom", code == 409, f"got {code}")

    # ---- reference windows ----------------------------------------------
    code, refs = call("GET", f"/jobs/{job}/reference/candidates")
    check("reference windows offered", code == 200 and "candidates" in refs)

    # ---- script intelligence ---------------------------------------------
    # The only stage that calls a language model, and the only one that can be
    # pointed off this machine. Skipped rather than failed when no model is
    # configured: a fresh clone with no Ollama is a supported way to run.
    print("\n  Script intelligence")
    code, mind = call("GET", "/api/settings/llm")
    if code != 200 or not mind.get("enabled"):
        print("        No script model configured; skipping the fit checks.")
    else:
        where = "on this machine" if mind.get("local") else "hosted"
        print(f"        {mind['provider']} / {mind['model']} ({where})")

        code, probe = call("POST", "/api/settings/llm/test", {})
        check("script model answers", code == 200 and probe.get("ok"),
              str(probe.get("error") or probe)[:70])

        # The longest line is the one most likely to overrun its slot, which is
        # the case the fitter exists for.
        longest = max(phrases, key=lambda p: len(p["text"]))
        idx = longest["index"]
        code, fit = call("POST", f"/jobs/{job}/segments/{idx}/fit", {})
        ok = code == 200 and {"slot_seconds", "spoken_seconds", "attempts"} <= set(fit)
        check("fit measured against the slot", ok, f"{code} {str(fit)[:70]}")

        if ok:
            print(f"        slot {fit['slot_seconds']}s, "
                  f"spoken {fit['spoken_seconds']}s, "
                  f"{len(fit['attempts'])} attempt(s)")
            if fit.get("changed"):
                check("rewrite is shorter than the original",
                      fit["spoken_seconds"] < fit["was_seconds"],
                      f"{fit['spoken_seconds']} vs {fit['was_seconds']}")
                check("rewrite changed the words",
                      fit["text"] != longest["text"])
                # The plan and the audio must agree afterwards, which is the
                # thing that silently broke when a synth failed mid-edit.
                _, tl2 = call("GET", f"/jobs/{job}/segments")
                now = next(p["text"] for p in tl2["segments"] if p["index"] == idx)
                check("timeline shows the fitted line", now == fit["text"],
                      f"{now!r} != {fit['text']!r}")
                check("picture marked stale by the fit",
                      bool(fit.get("video_stale")))
                call("POST", f"/jobs/{job}/undo")
            else:
                check("unchanged fit explains itself", bool(fit.get("detail")),
                      str(fit)[:70])
                print(f"        {fit.get('detail')}")

        code, _ = call("POST", f"/jobs/{job}/segments/9999/fit", {})
        check("fit rejects an unknown phrase", code == 404, f"got {code}")

    # ---- re-rolling a delivery -------------------------------------------
    # Same words, different draw. Synthesis is seeded for reproducibility, so
    # this is the only way to get a second take of a line you are happy with.
    print("\n  Delivery")
    _, take1 = call("GET", f"/jobs/{job}/segments/0/audio", raw=True)
    code, roll = call("POST", f"/jobs/{job}/segments/0", {"seed": 4242})
    check("delivery re-rolled", code == 200 and roll.get("seed") == 4242,
          str(roll)[:70])
    check("re-roll left the words alone", roll.get("text") == original,
          f"got {roll.get('text')!r}")
    _, take2 = call("GET", f"/jobs/{job}/segments/0/audio", raw=True)
    check("a new seed gives a different take", take1 != take2,
          "identical audio means the seed was ignored")
    call("POST", f"/jobs/{job}/undo")

    # ---- choosing the reference window ------------------------------------
    # Re-clones from a different few seconds of the speaker and re-speaks
    # every phrase, because the cached units were all said in the old voice.
    print("\n  Reference window")
    code, _ = call("POST", f"/jobs/{job}/reference", {"start": 0.0, "duration": 0.5})
    check("a too-short reference is rejected", code == 400, f"got {code}")
    code, _ = call("POST", f"/jobs/{job}/reference", {"start": "x", "duration": 3})
    check("a non-numeric window is rejected", code == 400, f"got {code}")

    windows = refs.get("candidates") or []
    if windows:
        w = windows[-1]
        code, _ = call("POST", f"/jobs/{job}/reference",
                       {"start": w["start"], "duration": w["duration"]})
        check("reference change accepted", code == 200, f"got {code}")
        state, d = wait_for(job)
        check("re-cloned from the new window", state == "done",
              f"{state} {d.get('error')}")
        _, tl3 = call("GET", f"/jobs/{job}/segments")
        check("timeline survived the re-clone",
              len(tl3.get("segments", [])) == len(phrases),
              f"{len(tl3.get('segments', []))} vs {len(phrases)}")
    else:
        print("        No candidate windows offered; skipping the re-clone.")

    # ---- re-rendering the picture ------------------------------------------
    # Edits rewrite the audio immediately; the video is only redone on
    # request. Until it is, the render on disk is a previous take.
    print("\n  Re-render")
    _, before_state = call("GET", f"/jobs/{job}/status")
    check("picture reported stale after edits",
          bool(before_state.get("video_stale")), str(before_state)[:70])
    code, _ = call("POST", f"/jobs/{job}/rerender")
    check("re-render accepted", code == 200, f"got {code}")
    state, d = wait_for(job)
    check("re-render finished", state == "done", f"{state} {d.get('error')}")
    check("picture no longer stale", not d.get("video_stale"),
          "an edit after this would be invisible in the file")
    code, _ = call("GET", f"/jobs/{job}/video", raw=True)
    check("re-rendered file downloads", code == 200, f"got {code}")

    # ---- exports ---------------------------------------------------------
    print("\n  Exports")
    for kind in ("transcript.txt", "translation.txt", "source.srt",
                 "dub.srt", "dub.vtt", "source.vtt"):
        code, body = call("GET", f"/jobs/{job}/export/{kind}", raw=True)
        check(f"{kind}", code == 200 and len(body) > 10, f"{code}, {len(body)}b")
    code, _ = call("GET", f"/jobs/{job}/export/nope.xyz", raw=True)
    check("unknown export rejected", code == 404, f"got {code}")

    # ---- trim and the music bed -------------------------------------------
    # Both are set at upload time, so they need a job of their own. Trimming
    # also keeps this one short, which is most of why it is affordable.
    print("\n  Trim and music bed")
    music = tone_wav(seconds=6)
    code, started = call(
        "POST",
        f"/process_video/?original_language=en&target_language=hi"
        f"&user_id=selftest&trim_start=2.0&trim_end=9.0&music_gain=-20",
        files={"file": (name, clip), "music": ("bed.wav", music)})
    trimmed = started.get("job_id")
    check("trimmed job with a music bed accepted", code == 200 and trimmed,
          f"{code} {str(started)[:70]}")

    if trimmed:
        state, d = wait_for(trimmed)
        check("trimmed render completed", state == "done",
              f"{state} {d.get('error')}")
        # 2.0 to 9.0 is seven seconds, whatever the source was.
        got = d.get("video_duration") or 0
        check("trim applied to the render", 6.0 <= got <= 8.0,
              f"video_duration {got}, expected about 7")
        code, body = call("GET", f"/jobs/{trimmed}/video", raw=True)
        check("trimmed render downloads", code == 200 and len(body) > 1000,
              f"{code}, {len(body)}b")
        call("DELETE", f"/videos/{trimmed}")

    # A trim that selects nothing is a mistake worth catching at the door.
    code, _ = call("POST",
                   f"/process_video/?original_language=en&target_language=hi"
                   f"&trim_start=9.0&trim_end=2.0",
                   files={"file": (name, clip)})
    check("an inverted trim is rejected", code == 400, f"got {code}")

    # ---- projects --------------------------------------------------------
    print("\n  Projects")
    code, rows = call("GET", "/videos/?user_id=selftest")
    check("project listed", code == 200 and any(r["job_id"] == job for r in rows))
    check("listed as openable", any(r["job_id"] == job and r["openable"] for r in rows))

    # ---- voice-over ------------------------------------------------------
    print("\n  Voice-over")
    code, vo = call("POST", f"/voiceover/?language={args.lang}&user_id=selftest",
                    body={"script": "यह आवाज़ का परीक्षण है।"},
                    files={"file": (name, clip)})
    if check("accepted", code == 200 and "job_id" in vo, str(vo)[:70]):
        status, vd = wait_for(vo["job_id"])
        check("voice-over rendered", status == "done", vd.get("error", ""))
        check("reported as a voice-over", vd.get("kind") == "voiceover")
        code, _ = call("GET", f"/jobs/{vo['job_id']}/audio/dub", raw=True)
        check("voice-over audio plays", code == 200)

    # ---- cancellation ----------------------------------------------------
    print("\n  Cancellation")
    code, c = call("POST", f"/process_video/?original_language=auto"
                           f"&target_language={args.lang}&user_id=selftest",
                   files={"file": (name, clip)})
    if check("second job accepted", code == 200):
        cjob = c["job_id"]
        for _ in range(60):
            _, st = call("GET", f"/jobs/{cjob}/status")
            if st.get("step", "").startswith(("transcribing", "synthesizing")):
                break
            time.sleep(2)
        code, res = call("POST", f"/jobs/{cjob}/cancel")
        check("cancel accepted", code == 200 and res.get("cancelled"))
        status, _ = wait_for(cjob, timeout=180)
        check("job stopped", status == "cancelled", f"ended as {status}")
        call("DELETE", f"/videos/{cjob}")

    # ---- cleanup ---------------------------------------------------------
    print("\n  Cleanup")
    code, _ = call("DELETE", f"/videos/{job}")
    check("project deleted", code == 200, f"got {code}")
    code, _ = call("GET", f"/jobs/{job}/status")
    check("deleted project is gone", code == 404, f"got {code}")
    if "job_id" in vo:
        call("DELETE", f"/videos/{vo['job_id']}")

    return report()


def report() -> int:
    print(f"\n  {'=' * 52}")
    print(f"  {passed} passed, {failed} failed")
    if notes:
        for n in notes:
            print(f"    - {n}")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
