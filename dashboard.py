#!/usr/bin/env python3
"""Olive Hub dashboard on port 8767 (configurable with DASH_PORT).

Open http://127.0.0.1:8767/ and enter the hub token (HUB_TOKEN from .env).
The token is kept in the browser session and sent as X-Olive-Token on API calls;
it is never embedded in the page or written to logs.

The UI is customisable (theme, visible cards, card order, refresh rate, quick
commands) with preferences saved in the browser. Non-secret .env values can be
edited from the Configuration card; edits are backed up before being written.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web
from dotenv import load_dotenv

import voice_manager
import train_provider

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("HUB_TOKEN", "")
HUB_URL = f"http://127.0.0.1:{os.getenv('HUB_PORT', '8765')}"
HOST = os.getenv("DASH_HOST", "127.0.0.1")
PORT = int(os.getenv("DASH_PORT", "8767"))
ENV_FILE = ROOT / ".env"

SECRET_RE = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD)")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def is_secret(key: str) -> bool:
    # MAX_TOKENS is a response-length setting, not a credential.
    if re.search(r"(?i)MAX_TOKENS?$", key):
        return False
    return bool(SECRET_RE.search(key))

LOG_FILES: dict[str, Path] = {
    "hub": ROOT / "olive_hub.err",
    "whisper": ROOT / "whisper.err",
    "tts-router": ROOT / "tts-router.err",
    "kokoro": ROOT / "kokoro-wyoming.err",
    "whatsapp": ROOT / "whatsapp-bridge" / "bridge.err",
    "restarts": ROOT / "restart-progress.log",
}


def _env_lines() -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    except Exception:
        pass
    return result


def config_summary() -> list[dict[str, str]]:
    rows = []
    for key, value in sorted(_env_lines().items()):
        secret = is_secret(key)
        shown = "<set>" if secret and value else ("<empty>" if secret else value)
        rows.append({"key": key, "value": shown, "secret": secret})
    return rows


def _script_value(filename: str, variable: str, default: str) -> str:
    try:
        text = (ROOT / filename).read_text(encoding="utf-8-sig", errors="ignore")
        m = re.search(rf"\${variable}\s*=\s*['\"]([^'\"]+)['\"]", text)
        return m.group(1) if m else default
    except Exception:
        return default


def voice_summary() -> dict[str, Any]:
    data: dict[str, Any] = {"active": "", "personas": [], "piper_en": "", "piper_it": ""}
    try:
        import voice_personas as vp
        registry = vp.load()
        data["active"] = registry.get("active") or ""
        data["personas"] = [
            {"name": p.get("name"), "provider": p.get("provider"),
             "voice": p.get("voice"), "ready": bool(p.get("ready")),
             "tone": p.get("tone", "")}
            for p in registry.get("personas", {}).values()
        ]
    except Exception as exc:
        data["error"] = str(exc)
    data["piper_en"] = _script_value("start-tts-detached.ps1", "enVoice", "en_GB-alan-medium")
    data["piper_it"] = _script_value("start-tts-detached.ps1", "itVoice", "it_IT-serena-medium")
    try:
        text = (ROOT / "start-whisper-detached.ps1").read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"--model\s+([^\s']+)", text)
        data["whisper_model"] = m.group(1) if m else "tiny"
    except Exception:
        data["whisper_model"] = "tiny"
    data["kokoro_host"] = os.getenv("KOKORO_HOST", "100.113.130.123")
    data["kokoro_port"] = int(os.getenv("KOKORO_PORT", "10222"))
    return data


def tail_file(path: Path, count: int = 150) -> list[str]:
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            return handle.readlines()[-count:]
    except Exception as exc:
        return [f"(cannot read {path.name}: {exc})"]


async def port_open(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=0.4)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def services_summary() -> list[dict[str, Any]]:
    env = _env_lines()
    checks = [
        ("Hub HTTP", "127.0.0.1", int(env.get("HUB_PORT", "8765"))),
        ("Wyoming ASR", "127.0.0.1", int(env.get("ASR_PORT", "10300"))),
        ("Whisper", "127.0.0.1", int(env.get("WHISPER_PORT", "10301"))),
        ("TTS router", "127.0.0.1", int(env.get("TTS_ROUTER_PORT", "10200"))),
        ("Piper EN", "127.0.0.1", 10201),
        ("Piper IT", "127.0.0.1", 10202),
        ("Kokoro (WSL)", os.getenv("KOKORO_HOST", "100.113.130.123"), 10222),
        ("WhatsApp", "127.0.0.1", int(env.get("WHATSAPP_BRIDGE_PORT", "3014"))),
        ("Music Assistant", "127.0.0.1", 8095),
    ]
    results = await asyncio.gather(*(port_open(h, p) for _, h, p in checks))
    return [{"name": n, "host": h, "port": p, "up": ok}
            for (n, h, p), ok in zip(checks, results)]


async def hub_health() -> dict[str, Any]:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(HUB_URL + "/health", headers={"X-Olive-Token": TOKEN},
                                   timeout=aiohttp.ClientTimeout(total=3)) as resp:
                return await resp.json(content_type=None)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def summary() -> dict[str, Any]:
    health = await hub_health()
    intents = health.get("intents") or []
    if not intents:
        try:
            text = (ROOT / "olive_hub.py").read_text(encoding="utf-8", errors="ignore")
            intents = sorted(set(re.findall(r'Intent\(\s*["\']([^"\']+)', text)))
        except Exception:
            intents = []
    return {
        "hub": health,
        "config": config_summary(),
        "voice": voice_summary(),
        "services": await services_summary(),
        "intents": intents,
        "time": asyncio.get_running_loop().time(),
    }


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path.startswith("/api/") and request.path != "/api/local-login":
        if request.headers.get("X-Olive-Token", "") != TOKEN or not TOKEN:
            return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def index(_request: web.Request) -> web.Response:
    return web.Response(text=HTML, content_type="text/html", charset="utf-8")


async def api_summary(_request: web.Request) -> web.Response:
    return web.json_response(await summary())


async def api_logs(request: web.Request) -> web.Response:
    name = request.query.get("name", "hub")
    path = LOG_FILES.get(name)
    if not path:
        return web.json_response({"error": "unknown log"}, status=404)
    return web.json_response({"name": name, "lines": tail_file(path)})


async def api_local_login(_request: web.Request) -> web.Response:
    # Convenience for the localhost-only dashboard: anyone who can reach this
    # server can already read .env. Disabled when bound to a LAN address.
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        return web.json_response({"error": "local login disabled"}, status=403)
    return web.json_response({"token": TOKEN})


async def api_config_set(request: web.Request) -> web.Response:
    """Write one non-secret .env value, keeping a timestamped backup."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    key = str(data.get("key", "")).strip()
    value = str(data.get("value", ""))
    if not ENV_KEY_RE.match(key):
        return web.json_response({"ok": False, "error": "key must look like UPPER_CASE_NAME"}, status=400)
    if is_secret(key):
        return web.json_response({"ok": False, "error": "secret keys cannot be edited here"}, status=403)

    try:
        original = ENV_FILE.read_text(encoding="utf-8-sig", errors="ignore")
        lines = original.splitlines()
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith("#") or "=" not in line:
                continue
            if line.split("=", 1)[0].strip() == key:
                lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"{key}={value}")

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = ENV_FILE.with_name(f".env.bak-{stamp}")
        backup.write_text(original, encoding="utf-8")

        fd, tmp_name = tempfile.mkstemp(prefix=".env-", dir=str(ROOT))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(tmp_name, ENV_FILE)
        return web.json_response({"ok": True, "key": key, "value": value,
                                  "backup": backup.name,
                                  "note": "Restart the hub (and dashboard if DASH_* changed) to apply."})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def hub_call(method: str, path: str, payload: dict | None = None,
                   timeout: float = 8.0) -> Any:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.request(method, HUB_URL + path, json=payload,
                                   headers={"X-Olive-Token": TOKEN},
                                   timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            try:
                return resp.status, await resp.json(content_type=None)
            except Exception:
                return resp.status, {"error": await resp.text()}


# ---- voice selection -------------------------------------------------
async def api_voices(request: web.Request) -> web.Response:
    lang = request.query.get("lang", "en")
    return web.json_response({"lang": lang, "voices": voice_manager.list_voices(lang),
                              "current": voice_manager.current_voices()})


VOICE_JOBS: dict[str, dict[str, Any]] = {}


async def _voice_job_worker(job_id: str, lang: str, key: str) -> None:
    job = VOICE_JOBS[job_id]
    try:
        job["status"] = "downloading"
        job["message"] = "Downloading voice model (can take several minutes)…"
        ok, msg = await asyncio.to_thread(voice_manager.install_voice, key)
        if not ok:
            job.update(status="error", message=f"Download failed: {msg}")
            return
        job["status"] = "restarting"
        job["message"] = "Model installed. Restarting the TTS stack…"
        result = await asyncio.to_thread(voice_manager.set_voice, lang, key, False)
        job.update(status="done" if result.get("ok") else "error",
                   message=result.get("message") or result.get("error") or "unknown result")
    except Exception as exc:
        job.update(status="error", message=str(exc))


async def api_voice_set(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    lang, key = str(data.get("lang", "en")), str(data.get("key", ""))
    try:
        catalog = voice_manager._catalog()
        if key not in catalog:
            return web.json_response({"ok": False, "error": f"unknown voice {key}"}, status=400)
    except Exception as exc:
        return web.json_response({"ok": False, "error": f"cannot read catalog: {exc}"}, status=500)
    job_id = uuid.uuid4().hex[:12]
    VOICE_JOBS[job_id] = {"status": "queued", "message": "Queued…", "lang": lang, "key": key}
    asyncio.create_task(_voice_job_worker(job_id, lang, key))
    return web.json_response({"ok": True, "job": job_id, "message": "Voice change started."})


async def api_voice_job(request: web.Request) -> web.Response:
    job_id = request.query.get("id", "")
    job = VOICE_JOBS.get(job_id)
    if not job:
        return web.json_response({"ok": False, "error": "unknown job"}, status=404)
    return web.json_response({"ok": True, **job})


# ---- intents ---------------------------------------------------------
async def api_intents_get(_request: web.Request) -> web.Response:
    try:
        status, body = await hub_call("GET", "/intents")
        if status == 200:
            return web.json_response({"ok": True, **body})
        return web.json_response({"ok": False, "error": body}, status=status)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)


async def api_intents_set(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    try:
        status, body = await hub_call("POST", "/intents", data, timeout=10)
        return web.json_response(body, status=status)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)


# ---- speaker wake word / microphone ----------------------------------
async def api_speaker_get(_request: web.Request) -> web.Response:
    try:
        import olive_hub as oh
        settings = oh.Settings.from_env()
        device = oh.LibreEcho(settings)
        await device.start()
        try:
            wake = await device.call("GET", "/wake-word")
            audio = await device.call("GET", "/audio")
        finally:
            await device.close()
        return web.json_response({"ok": True,
                                  "sensitivity": (wake.get("data") or {}).get("sensitivity"),
                                  "wake_word": (wake.get("data") or {}).get("wake_word"),
                                  "gain": (audio.get("data") or {}).get("microphone_gain"),
                                  "muted": (audio.get("data") or {}).get("microphone_muted")})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)


