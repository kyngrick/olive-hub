#!/usr/bin/env python3
"""Controlled wake-word A/B measurement with explicit approval gates.

This script NEVER changes device or hub settings. It only reads state and
logs. Each stage requires a separate command-line approval flag:

  baseline --approve-baseline   capture pre-upgrade baseline
  post     --approve-post       capture post-upgrade measurements
  false    --approve-false N    monitor N minutes for music-only false wakes
  compare                       compare saved baseline vs post (no approval needed)

The physical part (speaking at given distances/volumes, setting music volume)
must be done by a human. The script records what it can observe and prompts
you to confirm each condition before sampling.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
from olive_hub import Settings, LibreEcho  # noqa: E402

RESULTS = ROOT / "wake-ab-results.json"
LOG = "/var/log/libreecho-waked.log"


def load_results() -> dict:
    try:
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_results(data: dict) -> None:
    RESULTS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved {RESULTS}")


def confirm(message: str) -> None:
    """Prompt, but do not block in non-interactive automation (approval flag
    has already been supplied on the command line)."""
    try:
        input(message)
    except EOFError:
        print("(non-interactive: continuing with explicit approval flag)")


async def device_state(dev: LibreEcho) -> dict:
    out = {"at": datetime.now().isoformat(timespec="seconds")}
    for path, key in (("/device", "os_version"), ("/wake-word", "wake"),
                      ("/audio", "audio"), ("/microphones/beams", "beams"),
                      ("/status", "status")):
        try:
            body = await dev.call("GET", path, timeout=10)
            data = body.get("data") or {}
            if path == "/device":
                out["os_version"] = data.get("os_version")
            elif path == "/wake-word":
                out["wake"] = {k: data.get(k) for k in
                               ("sensitivity", "wake_word", "detected_count",
                                "capture_active", "inference_active", "model_status")}
                out["wake"]["threshold"] = round(
                    0.85 - 0.0044 * float(data.get("sensitivity") or 0), 3)
            elif path == "/audio":
                out["audio"] = {k: data.get(k) for k in
                                ("volume", "microphone_gain", "microphone_muted")}
            elif path == "/microphones/beams":
                out["beams"] = {k: data.get(k) for k in
                                ("running", "selected_deg", "doa_deg",
                                 "peak_db", "contrast_db", "switches")}
            else:
                out["status"] = {k: data.get(k) for k in
                                 ("features_state", "cpu_percent", "uptime_seconds")}
        except Exception as exc:
            out[key if key != "os_version" else "os_version_error"] = str(exc)[:120]
    return out


def remote_log_stats() -> dict:
    """Read wake score stats from the speaker (read-only SSH)."""
    try:
        import paramiko
    except ImportError:
        return {"error": "paramiko not installed"}
    host = "192.168.1.228"
    user = "olive"
    password = os.getenv("SPEAKER_PASSWORD", "olive1234")
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, username=user, password=password, timeout=12,
                       allow_agent=False, look_for_keys=False)
        _in, out, _err = client.exec_command(f"tail -400 {LOG}", timeout=20)
        text = out.read().decode("utf-8", "replace")
        client.close()
    except Exception as exc:
        return {"error": str(exc)[:160]}
    events = []
    for line in text.splitlines():
        m = re.search(r"score=([0-9.]+) vad_score=([0-9.]+) playback=(true|false) model=(\S+)", line)
        if m and m.group(4) != "synthetic-test":
            events.append((float(m.group(1)), m.group(3)))
    result = {}
    for label, subset in (("all", events),
                          ("quiet", [e for e in events if e[1] == "false"]),
                          ("music", [e for e in events if e[1] == "true"])):
        scores = sorted(e[0] for e in subset)
        if not scores:
            result[label] = {"n": 0}
            continue
        n = len(scores)
        result[label] = {"n": n, "min": round(scores[0], 3),
                         "p10": round(scores[n // 10], 3),
                         "median": round(scores[n // 2], 3),
                         "max": round(scores[-1], 3)}
    return result


async def snapshot(label: str, condition: str) -> dict:
    s = Settings.from_env()
    dev = LibreEcho(s)
    await dev.start()
    try:
        state = await device_state(dev)
    finally:
        await dev.close()
    state["condition"] = condition
    state["wake_log_stats"] = remote_log_stats()
    print(f"\n--- {label} / {condition} ---")
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return state


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["baseline", "post", "false", "compare"])
    ap.add_argument("--approve-baseline", action="store_true")
    ap.add_argument("--approve-post", action="store_true")
    ap.add_argument("--approve-false", type=int, default=0, metavar="MINUTES")
    ap.add_argument("--condition", default="quiet",
                    choices=["quiet", "music-low", "music-medium", "music-high", "distance"])
    args = ap.parse_args()

    results = load_results()
    if args.stage == "compare":
        b = results.get("baseline", {})
        p = results.get("post", {})
        if not b or not p:
            print("Need both baseline and post sections. Run both stages first.")
            return
        for cond in ("quiet", "music-medium"):
            bs = b.get(cond, {}).get("wake_log_stats", {}).get("music", {})
            ps = p.get(cond, {}).get("wake_log_stats", {}).get("music", {})
            print(f"{cond}: baseline median={bs.get('median')} post median={ps.get('median')}")
        return

    if args.stage == "baseline":
        if not args.approve_baseline:
            print("REFUSED: pass --approve-baseline to capture the baseline.")
            return
        print("Ensure the speaker is in the desired condition, then confirm.")
        confirm("Press ENTER when ready: ")
        results.setdefault("baseline", {})[args.condition] = await snapshot("baseline", args.condition)
    elif args.stage == "post":
        if not args.approve_post:
            print("REFUSED: pass --approve-post to capture post-upgrade data.")
            return
        print("Confirm the speaker has been upgraded to 0.14.6.")
        confirm("Press ENTER when ready: ")
        results.setdefault("post", {})[args.condition] = await snapshot("post", args.condition)
    else:
        minutes = args.approve_false
        if minutes < 1:
            print("REFUSED: pass --approve-false MINUTES to monitor false wakes.")
            return
        print(f"Set music playing, make sure NO ONE speaks, then confirm {minutes} min monitoring.")
        confirm("Press ENTER when ready: ")
        s = Settings.from_env(); dev = LibreEcho(s); await dev.start()
        try:
            before = (await dev.call("GET", "/wake-word", timeout=10)).get("data", {}).get("detected_count", 0)
        finally:
            await dev.close()
        print(f"detected_count before: {before}; monitoring {minutes} min (Ctrl+C to abort)")
        time.sleep(minutes * 60)
        s = Settings.from_env(); dev = LibreEcho(s); await dev.start()
        try:
            after = (await dev.call("GET", "/wake-word", timeout=10)).get("data", {}).get("detected_count", 0)
        finally:
            await dev.close()
        entry = {"minutes": minutes, "before": before, "after": after,
                 "false_activations": int(after or 0) - int(before or 0),
                 "at": datetime.now().isoformat(timespec="seconds")}
        results.setdefault("false_wake_tests", []).append(entry)
        print(json.dumps(entry, indent=2))

    save_results(results)


if __name__ == "__main__":
    asyncio.run(main())

