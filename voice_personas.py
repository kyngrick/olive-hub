#!/usr/bin/env python3
"""Voice persona registry used by the hub and the TTS language router.

A persona is a named voice style backed by one of:
  piper     - an installed Piper voice (works now)
  kokoro    - a Kokoro voice/blend on tcp://127.0.0.1:10204 (optional)
  omnivoice - a cloned voice on tcp://127.0.0.1:10203 (needs a big model)

`ready` means the backend is actually installed and can render the voice.
The router falls back to the language-default voice when a persona is not ready.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.environ.get("OLIVE_VOICE_REGISTRY") or os.path.join(BASE_DIR, "voice_personas.json")
REFS_DIR = os.path.join(BASE_DIR, "voice_refs")


def _empty() -> dict:
    return {"version": 1, "active": "", "personas": {}}


def load() -> dict:
    try:
        with open(REGISTRY, encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("personas"), dict):
            data.setdefault("active", "")
            return data
    except Exception:
        pass
    return _empty()


def save(data: dict) -> None:
    directory = os.path.dirname(REGISTRY) or "."
    fd, tmp = tempfile.mkstemp(prefix=".olive-voices-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, REGISTRY)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def add(name: str, ref_wav: str = "", ref_text: str = "",
        provider: str = "omnivoice", voice: str = "", tone: str = "",
        ready: bool = False) -> dict:
    data = load()
    key = " ".join(str(name or "").strip().lower().split())
    if not key:
        raise ValueError("empty persona name")
    data["personas"][key] = {
        "name": str(name).strip(),
        "provider": provider,
        "voice": voice or str(name).strip(),
        "ref_wav": ref_wav,
        "ref_text": ref_text,
        "tone": tone,
        "ready": bool(ready),
        "created_at": time.time(),
    }
    save(data)
    return data["personas"][key]


def set_active(name: str = "") -> str:
    data = load()
    key = " ".join(str(name or "").strip().lower().split())
    data["active"] = key if key in data["personas"] else ""
    save(data)
    return data["active"]


def active() -> tuple[str, dict | None]:
    data = load()
    key = data.get("active") or ""
    return key, data["personas"].get(key)


def all_personas() -> dict:
    return load().get("personas", {})


def remove(name: str) -> bool:
    data = load()
    key = " ".join(str(name or "").strip().lower().split())
    if key not in data["personas"]:
        return False
    del data["personas"][key]
    if data.get("active") == key:
        data["active"] = ""
    save(data)
    return True
