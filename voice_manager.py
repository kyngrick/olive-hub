#!/usr/bin/env python3
"""Browse, install and switch Piper voices for the Olive Hub TTS stack.

Used by the dashboard. All actions are local; voices are downloaded from the
official rhasspy/piper-voices repository with Piper's own downloader.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PIPER_ROOT = Path(r"C:\Users\Geggais and Rick\Documents\Codex\piper-wyoming")
PIPER_PY = PIPER_ROOT / ".venv" / "Scripts" / "python.exe"
PIPER_DATA = PIPER_ROOT / "data"
TTS_SCRIPT = ROOT / "start-tts-detached.ps1"
CATALOG_CACHE = ROOT / "piper_voices_cache.json"
VOICES_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
              "voices.json?download=true")


def _catalog() -> dict[str, Any]:
    try:
        if CATALOG_CACHE.exists() and time.time() - CATALOG_CACHE.stat().st_mtime < 86400:
            return json.loads(CATALOG_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    with urllib.request.urlopen(VOICES_URL, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    CATALOG_CACHE.write_text(json.dumps(data), encoding="utf-8")
    return data


def list_voices(lang: str = "en") -> list[dict[str, Any]]:
    """Return voices for a language family, e.g. 'en' or 'it'."""
    try:
        catalog = _catalog()
    except Exception as exc:
        return [{"error": f"cannot load voice catalog: {exc}"}]
    prefix = lang + "_"
    result = []
    for key, item in sorted(catalog.items()):
        if not key.startswith(prefix):
            continue
        lang_info = item.get("language") or {}
        # size of the .onnx file
        size = 0
        for name, info in (item.get("files") or {}).items():
            if name.endswith(".onnx"):
                size = int(info.get("size_bytes") or 0)
                break
        quality = str(item.get("quality") or "")
        result.append({
            "key": key,
            "name": str(item.get("name") or key),
            "quality": quality,
            "language": lang_info.get("name_english") or lang.upper(),
            "speakers": int(item.get("num_speakers") or 1),
            "size_mb": round(size / 1024 / 1024, 1),
            "installed": (PIPER_DATA / f"{key}.onnx").exists()
                         and (PIPER_DATA / f"{key}.onnx.json").exists(),
            # smaller models are measurably faster on this CPU
            "speed": "fast" if quality in ("low", "x_low") else
                     ("medium" if quality == "medium" else "slow"),
        })
    return result


def current_voices() -> dict[str, str]:
    text = TTS_SCRIPT.read_text(encoding="utf-8-sig", errors="ignore")
    out = {}
    for var, lang in (("enVoice", "en"), ("itVoice", "it")):
        m = re.search(rf"\${var}\s*=\s*['\"]([^'\"]+)['\"]", text)
        out[lang] = m.group(1) if m else ""
    return out


def _set_script_voice(lang: str, key: str) -> None:
    var = "enVoice" if lang == "en" else "itVoice"
    text = TTS_SCRIPT.read_text(encoding="utf-8-sig", errors="ignore")
    new, count = re.subn(rf"(\${var}\s*=\s*['\"])[^'\"]+(['\"])",
                         lambda m: m.group(1) + key + m.group(2), text, count=1)
    if count != 1:
        raise RuntimeError(f"could not find ${var} in {TTS_SCRIPT.name}")
    if TTS_SCRIPT.exists():
        backup = TTS_SCRIPT.with_name(TTS_SCRIPT.name + ".bak")
        backup.write_text(text, encoding="utf-8")
    TTS_SCRIPT.write_text(new, encoding="utf-8")


def install_voice(key: str) -> tuple[bool, str]:
    """Download a voice with Piper's official downloader."""
    if not re.fullmatch(r"[a-z]{2}_[A-Z]{2}-[A-Za-z0-9_\-]+-(low|medium|high|x_low)", key):
        return False, "invalid voice key"
    if not PIPER_PY.exists():
        return False, f"Piper venv not found at {PIPER_PY}"
    if (PIPER_DATA / f"{key}.onnx").exists() and (PIPER_DATA / f"{key}.onnx.json").exists():
        return True, "already installed"
    cmd = [str(PIPER_PY), "-m", "piper.download_voices", key,
           "--download-dir", str(PIPER_DATA)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "download timed out"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "download failed")[-500:]
    if not (PIPER_DATA / f"{key}.onnx").exists():
        return False, "downloader reported success but model file is missing"
    return True, "installed"


def _port_pids(ports: tuple[int, ...]) -> list[int]:
    try:
        out = subprocess.run(["netstat.exe", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    pids = set()
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        for port in ports:
            if local.endswith(f":{port}"):
                pids.add(parts[-1])
    return sorted(pids)


def restart_tts() -> tuple[bool, str]:
    """Recycle the TTS router + Piper backends so a voice change takes effect."""
    pids = _port_pids((10200, 10201, 10202))
    for pid in pids:
        subprocess.run(["taskkill.exe", "/F", "/PID", pid],
                       capture_output=True, text=True, timeout=20)
    time.sleep(1.5)
    script = ROOT / "start-tts-detached.ps1"
    # Redirect to a file rather than capturing pipes: the starter launches
    # detached children that inherit stdout, so communicate() can hang forever
    # on Windows even with a timeout.
    log = ROOT / "voice-switch.log"
    try:
        with log.open("w", encoding="utf-8") as handle:
            proc = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                   "-File", str(script)],
                                  stdout=handle, stderr=subprocess.STDOUT, timeout=180)
    except Exception as exc:
        return False, str(exc)
    text = log.read_text(encoding="utf-8", errors="ignore")[-500:] if log.exists() else ""
    if proc.returncode != 0:
        return False, text or "starter failed"
    return True, f"restarted (killed PIDs: {', '.join(pids) or 'none'})"


def set_voice(lang: str, key: str, auto_install: bool = True) -> dict[str, Any]:
    """Install (if needed), select, and apply a new Piper voice."""
    if lang not in ("en", "it"):
        return {"ok": False, "error": "lang must be 'en' or 'it'"}
    catalog = _catalog()
    item = catalog.get(key)
    if not item:
        return {"ok": False, "error": f"unknown voice {key!r}"}
    lang_code = str((item.get("language") or {}).get("code") or "")
    if not lang_code.startswith(lang + "_"):
        return {"ok": False, "error": f"voice {key} is not a {lang.upper()} voice"}

    installed = (PIPER_DATA / f"{key}.onnx").exists() and (PIPER_DATA / f"{key}.onnx.json").exists()
    if not installed:
        if not auto_install:
            return {"ok": False, "error": "voice not installed", "key": key,
                    "size_mb": round(int(list((item.get('files') or {}).values())[0].get('size_bytes', 0)) / 1048576, 1)}
        ok, msg = install_voice(key)
        if not ok:
            return {"ok": False, "error": f"download failed: {msg}", "key": key}

    _set_script_voice(lang, key)
    ok, msg = restart_tts()
    return {"ok": ok, "lang": lang, "key": key,
            "message": msg if not ok else f"Voice changed to {key}; {msg}"}