async def api_speaker_set(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    try:
        import olive_hub as oh
        settings = oh.Settings.from_env()
        device = oh.LibreEcho(settings)
        await device.start()
        applied = {}
        try:
            if "sensitivity" in data:
                val = max(0, min(100, int(data["sensitivity"])))
                await device.call("PUT", "/wake-word", {"sensitivity": val}, confirm=True)
                applied["sensitivity"] = val
            if "gain" in data:
                val = max(0, min(100, int(data["gain"])))
                await device.call("PUT", "/audio", {"microphone_gain": val}, confirm=True)
                applied["gain"] = val
        finally:
            await device.close()
        return web.json_response({"ok": True, "applied": applied})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)


# ---- latency benchmark -----------------------------------------------
async def api_latency(_request: web.Request) -> web.Response:
    """Measure ASR and TTS first-audio latency with real Wyoming connections."""
    sys_path = os.path.join(str(ROOT), ".venv", "Lib", "site-packages")
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    out: dict[str, Any] = {"ok": True}
    try:
        from wyoming.client import AsyncTcpClient
        from wyoming.audio import AudioChunk, AudioStart, AudioStop
        from wyoming.tts import Synthesize, SynthesizeVoice
        from wyoming.asr import Transcribe, Transcript
        import wave

        # TTS first-audio
        t0 = time.monotonic()
        first = None
        client = AsyncTcpClient("127.0.0.1", int(_env_lines().get("TTS_ROUTER_PORT", "10200")),
                                connect_timeout=5)
        await client.connect()
        await client.write_event(Synthesize(text="Hello, this is a timing test.",
                                            voice=SynthesizeVoice(name="")).event())
        try:
            for _ in range(2000):
                ev = await asyncio.wait_for(client.read_event(), timeout=20)
                if ev is None:
                    break
                if AudioChunk.is_type(ev.type) and first is None:
                    first = time.monotonic() - t0
                if ev.type in ("stop", "audio-stop"):
                    break
        finally:
            await client.disconnect()
        out["tts_first_audio_s"] = round(first or time.monotonic() - t0, 2)

        # ASR on an existing local recording
        wav = ROOT / "voice_refs" / "ricky.wav"
        if wav.exists():
            with wave.open(str(wav), "rb") as w:
                rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
                pcm = w.readframes(w.getnframes())
            t0 = time.monotonic()
            client = AsyncTcpClient("127.0.0.1", int(_env_lines().get("WHISPER_PORT", "10301")),
                                    connect_timeout=5)
            await client.connect()
            await client.write_event(Transcribe().event())
            await client.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
            step = max(1, rate * channels * width // 5)
            for i in range(0, len(pcm), step):
                await client.write_event(AudioChunk(rate=rate, width=width, channels=channels,
                                                    audio=pcm[i:i + step]).event())
            await client.write_event(AudioStop().event())
            try:
                for _ in range(2000):
                    ev = await asyncio.wait_for(client.read_event(), timeout=30)
                    if ev is None or Transcript.is_type(ev.type):
                        break
            finally:
                await client.disconnect()
            out["asr_total_s"] = round(time.monotonic() - t0, 2)
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)
    return web.json_response(out)


# ---- location & trains ------------------------------------------------
async def api_location(_request: web.Request) -> web.Response:
    env = _env_lines()
    try:
        lat = float(env.get("WEATHER_LAT") or 0)
        lon = float(env.get("WEATHER_LON") or 0)
    except ValueError:
        return web.json_response({"ok": False, "error": "invalid WEATHER_LAT/LON"}, status=400)
    import aiohttp
    async with aiohttp.ClientSession() as session:
        place = await train_provider.reverse(session, lat, lon)
    return web.json_response({"ok": True, "lat": lat, "lon": lon,
                              "city": env.get("OLIVE_CITY", ""), "place": place})


async def api_trains(request: web.Request) -> web.Response:
    text = request.query.get("text", "")
    parsed = train_provider.parse_request(text)
    if not parsed["origin"] or not parsed["destination"]:
        return web.json_response({"ok": False,
                                  "error": "say: trains from Rome to Milan tomorrow at 8"})
    import aiohttp
    async with aiohttp.ClientSession() as session:
        a, b = await asyncio.gather(
            train_provider.geocode(session, parsed["origin"]),
            train_provider.geocode(session, parsed["destination"]),
        )
        if not a or not b:
            return web.json_response({"ok": False, "error": "could not geocode the cities"}, status=502)
        trips = await train_provider.plan(session, a, b, parsed["when"], count=5)
    return web.json_response({"ok": True, "origin": parsed["origin"],
                              "destination": parsed["destination"],
                              "when": parsed["when"].isoformat() if parsed["when"] else "",
                              "from": a, "to": b, "trips": trips,
                              "price_url": train_provider.price_url(
                                  parsed["origin"], parsed["destination"], parsed["when"])})


# ---- performance presets ---------------------------------------------
async def api_performance(_request: web.Request) -> web.Response:
    """Apply a latency/quality preset to LLM settings in .env."""
    presets = {
        "fast": {"LLM_MODEL": "qwen/qwen3.8-27b",
                 "LLM_MODEL_FALLBACK": "openai/gpt-oss-20b",
                 "LLM_MAX_TOKENS": "180"},
        "balanced": {"LLM_MODEL": "openai/gpt-oss-20b",
                     "LLM_MODEL_FALLBACK": "qwen/qwen3.8-27b",
                     "LLM_MAX_TOKENS": "500"},
        "quality": {"LLM_MODEL": "openai/gpt-oss-120b",
                    "LLM_MODEL_FALLBACK": "openai/gpt-oss-20b",
                    "LLM_MAX_TOKENS": "900"},
    }
    # Preset name supplied as ?preset=...
    # (kept as a query param so this endpoint stays POST-only for CSRF clarity)
    name = _request.query.get("preset", "")
    values = presets.get(name)
    if not values:
        return web.json_response({"ok": False, "error": "unknown preset",
                                  "available": list(presets)}, status=400)
    results = {}
    for key, value in values.items():
        # reuse the safe writer logic directly
        class _Req:
            async def json(self):
                return {"key": key, "value": value}
        resp = await api_config_set(_Req())  # type: ignore[arg-type]
        data = json.loads(resp.text)
        results[key] = data.get("ok", False)
    if not all(results.values()):
        return web.json_response({"ok": False, "error": "failed to write all keys",
                                  "detail": results}, status=500)
    return web.json_response({"ok": True, "preset": name, "written": results,
                              "note": "Restart the hub to apply LLM changes."})


async def api_restart(_request: web.Request) -> web.Response:
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(HUB_URL + "/restart", headers={"X-Olive-Token": TOKEN},
                                    timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return web.json_response({"ok": resp.status == 200, "status": resp.status})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)


def make_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", index)
    app.router.add_get("/api/summary", api_summary)
    app.router.add_post("/api/local-login", api_local_login)
    app.router.add_get("/api/logs", api_logs)
    app.router.add_post("/api/config", api_config_set)
    app.router.add_get("/api/voices", api_voices)
    app.router.add_post("/api/voice", api_voice_set)
    app.router.add_get("/api/voice/job", api_voice_job)
    app.router.add_get("/api/intents", api_intents_get)
    app.router.add_post("/api/intents", api_intents_set)
    app.router.add_get("/api/speaker", api_speaker_get)
    app.router.add_post("/api/speaker", api_speaker_set)
    app.router.add_post("/api/latency", api_latency)
    app.router.add_post("/api/performance", api_performance)
    app.router.add_get("/api/location", api_location)
    app.router.add_get("/api/trains", api_trains)
    app.router.add_post("/api/restart", api_restart)
    return app


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Olive Hub Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0d1117;--card:#161b22;--fg:#c9d1d9;--accent:#58a6ff;--ok:#3fb950;--bad:#f85149;--dim:#8b949e;--line:#30363d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,Roboto,monospace}
header{padding:16px 24px;background:#010409;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px;flex-wrap:wrap}
h1{margin:0;font-size:20px;color:var(--accent)}.wrap{padding:20px;max-width:1400px;margin:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px}
.card h2{margin:0 0 12px;font-size:15px;color:var(--accent);text-transform:uppercase;letter-spacing:.08em}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left;word-break:break-word}
th{color:var(--dim);font-weight:600}.ok{color:var(--ok)}.bad{color:var(--bad)}.dim{color:var(--dim)}
pre{background:#010409;border:1px solid var(--line);border-radius:6px;padding:10px;max-height:320px;overflow:auto;white-space:pre-wrap;font-size:12px}
button,select,input,textarea{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font:inherit}
button{cursor:pointer}button:hover{border-color:var(--accent)}button.mini{padding:2px 8px;font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;max-height:260px;overflow:auto}.chip{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:2px 9px;font-size:12px}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;z-index:10}
.modal form{background:var(--card);border:1px solid var(--line);padding:24px;border-radius:10px;width:min(560px,92vw);max-height:90vh;overflow:auto}
.modal h2{margin-top:0;color:var(--accent)}#customize form{width:min(680px,94vw)}
#login form{width:min(380px,92vw)}.modal input{width:100%;margin:6px 0}.modal textarea{width:100%;height:110px;margin:6px 0}
.hidden{display:none}.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.rowline{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0}
.rowline input{flex:1;min-width:120px}.err{color:var(--bad);min-height:20px;font-size:13px}
.cfgsave{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.cfgsave input{flex:1;min-width:120px}
.orderlist label{display:flex;justify-content:space-between;align-items:center;padding:3px 0}
.note{font-size:12px;color:var(--dim)}
</style></head><body>
<header><h1>OLIVE HUB · CONTROL / TEST</h1><div class="bar">
<span id="status" class="dim">connecting…</span>
<select id="logname"><option>hub</option><option>whisper</option><option>tts-router</option><option>kokoro</option><option>whatsapp</option><option>restarts</option></select>
<button onclick="loadAll()">Refresh</button><button onclick="restartHub()">Restart hub</button>
<button onclick="openCustomize()">Customize</button><button onclick="lock()">Change token</button>
</div></header>

<div id="login" class="modal"><form onsubmit="login(event)"><h2>Olive Hub token</h2>
<p class="dim">Enter HUB_TOKEN from .env. It stays in this browser session.</p>
<input id="token" type="password" placeholder="X-Olive-Token" autofocus>
<div id="loginerr" class="err"></div>
<div class="bar"><button type="submit">Connect</button><button type="button" onclick="localLogin()">Use local token</button></div></form></div>

<div id="customize" class="modal hidden"><form onsubmit="savePrefs(event)">
<h2>Customize dashboard</h2>
<div class="rowline"><label>Theme
<select id="theme"><option value="midnight">Midnight</option><option value="ocean">Ocean</option><option value="light">Light</option><option value="forest">Forest</option></select></label></div>
<div class="rowline"><label>Accent <input id="accent" type="color" value="#58a6ff"></label></div>
<p class="dim">Visible cards and their order:</p>
<div id="orderlist" class="orderlist"></div>
<div class="rowline"><label>Refresh every <input id="interval" type="number" min="2" max="120" value="5" style="width:80px"> seconds</label></div>
<p class="dim">Quick test commands (shown in the Quick commands card):</p>
<textarea id="commands"></textarea>
<div id="preferr" class="err"></div>
<div class="bar"><button type="submit">Save</button><button type="button" onclick="resetPrefs()">Reset defaults</button><button type="button" onclick="closeCustomize()">Cancel</button></div>
</form></div>

<div class="wrap"><div class="grid" id="grid">
<div class="card" id="card-services"><h2>Hub / services</h2><table id="services"></table></div>
<div class="card" id="card-features"><h2>LLM / features</h2><table id="features"></table></div>
<div class="card" id="card-voice"><h2>Voice</h2><table id="voice"></table></div>
<div class="card" id="card-intents"><h2>Intents (<span id="intentcount">0</span>)</h2>
<input id="ifilter" placeholder="Filter intents…" oninput="renderIntents()" style="width:100%;margin-bottom:8px">
<div id="intents" class="chips"></div></div>
<div class="card" id="card-voicesel"><h2>Voice picker (Piper)</h2>
<div class="bar" style="margin-bottom:8px"><select id="voicelang" onchange="loadVoices()"><option value="en">English</option><option value="it">Italian</option></select>
<span id="voicecurrent" class="dim"></span></div>
<div id="voicelist" style="max-height:260px;overflow:auto"></div></div>
<div class="card" id="card-intentedit"><h2>Intent editor (hot reload)</h2>
<div id="intentlist" style="max-height:200px;overflow:auto;margin-bottom:8px"></div>
<input id="newiname" placeholder="name, e.g. custom.coffee" style="width:100%;margin-bottom:4px">
<input id="newipatterns" placeholder="patterns, comma separated (regex)" style="width:100%;margin-bottom:4px">
<input id="newireply" placeholder="spoken reply" style="width:100%;margin-bottom:4px">
<div class="bar"><button class="mini" onclick="addCustomIntent()">Add intent</button><button class="mini" onclick="loadIntents()">Reload</button></div>
<div id="intenterrmsg" class="err"></div></div>
<div class="card" id="card-speaker"><h2>Wake word / microphone</h2>
<div id="speakerinfo" class="dim">loading…</div>
<div class="rowline"><label>Wake sensitivity <input id="sensval" type="number" min="0" max="100" value="67" style="width:90px"></label></div>
<div class="rowline"><label>Mic gain <input id="gainval" type="number" min="0" max="100" value="65" style="width:90px"></label></div>
<div class="bar"><button class="mini" onclick="applySpeaker()">Apply to speaker</button><button class="mini" onclick="loadSpeaker()">Refresh</button></div>
<div id="speakererr" class="err"></div>
<p class="note">Higher sensitivity = easier to wake from a distance. 0 = strictest (threshold 0.85), 100 = most sensitive (0.41). Mic gain above ~78 may introduce hiss.</p></div>
<div class="card" id="card-perf"><h2>Response time</h2>
<div id="perfinfo" class="dim">Measured: ASR ~2.6 s (6.7 s audio), TTS first audio ~1.2 s, LLM ~0.3–0.8 s depending on model.</div>
<div class="bar" style="margin:8px 0"><button class="mini" onclick="applyPreset('fast')">Fast</button><button class="mini" onclick="applyPreset('balanced')">Balanced</button><button class="mini" onclick="applyPreset('quality')">Quality</button><button class="mini" onclick="runLatency()">Measure now</button></div>
<div id="perfmsg" class="err"></div>
<p class="note">Fast uses <b>qwen/qwen3.8-27b</b> (non-reasoning, multilingual, 0.3–0.8 s; good English and Italian). Quality uses gpt-oss-120b with thinking. Restart the hub after switching.</p></div>
<div class="card" id="card-travel" style="grid-column:1/-1"><h2>Travel / location</h2>
<div id="locationinfo" class="dim">location…</div>
<div class="rowline" style="margin-top:8px"><input id="trainq" placeholder="trains from Rome to Milan tomorrow at 8" style="flex:1">
<button class="mini" onclick="checkTrains()">Check trains</button><button class="mini" onclick="loadLocation()">Location</button></div>
<div id="trainresult"></div></div>
<div class="card" id="card-config" style="grid-column:1/-1"><h2>Configuration (.env, secrets redacted)</h2>
<div class="cfgsave"><input id="cfgkey" placeholder="KEY"><input id="cfgval" placeholder="new value"><button class="mini" onclick="saveConfig()">Save value</button></div>
<div id="cfgerr" class="err"></div>
<input id="cfilter" placeholder="Filter config…" oninput="renderConfig()" style="width:100%;margin-bottom:8px">
<div style="max-height:280px;overflow:auto"><table id="config"></table></div>
<p class="note">Non-secret values only. A timestamped .env backup is made before each change; restart the hub to apply. DASH_* changes need a dashboard restart.</p></div>
<div class="card" id="card-logs" style="grid-column:1/-1"><h2>Logs · <span id="logtitle">hub</span></h2><pre id="logs"></pre></div>
<div class="card" id="card-quick"><h2>Quick test commands</h2><pre id="quicktext"></pre></div>
<div class="card" id="card-libreecho"><h2>LibreEcho upgrade</h2><div id="leinfo" class="dim">checking…</div></div>
</div></div>

<script>
const DEFAULT_COMMANDS = ".\\.venv\\Scripts\\python.exe tools\\kokoro\\test_kokoro.py\n.\\.venv\\Scripts\\python.exe tools\\kokoro\\record_and_clone.py --name Ricky\n\nAlexa clone → sample → \"Who is this?\" → \"Ricky\"\nAlexa speak like KokoroIT / KokoroEN";
const CARD_NAMES = {services:'Hub / services',features:'LLM / features',voice:'Voice',voicesel:'Voice picker',intents:'Intents',intentedit:'Intent editor',speaker:'Wake / mic',perf:'Performance',travel:'Travel / location',config:'Configuration',logs:'Logs',quick:'Quick commands',libreecho:'LibreEcho upgrade'};
const DEFAULT_ORDER = ['services','features','voice','voicesel','intents','intentedit','speaker','perf','travel','config','logs','quick','libreecho'];
const DEFAULTS = {theme:'midnight',accent:'#58a6ff',interval:5,commands:DEFAULT_COMMANDS,
  show:Object.fromEntries(DEFAULT_ORDER.map(k=>[k,true])), order:DEFAULT_ORDER.slice()};
const THEMES = {midnight:{bg:'#0d1117',card:'#161b22',fg:'#c9d1d9'},ocean:{bg:'#071a2b',card:'#0f2942',fg:'#d6e9ff'},light:{bg:'#f6f8fa',card:'#ffffff',fg:'#1f2328'},forest:{bg:'#0b1712',card:'#13251c',fg:'#d8f3e2'}};
let T=sessionStorage.getItem('olive_token')||'', DATA=null, LOGS=[], TIMER=null;
let PREFS=loadPrefs();

function loadPrefs(){
  try{let p=JSON.parse(localStorage.getItem('olive_prefs')||'null');
    if(p&&p.order&&p.show){
      const order=DEFAULT_ORDER.filter(k=>p.order.includes(k)).concat(DEFAULT_ORDER.filter(k=>!p.order.includes(k)));
      return Object.assign({},DEFAULTS,p,{order,show:Object.assign({},DEFAULTS.show,p.show)});
    }
  }catch(e){}
  return JSON.parse(JSON.stringify(DEFAULTS));
}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function rows(el,items){document.getElementById(el).innerHTML=items.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('');}
function badge(v){return v?'<span class="ok">● up</span>':'<span class="bad">● down</span>'}

function applyPrefs(){
  const th=THEMES[PREFS.theme]||THEMES.midnight, root=document.documentElement.style;
  root.setProperty('--bg',th.bg);root.setProperty('--card',th.card);root.setProperty('--fg',th.fg);root.setProperty('--accent',PREFS.accent);
  const grid=document.getElementById('grid');
  PREFS.order.forEach(id=>{const el=document.getElementById('card-'+id);if(el){el.classList.toggle('hidden',!PREFS.show[id]);grid.appendChild(el);}});
  document.getElementById('quicktext').textContent=PREFS.commands;
  document.getElementById('interval').value=PREFS.interval;
  if(TIMER)clearInterval(TIMER);
  TIMER=setInterval(loadAll,Math.max(2,Number(PREFS.interval)||5)*1000);
}
function openCustomize(){
  document.getElementById('theme').value=PREFS.theme;document.getElementById('accent').value=PREFS.accent;
  document.getElementById('interval').value=PREFS.interval;document.getElementById('commands').value=PREFS.commands;
  const list=document.getElementById('orderlist');list.innerHTML='';
  PREFS.order.forEach((id,i)=>{
    const row=document.createElement('div');row.className='rowline';
    row.innerHTML='<label><input type="checkbox" data-show="'+id+'" '+(PREFS.show[id]?'checked':'')+'> '+esc(CARD_NAMES[id])+'</label>'+
      '<span><button class="mini" onclick="moveCard(\''+id+'\',-1)">↑</button> <button class="mini" onclick="moveCard(\''+id+'\',1)">↓</button></span>';
    list.appendChild(row);
  });
  document.getElementById('customize').classList.remove('hidden');
}
function closeCustomize(){document.getElementById('customize').classList.add('hidden');}
function moveCard(id,dir){
  const i=PREFS.order.indexOf(id), j=i+dir;
  if(i<0||j<0||j>=PREFS.order.length)return;
  [PREFS.order[i],PREFS.order[j]]=[PREFS.order[j],PREFS.order[i]];openCustomize();
}
function savePrefs(e){
  if(e)e.preventDefault();
  PREFS.theme=document.getElementById('theme').value;PREFS.accent=document.getElementById('accent').value;
  PREFS.interval=Math.max(2,Number(document.getElementById('interval').value)||5);
  PREFS.commands=document.getElementById('commands').value;
  document.querySelectorAll('[data-show]').forEach(cb=>PREFS.show[cb.dataset.show]=cb.checked);
  localStorage.setItem('olive_prefs',JSON.stringify(PREFS));applyPrefs();closeCustomize();
}
function resetPrefs(){
  PREFS=JSON.parse(JSON.stringify(DEFAULTS));localStorage.removeItem('olive_prefs');applyPrefs();openCustomize();
}

function showLogin(msg){document.getElementById('loginerr').textContent=msg||'';document.getElementById('login').classList.remove('hidden');}
function hideLogin(){document.getElementById('login').classList.add('hidden');}
async function api(p,opt={}){
  opt.headers=Object.assign({},opt.headers,{'X-Olive-Token':T});
  const r=await fetch(p,opt);
  if(r.status===401)throw new Error('unauthorized');
  if(!r.ok)throw new Error('HTTP '+r.status);
  return r.json();
}
async function login(e){
  if(e)e.preventDefault();
  const tok=document.getElementById('token').value.trim();
  if(!tok){showLogin('Enter the token first.');return;}
  T=tok;showLogin('Checking token…');
  try{DATA=await api('/api/summary');sessionStorage.setItem('olive_token',T);hideLogin();render();await loadLogs();loadVoices();loadIntents();loadSpeaker();loadLocation();}
  catch(err){T='';sessionStorage.removeItem('olive_token');showLogin('Token rejected. Check HUB_TOKEN in .env ('+err.message+').');}
}
async function localLogin(){
  try{
    const r=await fetch('/api/local-login',{method:'POST'}),d=await r.json();
    if(d.token){document.getElementById('token').value=d.token;await login(null);}
    else showLogin('Local login unavailable: '+JSON.stringify(d));
  }catch(err){showLogin('Local login failed: '+err.message);}
}
function lock(){sessionStorage.removeItem('olive_token');T='';DATA=null;document.getElementById('token').value='';showLogin('Session locked. Enter the token again.');}

function render(){
  const d=DATA,h=d.hub||{},v=d.voice||{};
  document.getElementById('status').textContent=(h.ok?'● hub online':'● hub offline')+' · '+(d.services||[]).filter(s=>s.up).length+'/'+(d.services||[]).length+' services';
  rows('services',(d.services||[]).map(s=>[s.name,s.host+':'+s.port,badge(s.up)]));
  const f=[['LLM',h.llm?'enabled':'disabled'],['Model',h.llm_model||'-'],['Provider',(d.config.find(x=>x.key==='AI_PROVIDER')||{}).value||'-'],
    ['Music Assistant',h.music_assistant?'configured':'no'],['MA reachable',h.music_assistant_reachable?'yes':'no'],
    ['WhatsApp',(h.whatsapp_bridge||{}).status||'-'],['Calendar',h.calendar?'yes':'no'],['Flights',(h.flights||{}).provider||'-'],
    ['Memory',JSON.stringify(h.memory_counts||{})],['Routines',h.scheduled_routines??0],['Conversation turns',h.conversation_turns??0]];
  rows('features',f);
  const personas=(v.personas||[]).map(p=>[p.name+' '+(p.active?'★':''),p.provider,p.voice,p.ready?'ready':'pending']);
  rows('voice',[['Active',v.active||'-','','']].concat(personas).concat([['Piper EN',v.piper_en,'',''],['Piper IT',v.piper_it,'',''],['Whisper',v.whisper_model,'',''],['Kokoro',v.kokoro_host+':'+v.kokoro_port,'','']]));
  document.getElementById('intentcount').textContent=(d.intents||[]).length;
  renderIntents();renderConfig();renderLibreEcho();
}
function renderIntents(){let q=(document.getElementById('ifilter').value||'').toLowerCase();document.getElementById('intents').innerHTML=(DATA?.intents||[]).filter(x=>x.toLowerCase().includes(q)).map(x=>'<span class="chip">'+esc(x)+'</span>').join('');}
function renderConfig(){
  let q=(document.getElementById('cfilter').value||'').toLowerCase();
  rows('config',(DATA?.config||[]).filter(x=>x.key.toLowerCase().includes(q)||x.value.toLowerCase().includes(q))
    .map(x=>[esc(x.key),esc(x.value),x.secret?'':'<button class="mini" onclick="fillConfig(\''+esc(x.key)+'\',\''+esc(x.value)+'\')">Edit</button>']));
}
function fillConfig(k,v){document.getElementById('cfgkey').value=k;document.getElementById('cfgval').value=v==='&lt;empty&gt;'?'':v;}
async function saveConfig(){
  const key=document.getElementById('cfgkey').value.trim(), value=document.getElementById('cfgval').value;
  const err=document.getElementById('cfgerr');
  if(!key){err.textContent='Enter a KEY.';return;}
  err.textContent='Saving…';
  try{
    const r=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,value})});
    err.textContent=r.ok?'Saved '+key+' (backup: '+r.backup+'). Restart the hub to apply.':('Failed: '+r.error);
    if(r.ok){document.getElementById('cfgval').value='';loadAll();}
  }catch(e){err.textContent='Failed: '+e.message;}
}
// ---- voice picker ----
async function loadVoices(){
  const lang=document.getElementById('voicelang').value;
  try{
    const d=await api('/api/voices?lang='+lang);
    document.getElementById('voicecurrent').textContent='current: '+(d.current?.[lang]||'-');
    document.getElementById('voicelist').innerHTML=(d.voices||[]).map(v=>{
      if(v.error)return '<div class="bad">'+esc(v.error)+'</div>';
      const isCur=v.key===d.current?.[lang];
      return '<div class="rowline"><span style="flex:1">'+esc(v.name)+' <span class="dim">'+esc(v.quality)+' · '+v.size_mb+' MB · '+v.speed+'</span>'+
        (isCur?' <span class="ok">● active</span>':'')+'</span>'+
        (v.installed?'<button class="mini" onclick="useVoice(\''+lang+'\',\''+v.key+'\')">Use</button>'
                    :'<button class="mini" onclick="useVoice(\''+lang+'\',\''+v.key+'\')">Download & use</button>')+'</div>';
    }).join('');
  }catch(e){document.getElementById('voicelist').innerHTML='<div class="bad">'+esc(e.message)+'</div>';}
}
async function useVoice(lang,key){
  if(!confirm('Switch '+lang.toUpperCase()+' voice to '+key+'? This restarts the TTS stack.'))return;
  const box=document.getElementById('voicelist');box.innerHTML='Starting…';
  try{
    const r=await api('/api/voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lang,key,install:true})});
    if(!r.ok){box.innerHTML='<span class="bad">'+esc(r.error)+'</span>';return;}
    pollVoiceJob(r.job,box);
  }catch(e){box.innerHTML='<span class="bad">'+esc(e.message)+'</span>';}
}
async function pollVoiceJob(id,box){
  try{
    const d=await api('/api/voice/job?id='+encodeURIComponent(id));
    box.innerHTML='<span class="'+(d.status==='done'?'ok':(d.status==='error'?'bad':'dim'))+'">'+esc(d.status)+': '+esc(d.message)+'</span>';
    if(d.status==='done'){loadVoices();loadAll();}
    else if(d.status!=='error')setTimeout(()=>pollVoiceJob(id,box),2000);
  }catch(e){box.innerHTML='<span class="bad">'+esc(e.message)+'</span>';}
}
// ---- intent editor ----
let INTENT_DATA=null;
async function loadIntents(){
  try{
    INTENT_DATA=await api('/api/intents');
    const all=[...(INTENT_DATA.customs||[]),...(INTENT_DATA.builtins||[])];
    document.getElementById('intentlist').innerHTML=all.map(i=>
      '<div class="rowline"><label><input type="checkbox" data-intent="'+esc(i.name)+'" '+(i.enabled?'checked':'')+
      (i.custom?' style="accent-color:var(--accent)"':'')+'> '+esc(i.name)+(i.custom?' <span class="ok">custom</span>':'')+'</label>'+
      (i.custom?'<button class="mini" onclick="deleteCustomIntent(\''+esc(i.name)+'\')">Delete</button>':'')+'</div>').join('');
    document.getElementById('intenterrmsg').textContent='';
  }catch(e){document.getElementById('intenterrmsg').textContent=e.message;}
}
async function saveIntents(){
  const disabled=[...document.querySelectorAll('[data-intent]')].filter(c=>!c.checked).map(c=>c.dataset.intent);
  const customs=(INTENT_DATA?.customs||[]).map(c=>({
    name:c.name,enabled:true,patterns:c.patterns||[],reply:c.reply||'',speak:c.speak||'pipeline'}));
  const payload={version:1,disabled,intents:customs};
  try{
    const r=await api('/api/intents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    document.getElementById('intenterrmsg').textContent=r.ok?'Saved and hot-reloaded.':('Failed: '+JSON.stringify(r.errors||r.error));
    if(r.ok)loadIntents();
  }catch(e){document.getElementById('intenterrmsg').textContent='Failed: '+e.message;}
}
async function addCustomIntent(){
  const name=document.getElementById('newiname').value.trim();
  const patterns=document.getElementById('newipatterns').value.split(',').map(s=>s.trim()).filter(Boolean);
  const reply=document.getElementById('newireply').value.trim();
  if(!name||!patterns.length||!reply){document.getElementById('intenterrmsg').textContent='Fill name, patterns and reply.';return;}
  if(!INTENT_DATA)await loadIntents();
  INTENT_DATA.customs.push({name,enabled:true,patterns,reply,speak:'pipeline'});
  document.getElementById('newiname').value=document.getElementById('newipatterns').value=document.getElementById('newireply').value='';
  await saveIntents();
}
async function deleteCustomIntent(name){
  if(!confirm('Delete custom intent '+name+'?'))return;
  if(!INTENT_DATA)await loadIntents();
  INTENT_DATA.customs=INTENT_DATA.customs.filter(c=>c.name!==name);
  await saveIntents();
}
// wire checkbox changes to a save
document.addEventListener('change',e=>{if(e.target?.dataset?.intent)saveIntents();});
// ---- speaker ----
async function loadSpeaker(){
  try{
    const d=await api('/api/speaker');
    if(!d.ok)throw Error(d.error);
    document.getElementById('speakerinfo').textContent='wake='+d.wake_word+' · sensitivity '+d.sensitivity+' · gain '+d.gain+(d.muted?' · MUTED':'');
    document.getElementById('sensval').value=d.sensitivity;document.getElementById('gainval').value=d.gain;
    document.getElementById('speakererr').textContent='';
  }catch(e){document.getElementById('speakererr').textContent=e.message;}
}
async function applySpeaker(){
  try{
    const r=await api('/api/speaker',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sensitivity:Number(document.getElementById('sensval').value),gain:Number(document.getElementById('gainval').value)})});
    document.getElementById('speakererr').textContent=r.ok?'Applied to speaker.':('Failed: '+r.error);
    if(r.ok)loadSpeaker();
  }catch(e){document.getElementById('speakererr').textContent='Failed: '+e.message;}
}
// ---- travel / location ----
async function loadLocation(){
  try{const d=await api('/api/location');
    document.getElementById('locationinfo').textContent=d.ok
      ? ('Home: '+d.city+' · '+d.lat.toFixed(2)+', '+d.lon.toFixed(2)+' · nearest: '+(d.place.name||'unknown'))
      : ('Location error: '+d.error);
  }catch(e){document.getElementById('locationinfo').textContent=e.message;}
}
async function checkTrains(){
  const q=document.getElementById('trainq').value.trim(), box=document.getElementById('trainresult');
  if(!q){box.textContent='Enter a journey.';return;}
  box.textContent='Looking up…';
  try{
    const d=await api('/api/trains?text='+encodeURIComponent(q));
    if(!d.ok){box.innerHTML='<span class="bad">'+esc(d.error)+'</span>';return;}
    const rows=(d.trips||[]).map(t=>'<tr><td>'+esc(t.depart)+'</td><td>'+esc(t.arrive)+'</td><td>'+esc(t.duration)+
      '</td><td>'+t.transfers+' change'+(t.transfers===1?'':'s')+'</td><td>'+esc(t.trains)+'</td></tr>').join('');
    box.innerHTML='<p class="dim">'+esc(d.origin)+' → '+esc(d.destination)+(d.when?' · '+esc(d.when.slice(0,16).replace('T',' ')):'')+
      '</p><table><tr><th>Depart</th><th>Arrive</th><th>Duration</th><th>Changes</th><th>Train</th></tr>'+rows+'</table>'+
      '<p class="note">Ticket prices are not published in the free timetable. '+
      '<a href="'+esc(d.price_url)+'" target="_blank" rel="noopener">Check prices</a> · '+
      'Timetable data: <a href="https://transitous.org/sources/" target="_blank" rel="noopener">Transitous sources</a></p>';
  }catch(e){box.innerHTML='<span class="bad">'+esc(e.message)+'</span>';}
}
// ---- performance ----
async function applyPreset(name){
  if(!confirm('Apply "'+name+'" LLM preset? Restart the hub afterwards.'))return;
  try{
    const r=await api('/api/performance?preset='+name,{method:'POST'});
    document.getElementById('perfmsg').textContent=r.ok?'Saved. Restart the hub to apply.':('Failed: '+JSON.stringify(r.error||r.detail));
  }catch(e){document.getElementById('perfmsg').textContent='Failed: '+e.message;}
}
async function runLatency(){
  document.getElementById('perfmsg').textContent='Measuring…';
  try{
    const r=await api('/api/latency',{method:'POST'});
    document.getElementById('perfmsg').textContent=r.ok?('ASR '+r.asr_total_s+'s · TTS first audio '+r.tts_first_audio_s+'s'):('Failed: '+r.error);
  }catch(e){document.getElementById('perfmsg').textContent='Failed: '+e.message;}
}
function renderLogs(){document.getElementById('logtitle').textContent=document.getElementById('logname').value;document.getElementById('logs').textContent=(LOGS||[]).join('');}
function renderLibreEcho(){
  document.getElementById('leinfo').innerHTML='<b>Stable: 0.14.6</b> (17 Nov 2026) · installed: 0.14.5<br>'+
    '<span class="ok">● In-place OTA is supported</span> — no reinstall needed.<br>'+
    'Update from the speaker control centre: <b>System › System update › Check for updates</b>.<br>'+
    '<span class="bad">Do not</span> upload the update file by hand — broken in 0.14.6.<br>'+
    'Relevant fixes: Sendspin resumes after voice replies/mute, Wi-Fi pauses fixed, '+
    'repeatable alarms & spoken reminders, AirPlay skip/seek fixes.<br>'+
    'Known issue: AirPlay from Music Assistant still needs Sendspin, AirPlay 2-compatibility '+
    'or AirPlay 1 (RAOP). Keep the signed A/B rollback slot.';
}
async function loadLogs(){
  LOGS=(await api('/api/logs?name='+encodeURIComponent(document.getElementById('logname').value))).lines;renderLogs();
}
async function loadAll(){
  if(!T)return;
  try{DATA=await api('/api/summary');render();await loadLogs();}
  catch(e){if(e.message==='unauthorized')showLogin('Session expired. Enter the token again.');}
}
async function restartHub(){
  if(!confirm('Restart the hub?'))return;
  try{const r=await api('/api/restart',{method:'POST'});alert(r.ok?'Restart requested':'Restart failed: '+JSON.stringify(r));}
  catch(e){alert('Restart failed: '+e.message);}
}
document.getElementById('logname').onchange=loadLogs;
applyPrefs();
if(T){
  showLogin('Checking saved token…');
  api('/api/summary').then(d=>{DATA=d;hideLogin();render();loadLogs();loadVoices();loadIntents();loadSpeaker();loadLocation();})
    .catch(()=>{T='';sessionStorage.removeItem('olive_token');showLogin('Saved token was rejected. Check HUB_TOKEN in .env.');});
}else showLogin();
</script></body></html>"""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [OLIVE-DASH] %(levelname)s: %(message)s")
    web.run_app(make_app(), host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
