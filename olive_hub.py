#!/usr/bin/env python3
"""
Olive Hub — voice / music / automation orchestrator for the LibreEcho speaker.

Replaces the fast-path chain in New_pipe2.py with:

  * one speech path (no double announcements)
  * device-native radio, transport, volume and timers (no fake "playing music")
  * a real intent registry (English + Italian) instead of `if "play" in text`
  * optional Home Assistant bridge (REST) that degrades cleanly when unset
  * async I/O everywhere, token caching, timeouts, and endpoint auth

Run:
    python olive_hub.py                 # starts HTTP (:8765) + Wyoming ASR (:10300)
    python olive_hub.py --dry-run       # no device/HA network calls, for testing

Test without a microphone:
    curl -X POST localhost:8765/command -H 'X-Olive-Token: ...' -d '{"text":"what time is it"}'
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import datetime as dt
import difflib
import json
import logging
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import webbrowser
import wave
import io
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo(os.getenv("OLIVE_TIMEZONE", "Europe/Rome"))
except Exception:
    LOCAL_TZ = dt.datetime.now().astimezone().tzinfo

import aiohttp
from aiohttp import web

# Optional feature modules (news, calendar, Telegram, facts, mood radio).
from calendar_ics import calendar_path, get_today_events
from calendar_write import append_event, events_for
from places import Places
from extras import (ShoppingList, convert_currency as fx_convert,
                    convert_units, joke as tell_joke, quote as daily_quote)
from web_helpers import flights_url as build_flights_url, youtube_url as resolve_youtube_url
import train_provider
import flights_provider as flights
import whatsapp as whatsapp_bridge
import voice_personas as voices
from iot_discover import (REPORT as IOT_REPORT, known_ips as iot_known_ips,
                         new_since, run_scan as iot_run_scan, stage as iot_stage)
from messenger import send_telegram
from music_catalog import MusicCatalog
from memory import PermanentMemory
from news import search_news
from personal_facts import PersonalFacts
from tv import TvControl
from iot import IotRegistry

SCHEDULE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "olive_schedule.json")

try:  # Wyoming is only needed for the voice front-end
    from wyoming.server import AsyncEventHandler, AsyncTcpServer
    from wyoming.client import AsyncTcpClient
    from wyoming.event import Event
    from wyoming.asr import Transcribe, Transcript
    from wyoming.audio import AudioStart, AudioChunk, AudioStop

    WYOMING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    WYOMING_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OLIVE-HUB] %(levelname)s: %(message)s",
)
log = logging.getLogger("olive-hub")

FASTPATH_PREFIX = "__FASTPATH__:"


# =====================================================================
# CONFIGURATION
# =====================================================================
def strip_inline_comment(value: str) -> str:
    """Drop a trailing '# comment' from an unquoted .env value."""
    value = value.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end > 0:
            return value[1:end]
    match = re.search(r"\s+#", value)
    if match:
        value = value[: match.start()]
    return value.strip()


def load_dotenv(path: str) -> None:
    """Minimal .env loader; does not overwrite real environment variables."""
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = strip_inline_comment(value)
            os.environ.setdefault(key, value)


def env_str(name: str, default: str = "") -> str:
    return strip_inline_comment(os.environ.get(name, default) or "")


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = env_str(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


def env_rgb(name: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """Parse '255,190,0' or '#ffbe00' into an RGB triple."""
    raw = env_str(name, "")
    if not raw:
        return default
    raw = raw.strip()
    try:
        if raw.startswith("#"):
            value = raw.lstrip("#")
            if len(value) == 3:
                value = "".join(ch * 2 for ch in value)
            return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
        parts = [int(p) for p in re.split(r"[,\s]+", raw) if p]
        if len(parts) == 3:
            return (min(255, max(0, parts[0])), min(255, max(0, parts[1])),
                    min(255, max(0, parts[2])))
    except ValueError:
        pass
    return default


@dataclass
class Settings:
    # LibreEcho speaker
    speaker_url: str = "http://192.168.1.228:8080/api/v1"
    speaker_user: str = "olive"
    speaker_password: str = ""
    speaker_timeout: float = 3.0

    # Hub HTTP endpoint
    hub_host: str = "127.0.0.1"
    hub_port: int = 8765
    hub_token: str = ""  # when set, required as X-Olive-Token

    # Wyoming ASR proxy
    asr_host: str = "127.0.0.1"
    asr_port: int = 10300
    whisper_host: str = "127.0.0.1"
    whisper_port: int = 10301
    asr_timeout: float = 15.0

    # LLM passthrough (optional)
    llm_url: str = ""
    llm_token: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    llm_model_fallback: str = "openai/gpt-oss-20b"
    llm_max_tokens: int = 900
    llm_timeout: float = 30.0

    # Home Assistant (optional)
    ha_url: str = ""
    ha_token: str = ""
    ha_timeout: float = 5.0

    # Tuya (optional, direct — no Home Assistant)
    tuya_region: str = "eu"
    tuya_api_key: str = ""
    tuya_api_secret: str = ""
    tuya_device_id: str = ""

    # Local IoT (no cloud): Tuya devices with a known local key
    iot_enabled: bool = True
    iot_keys_file: str = ""

    # Behaviour
    city: str = "San Vincenzo"
    lat: Optional[float] = None
    lon: Optional[float] = None
    units: str = "metric"  # metric -> Celsius, imperial -> Fahrenheit
    language: str = "en"
    duck_music: bool = True
    duck_mode: str = "auto"  # auto | pause | volume | none
    radio_search: bool = True  # allow radio-browser lookups for unknown genres
    radio_prefer_ma: bool = True  # named stations stream through Music Assistant
    suppress_tts_when_device_speaks: bool = True
    dry_run: bool = False

    # Conversation (voice)
    conversation_ttl: float = 120.0     # forget the thread after this many seconds
    conversation_turns: int = 10        # keep at most this many messages
    system_prompt: str = (
        "You are Olive, a calm, concise voice assistant in a living room in Italy. "
        "Answer in one or two short spoken sentences. Never use markdown, lists, "
        "URLs, emoji or stage directions. Use the recent conversation for context. "
        "Never claim you switched a light or appliance unless the system already did it."
    )
    asr_empty_placeholder: str = "olive"  # wakes the assistant when the STT is empty

    # LED theme (device profiles)
    led_theme: bool = True
    led_listening_rgb: tuple[int, int, int] = (255, 190, 0)   # yellow, conversation
    led_thinking_rgb: tuple[int, int, int] = (255, 96, 0)     # orange, cloud AI
    led_brightness: int = 100

    # The LibreEcho agent asks for SSE; set STREAM_CHAT=0 if its parser chokes.
    stream_chat: bool = True

    # Follow-up listening: reopen the microphone after a spoken reply so the
    # conversation can continue without repeating the wake word.
    follow_up: bool = True
    follow_up_delay: float = 2.5   # seconds after the reply before listening
    pending_ttl: float = 60.0      # clarification window for timer/alarm/reminder

    # Permanent memory (facts, notes, recent topics) that survives restarts
    memory_enabled: bool = True
    memory_file: str = ""          # empty = olive_memory.json next to the hub

    # Music Assistant (optional alternative to device-native radio)
    ma_url: str = ""
    ma_token: str = ""
    ma_player_id: str = ""
    ma_enabled: bool = False

    # TV: Chromecast for media, ADB for Android TV power/apps
    tv_enabled: bool = True
    tv_cast_name: str = ""            # fuzzy name, e.g. "living room"
    tv_discovery_timeout: float = 8.0
    tv_cache_seconds: float = 60.0
    tv_adb_host: str = ""             # empty = use the cast device's IP
    tv_adb_port: int = 5555
    tv_mac: str = ""                  # for Wake-on-LAN when the TV is fully off
    adb_path: str = ""                # empty = find "adb" on PATH

    @classmethod
    def from_env(cls) -> "Settings":
        # Reuse the LLM keys the existing project already has, so the hub works
        # without asking for new credentials (Groq first, then Ollama, then custom).
        llm_url = env_str("LLM_URL")
        llm_token = env_str("LLM_TOKEN")
        if not llm_url:
            provider = env_str("AI_PROVIDER", "").lower()
            groq_base = env_str("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
            ollama_base = env_str("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
            if provider == "groq" and env_str("GROQ_API_KEY"):
                llm_url, llm_token = groq_base + "/chat/completions", env_str("GROQ_API_KEY")
            elif provider == "ollama" or (not provider and env_str("OLLAMA_BASE_URL")):
                llm_url = ollama_base + "/chat/completions"
            elif env_str("OPENAI_API_KEY"):
                base = env_str("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
                llm_url, llm_token = base + "/chat/completions", env_str("OPENAI_API_KEY")

        return cls(
            speaker_url=env_str("SPEAKER_URL", cls.speaker_url),
            speaker_user=env_str("SPEAKER_USER", cls.speaker_user),
            speaker_password=env_str("SPEAKER_PASSWORD", ""),
            hub_host=env_str("HUB_HOST", cls.hub_host),
            hub_port=env_int("HUB_PORT", cls.hub_port),
            hub_token=env_str("HUB_TOKEN", ""),
            asr_host=env_str("ASR_HOST", cls.asr_host),
            asr_port=env_int("ASR_PORT", cls.asr_port),
            whisper_host=env_str("WHISPER_HOST", cls.whisper_host),
            whisper_port=env_int("WHISPER_PORT", cls.whisper_port),
            asr_timeout=float(env_str("ASR_TIMEOUT", "15")),
            llm_url=llm_url,
            llm_token=llm_token,
            llm_model=env_str("LLM_MODEL", cls.llm_model),
            llm_model_fallback=env_str("LLM_MODEL_FALLBACK", cls.llm_model_fallback),
            llm_max_tokens=env_int("LLM_MAX_TOKENS", cls.llm_max_tokens),
            ha_url=(env_str("HA_URL") or env_str("HOME_ASSISTANT_URL")).rstrip("/"),
            ha_token=env_str("HA_TOKEN") or env_str("HOME_ASSISTANT_TOKEN"),
            tuya_region=env_str("TUYA_REGION", cls.tuya_region),
            tuya_api_key=env_str("TUYA_API_KEY", ""),
            tuya_api_secret=env_str("TUYA_API_SECRET", ""),
            tuya_device_id=env_str("TUYA_DEVICE_ID", ""),
            iot_enabled=env_bool("IOT_ENABLED", True),
            iot_keys_file=env_str("IOT_KEYS_FILE", ""),
            city=env_str("OLIVE_CITY", cls.city),
            lat=float(env_str("WEATHER_LAT")) if env_str("WEATHER_LAT") else None,
            lon=float(env_str("WEATHER_LON")) if env_str("WEATHER_LON") else None,
            units=env_str("OLIVE_UNITS", cls.units).lower(),
            language=env_str("OLIVE_LANGUAGE", cls.language).lower(),
            duck_music=env_bool("DUCK_MUSIC", True),
            duck_mode=env_str("DUCK_MODE", "auto").lower(),
            radio_search=env_bool("RADIO_SEARCH", True),
            radio_prefer_ma=env_bool("RADIO_PREFER_MA", True),
            suppress_tts_when_device_speaks=env_bool("SUPPRESS_TTS_ON_DEVICE", True),
            dry_run=env_bool("OLIVE_DRY_RUN", False),
            conversation_ttl=float(env_str("CONVERSATION_TTL", "120")),
            conversation_turns=env_int("CONVERSATION_TURNS", 10),
            system_prompt=env_str("OLIVE_SYSTEM_PROMPT", cls.system_prompt),
            asr_empty_placeholder=env_str("ASR_EMPTY_PLACEHOLDER", cls.asr_empty_placeholder),
            led_theme=env_bool("LED_THEME", True),
            led_listening_rgb=env_rgb("LED_LISTENING_RGB", cls.led_listening_rgb),
            led_thinking_rgb=env_rgb("LED_THINKING_RGB", cls.led_thinking_rgb),
            led_brightness=env_int("LED_BRIGHTNESS", 100),
            stream_chat=env_bool("STREAM_CHAT", True),
            follow_up=env_bool("FOLLOW_UP", True),
            follow_up_delay=float(env_str("FOLLOW_UP_DELAY", "2.5")),
            pending_ttl=float(env_str("PENDING_TTL", "60")),
            memory_enabled=env_bool("MEMORY", True),
            memory_file=env_str("OLIVE_MEMORY_FILE", ""),
            ma_url=env_str("MA_URL", "").rstrip("/"),
            ma_token=env_str("MA_TOKEN", ""),
            ma_player_id=env_str("MA_PLAYER_ID", ""),
            ma_enabled=env_bool("MA_ENABLED", False),
            tv_enabled=env_bool("TV_ENABLED", True),
            tv_cast_name=env_str("TV_CAST_NAME", ""),
            tv_discovery_timeout=float(env_str("TV_DISCOVERY_TIMEOUT", "8")),
            tv_cache_seconds=float(env_str("TV_CACHE_SECONDS", "60")),
            tv_adb_host=env_str("TV_ADB_HOST", ""),
            tv_adb_port=env_int("TV_ADB_PORT", 5555),
            tv_mac=env_str("TV_MAC", ""),
            adb_path=env_str("ADB_PATH", ""),
        )


# =====================================================================
# LIBREECHO DEVICE CLIENT
# =====================================================================
class LibreEcho:
    """Async client for LibreEcho OS API v1 with CSRF handling and token cache."""

    def __init__(self, settings: Settings) -> None:
        self.base = settings.speaker_url.rstrip("/")
        self.user = settings.speaker_user
        self.password = settings.speaker_password
        self.timeout = aiohttp.ClientTimeout(total=settings.speaker_timeout)
        self.dry_run = settings.dry_run
        self._session: Optional[aiohttp.ClientSession] = None
        self._csrf: str = ""
        self._token: str = ""
        self._token_at: float = 0.0
        self._auth_lock: Optional[asyncio.Lock] = None
        self._stations: list[dict[str, Any]] = []
        self._stations_at: float = 0.0
        self.last_transport_at: float = 0.0

    async def start(self) -> None:
        if self.dry_run:
            return
        self._session = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ---- transport -------------------------------------------------
    async def _raw(self, method: str, path: str, payload: Optional[dict] = None,
                   timeout: Optional[float] = None,
                   confirm: bool = False) -> tuple[int, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        headers = {"Content-Type": "application/json"}
        if self._csrf:
            headers["X-LibreEcho-CSRF"] = self._csrf
        if confirm:
            headers["X-LibreEcho-Confirm"] = "confirm-device-action"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self.base}{path}"
        overrides: dict[str, Any] = {}
        if timeout:
            overrides["timeout"] = aiohttp.ClientTimeout(total=timeout)
        async with self._session.request(method, url, json=payload, headers=headers,
                                         **overrides) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = await resp.text()
            return resp.status, body

    async def _ensure_auth(self) -> None:
        if self._token and (time.monotonic() - self._token_at) < 1800:
            return
        if self._auth_lock is None:
            self._auth_lock = asyncio.Lock()
        async with self._auth_lock:
            # Another coroutine may have logged in while we waited.
            if self._token and (time.monotonic() - self._token_at) < 1800:
                return
            status, body = await self._raw("GET", "/config")
            if status == 200 and isinstance(body, dict):
                self._csrf = (body.get("data") or {}).get("csrf_token") or ""
            status, body = await self._raw(
                "POST", "/auth/login", {"username": self.user, "password": self.password}
            )
            if status != 200:
                raise RuntimeError(f"LibreEcho login failed (HTTP {status})")
            self._token = ((body or {}).get("data") or {}).get("token") or ""
            self._token_at = time.monotonic()
            if not self._token:
                raise RuntimeError("LibreEcho login returned no token")

    async def call(self, method: str, path: str, payload: Optional[dict] = None,
                   timeout: Optional[float] = None,
                   confirm: bool = False) -> Any:
        """Authenticated call with one re-login retry on 401."""
        if self.dry_run:
            return {"dry_run": True, "path": path, "payload": payload}
        await self._ensure_auth()
        status, body = await self._raw(method, path, payload, timeout, confirm)
        if status == 401:
            self._token = ""
            await self._ensure_auth()
            status, body = await self._raw(method, path, payload, timeout, confirm)
        if status >= 400:
            raise RuntimeError(f"LibreEcho {method} {path} -> HTTP {status}: {str(body)[:200]}")
        return body

    # ---- speech ----------------------------------------------------
    async def announce(self, text: str, timeout: float = 30.0) -> None:
        # The device answers only once it has finished synthesising/speaking,
        # so a long text needs far more than the 3 s default.
        await self.call("POST", "/audio/announce", {"text": text}, timeout=timeout)

    async def stop_announce(self) -> None:
        with contextlib.suppress(Exception):
            await self.call("POST", "/audio/announce/stop")

    # ---- music -----------------------------------------------------
    async def stations(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self.dry_run:
            # Mirror the real catalog so dry-run routing behaves like the device.
            return [{"word": station["word"], "name": station["name"]}
                    for station in CURATED_STATIONS]
        if self._stations and not refresh and (time.monotonic() - self._stations_at) < 300:
            return self._stations
        body = await self.call("GET", "/integrations/radio")
        data = (body or {}).get("data") or {}
        found: list[dict[str, Any]] = []
        if isinstance(data, dict) and isinstance(data.get("stations"), list):
            # Live shape (LibreEcho OS 0.14.x)
            found = [s for s in data["stations"] if isinstance(s, dict)]
        elif isinstance(data, dict):
            # Documented flat shape: station_0_word, station_0_name, ...
            count = int(data.get("station_count") or 0)
            for index in range(count):
                prefix = f"station_{index}_"
                entry = {
                    "word": data.get(prefix + "word") or "",
                    "name": data.get(prefix + "name") or "",
                    "url": data.get(prefix + "url") or "",
                    "enabled": data.get(prefix + "enabled", True),
                }
                if entry["word"] or entry["name"]:
                    found.append(entry)
        elif isinstance(data, list):
            found = data
        self._stations, self._stations_at = found, time.monotonic()
        return found

    async def set_stations(self, stations: list[dict[str, Any]]) -> Any:
        """Replace the device station list (max 32 on current firmware)."""
        payload: dict[str, Any] = {"station_count": len(stations)}
        for index, station in enumerate(stations):
            prefix = f"station_{index}_"
            payload[prefix + "word"] = station.get("word") or ""
            payload[prefix + "name"] = station.get("name") or ""
            payload[prefix + "url"] = station.get("url") or ""
            payload[prefix + "enabled"] = bool(station.get("enabled", True))
        result = await self.call("PUT", "/integrations/radio", payload)
        self._stations, self._stations_at = [], 0.0
        return result

    async def play_radio(self, word: str) -> Any:
        result = await self.call("POST", "/integrations/radio/play", {"word": word})
        self.last_transport_at = time.monotonic()
        return result

    async def stop_radio(self) -> None:
        with contextlib.suppress(Exception):
            await self.call("POST", "/integrations/radio/stop")
        self.last_transport_at = time.monotonic()

    async def transport(self, action: str) -> Any:
        result = await self.call("POST", "/playback/transport", {"action": action})
        self.last_transport_at = time.monotonic()
        return result

    async def playback(self) -> dict[str, Any]:
        body = await self.call("GET", "/playback")
        return (body or {}).get("data") or {}

    async def current_station_word(self) -> Optional[str]:
        """Word of the station currently playing, if the source is radio."""
        try:
            state = await self.playback()
            if str(state.get("source") or "") != "radio":
                return None
            name = normalise(str((state.get("metadata") or {}).get("station") or ""))
            if not name:
                return None
            for station in await self.stations():
                if normalise(str(station.get("name") or "")) == name:
                    return str(station.get("word") or "") or None
        except Exception:
            return None
        return None

    async def is_playing(self) -> bool:
        try:
            state = await self.playback()
        except Exception:
            return False
        status = str(state.get("state") or state.get("status") or "").lower()
        if status:
            return status in ("playing", "play", "streaming")
        for key in ("playing", "is_playing", "active"):
            value = state.get(key)
            if isinstance(value, bool):
                return value
        return False

    async def can_pause(self) -> bool:
        try:
            transport = (await self.playback()).get("transport") or {}
        except Exception:
            return False
        return bool(transport.get("pause"))

    @staticmethod
    def _volume_key(audio: dict[str, Any]) -> str:
        # /audio has both `volume` and `notification_volume`; never hit the wrong one.
        if isinstance(audio.get("volume"), (int, float)):
            return "volume"
        for key, value in audio.items():
            if "volume" in key.lower() and "notification" not in key.lower() and isinstance(value, (int, float)):
                return key
        return "volume"

    async def set_volume(self, level: int) -> Any:
        level = max(0, min(100, int(level)))
        audio = (await self.call("GET", "/audio") or {}).get("data") or {}
        return await self.call("PUT", "/audio", {self._volume_key(audio): level})

    async def current_volume(self) -> Optional[int]:
        audio = (await self.call("GET", "/audio") or {}).get("data") or {}
        value = audio.get(self._volume_key(audio))
        return int(value) if isinstance(value, (int, float)) else None

    # ---- LED --------------------------------------------------------
    async def led(self) -> dict[str, Any]:
        return (await self.call("GET", "/led") or {}).get("data") or {}

    async def set_led_profile(self, name: str, rgb: tuple[int, int, int],
                              brightness: int = 100) -> Any:
        r, g, b = rgb
        return await self.call("PUT", "/led/profile", {
            "name": name, "r": int(r), "g": int(g), "b": int(b),
            "brightness": max(0, min(100, int(brightness))),
        })

    async def set_led(self, rgb: tuple[int, int, int], brightness: int = 100) -> Any:
        r, g, b = rgb
        return await self.call("PUT", "/led", {
            "colour": {"r": int(r), "g": int(g), "b": int(b)},
            "brightness": max(0, min(100, int(brightness))),
        })

    # ---- timers (device-native: they ring on the speaker) ----------
    async def add_timer(self, seconds: int, label: str = "") -> Any:
        return await self.call("POST", "/timers", {"seconds": int(seconds), "label": label})

    async def list_timers(self) -> list[dict[str, Any]]:
        data = (await self.call("GET", "/timers") or {}).get("data") or {}
        if isinstance(data, list):
            return data
        timers = data.get("timers") if isinstance(data, dict) else None
        return timers if isinstance(timers, list) else []

    async def cancel_timer(self, timer_id: str) -> Any:
        return await self.call("DELETE", f"/timers/{urllib.parse.quote(str(timer_id))}")

    async def dismiss_timers(self) -> Any:
        """Stop ringing timers (DELETE only works for pending ones)."""
        return await self.call("POST", "/timers/dismiss", {})


# =====================================================================
# RADIO LIBRARY (curated catalog, tags, on-demand search, playlists)
# =====================================================================
# Every URL below was probed and, where marked, played on the device.
# `tags` drive mood/genre requests; `pref` breaks ties (lower wins).
CURATED_STATIONS: list[dict[str, Any]] = [
    # pop / hits
    {"word": "capital", "name": "Capital FM", "pref": 1,
     "url": "https://media-ice.musicradio.com/CapitalMP3",
     "tags": ["pop", "hits", "top40", "uk"]},
    {"word": "poptron", "name": "PopTron", "pref": 2,
     "url": "http://ice1.somafm.com/poptron-128-mp3",
     "tags": ["pop", "electronic", "indie"]},
    {"word": "radio105", "name": "Radio 105", "pref": 3,
     "url": "https://icy.unitedradio.it/Radio105.mp3",
     "tags": ["pop", "hits", "it"]},
    {"word": "rai2", "name": "Rai Radio 2", "pref": 4,
     "url": "https://icestreaming.rai.it/2.mp3",
     "tags": ["pop", "it", "talk"]},
    {"word": "80s80s", "name": "80s80s Radio", "pref": 5,
     "url": "https://streams.80s80s.de/80s80s/mp3-192/streams.80s80s.de/",
     "tags": ["80s", "pop", "hits"]},
    # hip hop / rap / rnb
    {"word": "capitalxtra", "name": "Capital XTRA", "pref": 1,
     "url": "https://media-ice.musicradio.com/CapitalXTRALondonMP3",
     "tags": ["hiphop", "hip-hop", "rap", "rnb", "uk"]},
    {"word": "skyrockclassics", "name": "Skyrock Klassics", "pref": 2,
     "url": "https://icecast.skyrock.net/s/klassiks_mp3_128k",
     "tags": ["hiphop", "rap", "80s", "oldschool"]},
    {"word": "bbc1xtra", "name": "BBC Radio 1Xtra", "pref": 3,
     "url": "http://as-hls-ww-live.akamaized.net/pool_92079267/live/ww/bbc_1xtra/"
            "bbc_1xtra.isml/bbc_1xtra-audio%3d128000.norewind.m3u8",
     "tags": ["hiphop", "rap", "rnb", "uk"]},
    {"word": "skyrock", "name": "Skyrock", "pref": 4,
     "url": "https://icecast.skyrock.net/s/natio_mp3_128k",
     "tags": ["hiphop", "rap", "fr"]},
    {"word": "fipgroove", "name": "FIP Groove", "pref": 5,
     "url": "https://icecast.radiofrance.fr/fipgroove-midfi.mp3",
     "tags": ["hiphop", "soul", "groove", "funk", "jazz"]},
    {"word": "mouv", "name": "Mouv", "pref": 6,
     "url": "https://icecast.radiofrance.fr/mouv-midfi.mp3",
     "tags": ["hiphop", "rap", "fr"]},
    {"word": "fluid", "name": "Fluid", "pref": 7,
     "url": "http://ice1.somafm.com/fluid-128-mp3",
     "tags": ["hiphop", "instrumental", "chill", "triphop"]},
    # 80s
    {"word": "u80s", "name": "Underground 80s", "pref": 1,
     "url": "http://ice1.somafm.com/u80s-128-mp3",
     "tags": ["80s", "synth", "newwave", "pop"]},
    # electronic
    {"word": "defcon", "name": "Def Con Radio", "pref": 1,
     "url": "http://ice1.somafm.com/defcon-128-mp3",
     "tags": ["electronic", "ambient", "gaming"]},
    {"word": "beatblender", "name": "Beat Blender", "pref": 2,
     "url": "http://ice1.somafm.com/beatblender-128-mp3",
     "tags": ["house", "deep", "electronic", "dance"]},
    # chill / ambient / rainy day
    {"word": "groove", "name": "Groove Salad", "pref": 1,
     "url": "http://ice1.somafm.com/groovesalad-128-mp3",
     "tags": ["chill", "ambient", "downtempo", "lounge"]},
    {"word": "dronezone", "name": "Drone Zone", "pref": 1,
     "url": "http://ice1.somafm.com/dronezone-128-mp3",
     "tags": ["ambient", "rainy", "rainyday", "sleep", "chill"]},
    {"word": "lush", "name": "Lush", "pref": 2,
     "url": "http://ice1.somafm.com/lush-128-mp3",
     "tags": ["chill", "vocal", "rainy", "rainyday", "downtempo"]},
    {"word": "deepspace", "name": "Deep Space One", "pref": 3,
     "url": "http://ice1.somafm.com/deepspaceone-128-mp3",
     "tags": ["ambient", "space", "rainy", "chill"]},
    {"word": "ambient", "name": "Ambient Sleeping Pill", "pref": 4,
     "url": "http://radio.stereoscenic.com/asp-s",
     "tags": ["ambient", "sleep", "rainy", "chill"]},
    {"word": "secretagent", "name": "Secret Agent", "pref": 5,
     "url": "http://ice1.somafm.com/secretagent-128-mp3",
     "tags": ["lounge", "chill", "spy"]},
    {"word": "rmc", "name": "Radio Monte Carlo", "pref": 6,
     "url": "https://icy.unitedradio.it/RMC.mp3",
     "tags": ["chill", "it", "lounge"]},
    # jazz / soul
    {"word": "fip", "name": "FIP", "pref": 1,
     "url": "https://icecast.radiofrance.fr/fip-midfi.mp3",
     "tags": ["eclectic", "jazz", "world", "fr"]},
    {"word": "fipjazz", "name": "FIP Jazz", "pref": 2,
     "url": "https://icecast.radiofrance.fr/fipjazz-midfi.mp3",
     "tags": ["jazz", "fr"]},
    # classical
    {"word": "classicfm", "name": "Classic FM", "pref": 1,
     "url": "https://media-ice.musicradio.com/ClassicFMMP3",
     "tags": ["classical", "uk"]},
    {"word": "rai3", "name": "Rai Radio 3", "pref": 2,
     "url": "https://icestreaming.rai.it/3.mp3",
     "tags": ["classical", "culture", "it"]},
    # italian
    {"word": "rai1", "name": "Rai Radio 1", "pref": 1,
     "url": "https://icestreaming.rai.it/1.mp3",
     "tags": ["news", "talk", "it"]},
    {"word": "virgin", "name": "Virgin Radio", "pref": 1,
     "url": "https://icy.unitedradio.it/Virgin.mp3",
     "tags": ["rock", "it"]},
    {"word": "deejay", "name": "Radio Deejay", "pref": 1,
     "url": "https://4c4b867c89244861ac216426883d1ad0.msvdn.net/radiodeejay/radiodeejay/master.m3u8",
     "tags": ["pop", "it", "dance"]},
    # news / talk
    {"word": "lbc", "name": "LBC", "pref": 1,
     "url": "https://media-ice.musicradio.com/LBCUKMP3",
     "tags": ["news", "talk", "uk"]},
]

CURATED_META = {station["word"]: station for station in CURATED_STATIONS}

STOPWORDS = {
    "play", "put", "metti", "metti su", "riproduci", "accendi", "please", "some", "the",
    "and", "for", "per", "di", "della", "delle", "dei", "degli", "del", "music", "musica",
    "radio", "station", "stazione", "canzone", "song", "track", "qualcosa", "dammi",
    "voglio", "want", "hear", "ascoltare", "mi", "fa", "un", "una", "uno", "il", "la",
}

ALIASES = [
    (r"\bhip[\s-]?hop\b", "hiphop"),
    (r"\br\s*(?:&|and|'n')\s*b\b", "rnb"),
    (r"\bold[\s-]?school\b", "oldschool"),
    (r"\btop[\s-]?40\b", "top40"),
    (r"\beighties\b", "80s"),
    (r"\bnineties\b", "90s"),
    (r"\brainy[\s-]?day\b", "rainy"),
    (r"\brainy\b", "rainy"),
    (r"\bitalian\b", "it"),
    (r"\benglish\b", "uk"),
]


def keywords(text: str) -> list[str]:
    value = normalise(text)
    for pattern, replacement in ALIASES:
        value = re.sub(pattern, replacement, value)
    value = re.sub(r"[^a-z0-9' ]", " ", value)
    return [word for word in value.split() if len(word) > 1 and word not in STOPWORDS]


def score_station(station: dict[str, Any], words: list[str]) -> int:
    tags = {str(tag).lower() for tag in (station.get("tags") or [])}
    haystack = normalise(f"{station.get('name', '')} {station.get('word', '')}")
    score = 0
    for word in words:
        pattern = rf"\b{re.escape(word)}"
        if word in tags:
            score += 3
        elif any(re.search(pattern, tag) for tag in tags):
            score += 2
        if re.search(pattern, haystack):
            score += 2
    return score


def slugify(value: str, limit: int = 16) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", normalise(value))
    return (slug[:limit] or "station")


class RadioLibrary:
    """Device station list + moods + on-demand search, behind one interface."""

    SEARCH_URL = "https://de1.api.radio-browser.info/json/stations/search"

    def __init__(self, device: LibreEcho, settings: "Settings", http: aiohttp.ClientSession) -> None:
        self.device = device
        self.settings = settings
        self.http = http
        self._queue: list[dict[str, Any]] = []
        self._queue_pos = 0
        self._online: dict[str, tuple[dict[str, Any], float]] = {}
        self._dynamic: set[str] = set()
        self._seeded = False

    # -- list management ---------------------------------------------
    @staticmethod
    def _is_stream(url: Any) -> bool:
        text = str(url or "")
        return text.startswith("http") and "tunein.com" not in text

    def _decorate(self, station: dict[str, Any]) -> dict[str, Any]:
        meta = CURATED_META.get(str(station.get("word") or ""))
        if not meta:
            return station
        return {**station, "tags": meta.get("tags") or [], "pref": meta.get("pref", 50)}

    async def all(self, refresh: bool = False) -> list[dict[str, Any]]:
        return [self._decorate(station) for station in await self.device.stations(refresh)]

    async def ensure_seeded(self, force: bool = False) -> None:
        if self.device.dry_run:
            return
        current = await self.device.stations(refresh=True)
        words = {str(station.get("word") or "") for station in current}
        missing = [station for station in CURATED_STATIONS if station["word"] not in words]
        if not missing and current and not force:
            self._seeded = True
            return
        curated_words = {station["word"] for station in CURATED_STATIONS}
        merged: list[dict[str, Any]] = [
            {"word": s["word"], "name": s["name"], "url": s["url"], "enabled": True}
            for s in CURATED_STATIONS
        ]
        for station in current:
            word = str(station.get("word") or "")
            if word in curated_words or not self._is_stream(station.get("url")):
                continue
            merged.append({
                "word": word,
                "name": station.get("name") or word,
                "url": station.get("url"),
                "enabled": station.get("enabled", True),
            })
        await self.device.set_stations(merged[:32])
        self._seeded = True
        log.info("radio: seeded %d stations (%d curated)", min(len(merged), 32), len(CURATED_STATIONS))

    async def add(self, entry: dict[str, Any]) -> Optional[dict[str, Any]]:
        current = list(await self.device.stations(refresh=True))
        existing = {str(s.get("word")) for s in current}
        word = str(entry.get("word") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9 -]*", word or ""):
            word = self._word_for(str(entry.get("name") or word), existing)
            entry = {**entry, "word": word}
        if word in existing:
            return next((s for s in current if str(s.get("word")) == word), entry)
        if len(current) >= 32:
            victim = next((s for s in reversed(current) if str(s.get("word")) in self._dynamic), None)
            if victim is None:
                victim = current[-1]
            current = [s for s in current if s is not victim]
            log.info("radio: evicted %s to make room", victim.get("word"))
        current.append({
            "word": word,
            "name": str(entry.get("name") or word)[:60],
            "url": entry.get("url"),
            "enabled": True,
        })
        try:
            await self.device.set_stations(current)
        except Exception as exc:
            log.warning("radio: could not add %r: %s", entry.get("name"), exc)
            return None
        self._dynamic.add(word)
        return entry

    # -- lookup ------------------------------------------------------
    async def _fetch_rows(self, params: dict[str, str]) -> list[dict[str, Any]]:
        async with self.http.get(
            self.SEARCH_URL, params=params,
            headers={"User-Agent": "OliveHub/1.0"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            rows = await resp.json(content_type=None)
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _word_for(self, name: str, existing: set[str]) -> str:
        base = slugify(name, 20) or "station"
        word, counter = base, 2
        while word in existing:
            suffix = str(counter)
            word = base[:20 - len(suffix)] + suffix
            counter += 1
        return word

    async def search_online(self, query: str) -> Optional[dict[str, Any]]:
        if not self.settings.radio_search or self.device.dry_run:
            return None
        words = keywords(query)
        if not words:
            return None
        term = " ".join(words)
        cached = self._online.get(term)
        if cached and (time.monotonic() - cached[1]) < 900:
            return cached[0]

        rows: list[dict[str, Any]] = []
        for params in (
            {"name": term, "limit": "20", "hidebroken": "true", "order": "clickcount", "reverse": "true"},
            {"tagList": ",".join(words), "limit": "20", "hidebroken": "true", "order": "clickcount", "reverse": "true"},
        ):
            try:
                rows.extend(await self._fetch_rows(params))
            except Exception as exc:
                log.warning("station search failed for %r: %s", term, exc)

        best: Optional[dict[str, Any]] = None
        best_score = 0
        seen: set[str] = set()
        for row in rows:
            url = str(row.get("url_resolved") or "")
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            codec = str(row.get("codec") or "").upper()
            if codec not in ("MP3", "AAC", "AAC+", "MP3+"):
                continue
            try:
                bitrate = int(row.get("bitrate") or 0)
            except (TypeError, ValueError):
                bitrate = 0
            if bitrate < 96:
                continue
            name = str(row.get("name") or term).strip()
            tags = [tag.strip().lower() for tag in str(row.get("tags") or "").split(",") if tag.strip()]
            candidate = {"name": name, "url": url, "tags": tags, "word": "", "pref": 50}
            relevance = score_station(candidate, words)
            if relevance <= 0:  # never pick a station that does not mention the request
                continue
            score = relevance + (2 if bitrate >= 128 else 0) - (2 if len(name) > 70 else 0)
            if score > best_score:
                best, best_score = candidate, score
        if best is None:
            return None
        existing = {str(s.get("word")) for s in await self.all()}
        best["word"] = self._word_for(best["name"], existing)
        self._online[term] = (best, time.monotonic())
        return best

    async def best(self, query: str) -> Optional[dict[str, Any]]:
        words = keywords(query)
        stations = await self.all()
        # An actual station name always beats a genre/tag guess.
        exact = find_station(stations, clean_station_name(query))
        if exact is not None:
            return exact
        best: Optional[dict[str, Any]] = None
        best_key: tuple[int, int] = (0, 0)
        for station in stations:
            score = score_station(station, words)
            if score <= 0:
                continue
            key = (score, -int(station.get("pref") or 50))
            if key > best_key:
                best, best_key = station, key
        if best is not None:
            return best
        return await self.search_online(query)

    async def ma_url(self, station: dict[str, Any]) -> str:
        """A direct stream URL Music Assistant can actually decode.

        The device handles BBC HLS (.m3u8) fine, but MA's ffmpeg pipeline
        stalls on those feeds. Prefer the station's direct MP3/AAC URL and
        fall back to a radio-browser match when it only has HLS.
        """
        url = str(station.get("url") or "")
        if url and not re.search(r"\.(?:m3u8|m3u)(?:\?|$)", url, re.IGNORECASE):
            return url
        name = str(station.get("name") or station.get("word") or "")
        if name:
            try:
                alternate = await self.search_online(name)
            except Exception as exc:
                log.debug("MA stream lookup failed for %r: %s", name, exc)
                alternate = None
            alt_url = str((alternate or {}).get("url") or "")
            if alt_url.startswith("http"):
                log.info("radio: MA stream for %r uses %s", name, alt_url[:80])
                return alt_url
        return url

    async def find_device_station(self, query: str) -> Optional[dict[str, Any]]:
        """Best match among stations the device already knows, without adding any."""
        stations = await self.all()
        if not stations:
            return None
        wanted = clean_station_name(query)
        if not wanted:
            return None
        exact = find_station(stations, wanted)
        if exact is not None:
            return exact
        words = keywords(query)
        best: Optional[dict[str, Any]] = None
        best_score = 0
        for station in stations:
            score = score_station(station, words)
            if score > best_score:
                best, best_score = station, score
        return best

    # -- actions -----------------------------------------------------
    async def play(self, query: str) -> Optional[dict[str, Any]]:
        wanted = clean_station_name(query)
        stations = await self.all()
        if not stations:
            await self.ensure_seeded()
            stations = await self.all(refresh=True)
        chosen: Optional[dict[str, Any]] = None
        if not wanted or wanted in GENERIC_MUSIC:
            chosen = next(
                (s for s in stations if str(s.get("enabled", True)).lower() not in ("false", "0")),
                None,
            )
        else:
            chosen = await self.best(wanted)
            if chosen is not None and not any(
                str(s.get("word")) == str(chosen.get("word")) for s in stations
            ):
                chosen = await self.add(chosen)
        if chosen is None:
            return None
        await self.device.play_radio(chosen.get("word") or chosen.get("name"))
        return chosen

    async def playlist(self, query: str, count: int = 4) -> list[dict[str, Any]]:
        words = keywords(query)
        stations = await self.all()
        ranked = sorted(
            stations,
            key=lambda s: (score_station(s, words), -int(s.get("pref") or 50)),
            reverse=True,
        )
        picked = [s for s in ranked if score_station(s, words) >= 2][:count]
        if len(picked) < 2:
            single = await self.best(query)
            picked = [single] if single else picked
        if not picked:
            return []
        self._queue, self._queue_pos = picked, 0
        first = picked[0]
        if not any(str(s.get("word")) == str(first.get("word")) for s in stations):
            if await self.add(first) is None:
                return []
        await self.device.play_radio(first.get("word") or first.get("name"))
        return picked

    async def next(self) -> Optional[dict[str, Any]]:
        if self._queue:
            self._queue_pos = (self._queue_pos + 1) % len(self._queue)
            station = self._queue[self._queue_pos]
            await self.device.play_radio(station.get("word") or station.get("name"))
            return station
        stations = await self.all()
        if not stations:
            return None
        try:
            state = await self.device.playback()
            current = normalise(str(((state.get("metadata") or {}).get("station")) or ""))
        except Exception:
            current = ""
        index = next((i for i, s in enumerate(stations) if current and normalise(str(s.get("name"))) == current), -1)
        station = stations[(index + 1) % len(stations)]
        await self.device.play_radio(station.get("word") or station.get("name"))
        return station



ANNOUNCE_SETTLE = 1.2  # seconds the device needs after a stream change before TTS works
ANNOUNCE_LIMIT = 500   # the API caps announcement text at 511 characters


class MusicAssistant:
    """Optional Music Assistant bridge (disabled unless MA_ENABLED=1)."""

    def __init__(self, settings: Settings, http: aiohttp.ClientSession) -> None:
        self.settings = settings
        self.http = http

    @property
    def enabled(self) -> bool:
        return bool(self.settings.ma_enabled and self.settings.ma_url
                    and self.settings.ma_player_id)

    async def call(self, command: str, timeout: float = 12.0, **args: Any) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.settings.ma_token:
            headers["Authorization"] = f"Bearer {self.settings.ma_token}"
        payload: dict[str, Any] = {"command": command}
        if args:
            payload["args"] = args
        async with self.http.post(
            f"{self.settings.ma_url}/api", json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            if response.status != 200:
                log.warning("MA %s -> HTTP %s", command, response.status)
                return None
            return await response.json(content_type=None)

    async def playing(self) -> bool:
        """True when Music Assistant's queue is playing or paused."""
        if not self.enabled:
            return False
        try:
            data = await self.call("players/get", timeout=3.0,
                                   player_id=self.settings.ma_player_id)
        except Exception:
            return False
        state = str((data or {}).get("playback_state") or "").lower()
        return state in ("playing", "paused")

    async def verify_streaming(self, device: "LibreEcho", timeout: float = 10.0) -> bool:
        """Wait until the speaker reports that Sendspin audio is actually flowing."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = (await device.call("GET", "/sendspin", timeout=3)).get("data") or {}
            except Exception:
                status = {}
            if status.get("streaming"):
                return True
            await asyncio.sleep(1.0)
        return False

    async def ok(self, command: str, timeout: float = 6.0, **args: Any) -> bool:
        """True when Music Assistant accepted the command.

        MA answers most commands with a bare JSON ``null``, so the HTTP status
        is the only reliable success signal. Calls stay short: a wedged MA must
        never hold up the spoken reply.
        """
        headers = {"Content-Type": "application/json"}
        if self.settings.ma_token:
            headers["Authorization"] = f"Bearer {self.settings.ma_token}"
        payload: dict[str, Any] = {"command": command}
        if args:
            payload["args"] = args
        try:
            async with self.http.post(
                f"{self.settings.ma_url}/api", json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status != 200:
                    log.warning("MA %s -> HTTP %s", command, response.status)
                    return False
                return True
        except Exception as exc:
            log.warning("MA %s failed: %r", command, exc)
            return False

    async def play_url(self, url: str) -> bool:
        if not self.enabled or not url:
            return False
        queue = self.settings.ma_player_id
        # A stale queue item from an earlier stream makes play_media a no-op.
        await self.ok("player_queues/clear", 4.0, queue_id=queue)
        if not await self.ok("player_queues/play_media", 10.0, queue_id=queue, media=[url]):
            return False
        # play_media queues the item; an explicit play starts the queue even
        # when the universal player was idle.
        await self.ok("player_queues/play", 4.0, queue_id=queue)
        return True

    async def stop(self) -> bool:
        if not self.enabled:
            return False
        return await self.ok("player_queues/stop", queue_id=self.settings.ma_player_id)


class Speech:
    """Single speaking path with optional music ducking."""

    def __init__(self, device: LibreEcho, settings: Settings) -> None:
        self.device = device
        self.settings = settings
        self._lock = asyncio.Lock()
        self._pending: Optional[tuple[str, int, int]] = None  # ("volume", level, ducked)
        self._duck_task: Optional[asyncio.Task] = None

    @staticmethod
    def estimate_seconds(text: str) -> float:
        return max(2.0, min(30.0, len(text) / 13.0 + 1.0))

    async def _state(self) -> str:
        try:
            return str((await self.device.playback()).get("state") or "").lower()
        except Exception:
            return ""

    async def _wait_speech_finished(self) -> None:
        """Wait while the box is actively speaking (it flags state='announcing')."""
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if await self._state() not in ("announcing", "speaking"):
                return
            await asyncio.sleep(0.5)

    async def wait_until_done(self, timeout: float = 45.0) -> bool:
        """True once the queued announcement has finished playing.

        Returns False when the device never started speaking (dropped request)
        or the wait timed out, so callers can carry on either way.
        """
        started = time.monotonic()
        deadline = started + timeout
        began = False
        while time.monotonic() < deadline:
            state = await self._state()
            if state in ("announcing", "speaking"):
                began = True
            elif began:
                return True
            elif time.monotonic() - started > 12.0:
                return False
            await asyncio.sleep(0.4)
        return False

    async def _wait_announcement_done(self, start_timeout: float = 20.0,
                                      total_timeout: float = 60.0) -> None:
        """Wait for a queued announcement: synthesis can start ~10 s after queueing."""
        started = time.monotonic()
        deadline = started + total_timeout
        began = False
        while time.monotonic() < deadline:
            state = await self._state()
            if state in ("announcing", "speaking"):
                began = True
            elif began:
                return
            elif time.monotonic() - started > start_timeout:
                return  # never spoke (dropped by the device); stop waiting
            await asyncio.sleep(0.5)

    async def _current_radio_word(self) -> Optional[str]:
        return await self.device.current_station_word()

    async def say(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        """Announce proactively (timers, /command, events). Not used for pipeline replies."""
        text = text.strip()
        if not text:
            return
        if len(text) > ANNOUNCE_LIMIT:
            cut = text[:ANNOUNCE_LIMIT].rsplit(" ", 1)[0]
            text = (cut or text[:ANNOUNCE_LIMIT]) + "."
        restore: Optional[tuple[str, int, int]] = None
        station: Optional[str] = None
        async with self._lock:
            # Never queue a second line over one that is being spoken right now.
            await self._wait_speech_finished()
            if self.settings.duck_music and not self.settings.dry_run:
                station = await self._current_radio_word()
                restore = await self._duck()
            # Right after a stream starts/stops the box also refuses new speech.
            recent = time.monotonic() - self.device.last_transport_at
            if recent < ANNOUNCE_SETTLE:
                await asyncio.sleep(ANNOUNCE_SETTLE - recent)
            spoken = False
            for attempt in (1, 2):
                try:
                    await self.device.announce(text)
                    spoken = True
                    break
                except Exception as exc:
                    if attempt == 1 and "501" in str(exc):
                        log.info("device refused the announcement, retrying once")
                        await asyncio.sleep(1.0)
                    else:
                        # A failed announcement must never break the caller's HTTP response.
                        log.warning("announce failed: %s", exc)
            if not spoken and station:
                # A broken TTS attempt stops the stream; put the radio back on.
                log.info("resuming %s after failed announcement", station)
                with contextlib.suppress(Exception):
                    await self.device.play_radio(station)
        if restore:
            asyncio.create_task(self._unduck_after(restore))

    async def _duck(self) -> Optional[tuple[str, int, int]]:
        """Lower the music while speaking. Live radio streams cannot pause, so duck volume."""
        if self.settings.duck_mode == "none" or self._pending is not None:
            # Already ducked for an earlier announcement; its restore owns the level.
            return None
        try:
            state = await self.device.playback()
        except Exception:
            return None
        if str(state.get("state") or "").lower() != "playing":
            return None
        volume = await self.device.current_volume()
        if not volume or volume <= 5:
            return None
        ducked = max(5, int(volume * 0.35))
        try:
            await self.device.set_volume(ducked)
        except Exception:
            return None
        self._pending = ("volume", volume, ducked)
        return self._pending

    async def _unduck_after(self, restore: tuple[str, int, int]) -> None:
        _kind, level, ducked = restore
        try:
            await self._wait_announcement_done()
            current = await self.device.current_volume()
            if current is not None and current > ducked + 2:
                # Someone changed the volume while we spoke; keep their choice.
                log.info("volume changed during announcement (%s), not restoring %s",
                         current, level)
            else:
                await self.device.set_volume(level)
        except Exception as exc:  # pragma: no cover - device hiccup
            log.warning("restore after announcement failed: %s", exc)
        finally:
            self._pending = None

    async def duck_for_listening(self) -> Optional[tuple[str, int, int]]:
        """Drop music to a minimum while the user speaks a command.

        The device's volume is global, so this works for Music Assistant and
        native radio alike.  A watchdog restores it if the turn never finishes.
        """
        if (self.settings.duck_mode == "none" or self.settings.dry_run
                or self._pending is not None):
            return None
        try:
            state = await self.device.playback()
        except Exception:
            return None
        if str(state.get("state") or "").lower() != "playing":
            return None
        try:
            volume = await self.device.current_volume()
            if not volume or volume <= 5:
                return None
            target = max(1, min(10, int(volume * 0.15)))
            await self.device.set_volume(target)
        except Exception as exc:
            log.debug("listening duck failed: %s", exc)
            return None
        self._pending = ("volume", volume, target)
        log.info("music ducked for listening: %s -> %s", volume, target)
        self._duck_task = asyncio.create_task(self._auto_restore_listening(self._pending))
        return self._pending

    async def _auto_restore_listening(self, restore: tuple[str, int, int]) -> None:
        await asyncio.sleep(25.0)
        await self.restore_duck(restore)

    async def restore_duck(self, restore: Optional[tuple[str, int, int]]) -> None:
        """Undo a listening duck unless the user changed the volume meanwhile."""
        if not restore or self._pending != restore:
            return
        if self._duck_task and asyncio.current_task() is not self._duck_task:
            self._duck_task.cancel()
            self._duck_task = None
        _kind, level, ducked = restore
        try:
            await self._wait_announcement_done(start_timeout=3.0, total_timeout=15.0)
            current = await self.device.current_volume()
            if current is None or current <= ducked + 2:
                await self.device.set_volume(level)
                log.info("music volume restored after command: %s", level)
            else:
                log.info("volume changed during command (%s); keeping user's choice", current)
        except Exception as exc:
            log.warning("listening duck restore failed: %s", exc)
        finally:
            if self._pending == restore:
                self._pending = None


# =====================================================================
# TUYA (direct, no Home Assistant)
# =====================================================================
class TuyaHome:
    """Tuya Cloud API via tinytuya, wrapped so it never blocks the event loop.

    Needs a free Tuya IoT Platform project: Access ID + Access Secret + region.
    Run `python tuya_setup.py` to list your devices and write the .env keys.
    """

    def __init__(self, settings: "Settings") -> None:
        self.region = (settings.tuya_region or "eu").lower()
        self.api_key = settings.tuya_api_key
        self.api_secret = settings.tuya_api_secret
        self.api_device = settings.tuya_device_id
        self.enabled = bool(self.api_key and self.api_secret)
        self._cloud: Any = None
        self._devices: list[dict[str, Any]] = []
        self._devices_at = 0.0
        self.last_error = ""

    def _client(self) -> Any:
        if self._cloud is None:
            import tinytuya  # imported lazily so the hub runs without it
            self._cloud = tinytuya.Cloud(
                apiRegion=self.region,
                apiKey=self.api_key,
                apiSecret=self.api_secret,
                apiDeviceID=self.api_device or None,
            )
        return self._cloud

    async def devices(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        if self._devices and not refresh and (time.monotonic() - self._devices_at) < 60:
            return self._devices

        def fetch() -> Any:
            return self._client().getdevices(verbose=False)

        try:
            rows = await asyncio.to_thread(fetch)
        except Exception as exc:
            log.warning("Tuya device list failed: %s", exc)
            return self._devices
        if isinstance(rows, dict):
            if "result" not in rows and (rows.get("msg") or rows.get("code")):
                # e.g. 28841107 "data center suspended": the project exists but
                # is not enabled for this account/region.
                self.last_error = str(rows.get("msg") or rows.get("code"))
                log.warning("Tuya cloud unavailable: %s", self.last_error)
                return self._devices
            rows = rows.get("result")
        self._devices = [row for row in (rows or []) if isinstance(row, dict)]
        self._devices_at = time.monotonic()
        return self._devices

    async def resolve(self, phrase: str) -> Optional[dict[str, Any]]:
        wanted = normalise(phrase)
        if not wanted:
            return None
        try:
            devices = await self.devices()
        except Exception as exc:
            log.warning("Tuya lookup failed: %s", exc)
            return None
        best: Optional[dict[str, Any]] = None
        best_score = 0.0
        for device in devices:
            name = normalise(str(device.get("name") or ""))
            if not name:
                continue
            score = 0.0
            if wanted in name or name in wanted:
                score = 2.0 + len(wanted) / max(len(name), 1)
            else:
                words = [w for w in wanted.split() if len(w) > 2]
                if words and all(w in name for w in words):
                    score = 1.0 + len(words) / 10
            if score > best_score:
                best, best_score = device, score
        return best

    async def _status(self, device: dict[str, Any]) -> dict[str, Any]:
        """Return {code: value} for a device.

        Tuya's /status endpoint answers with a list of {"code","value"} objects;
        older/library paths give {"dps": {dp_id: value}}. Handle both.
        """
        def fetch() -> Any:
            return self._client().getstatus(device.get("id"))

        try:
            raw = await asyncio.to_thread(fetch)
        except Exception as exc:
            log.warning("Tuya status failed for %s: %s", device.get("name"), exc)
            return {}
        return self._flatten_status(raw)

    @staticmethod
    def _flatten_status(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        result = raw.get("result")
        if isinstance(result, list):
            return {str(item.get("code")): item.get("value")
                    for item in result if isinstance(item, dict) and item.get("code")}
        if isinstance(result, dict):
            if isinstance(result.get("dps"), dict):
                return {str(key): value for key, value in result["dps"].items()}
            if isinstance(result.get("status"), list):
                return {str(item.get("code")): item.get("value")
                        for item in result["status"] if isinstance(item, dict) and item.get("code")}
        if isinstance(raw.get("dps"), dict):
            return {str(key): value for key, value in raw["dps"].items()}
        return {}

    async def _switch_code(self, device: dict[str, Any], status: dict[str, Any]) -> str:
        for code in status:
            if str(code).startswith("switch"):
                return str(code)
        # Nothing readable yet (offline device): ask for the declared functions.
        def fetch() -> Any:
            return self._client().getfunctions(device.get("id"))

        try:
            raw = await asyncio.to_thread(fetch)
        except Exception as exc:
            log.debug("Tuya functions failed for %s: %s", device.get("name"), exc)
            raw = None
        result = raw.get("result") if isinstance(raw, dict) else None
        if isinstance(result, dict):
            functions = result.get("functions") or result.get("status") or []
            names = [str(item.get("code")) for item in functions if isinstance(item, dict)]
            for name in names:
                if name.startswith("switch"):
                    return name
        return "switch_1"

    async def is_on(self, device: dict[str, Any]) -> Optional[bool]:
        status = await self._status(device)
        for code, value in status.items():
            if str(code).startswith("switch") and isinstance(value, bool):
                return value
        # Some products report power under dp 1 instead of switch_1.
        if isinstance(status.get("1"), bool):
            return status["1"]
        return None

    async def set_power(self, device: dict[str, Any], on: bool) -> bool:
        status = await self._status(device)
        code = await self._switch_code(device, status)
        commands = [{"code": code, "value": bool(on)}]
        device_id = device.get("id")

        def send() -> Any:
            return self._client().sendcommand(device_id, commands)

        try:
            result = await asyncio.to_thread(send)
        except Exception as exc:
            log.warning("Tuya command failed for %s: %s", device.get("name"), exc)
            return False
        if isinstance(result, dict) and result.get("success") is False:
            log.warning("Tuya rejected %s for %s: %s", code, device.get("name"),
                        str(result.get("msg") or result)[:160])
            return False
        return True


# =====================================================================
# HOME ASSISTANT BRIDGE (optional)
# =====================================================================
class HomeAssistant:
    def __init__(self, settings: Settings) -> None:
        self.base = settings.ha_url
        self.token = settings.ha_token
        self.timeout = aiohttp.ClientTimeout(total=settings.ha_timeout)
        self.enabled = bool(self.base and self.token)
        self._session: Optional[aiohttp.ClientSession] = None
        self._states: list[dict[str, Any]] = []
        self._states_at: float = 0.0

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            )
        return self._session

    async def states(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        if self._states and not refresh and (time.monotonic() - self._states_at) < 30:
            return self._states
        session = await self._session_get()
        async with session.get(f"{self.base}/api/states") as resp:
            if resp.status != 200:
                raise RuntimeError(f"HA /api/states -> HTTP {resp.status}")
            self._states = await resp.json()
        self._states_at = time.monotonic()
        return self._states

    async def call(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        session = await self._session_get()
        async with session.post(f"{self.base}/api/services/{domain}/{service}", json=data) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"HA {domain}.{service} -> HTTP {resp.status}")
            self._states_at = 0.0
            return await resp.json()

    async def resolve(self, phrase: str) -> Optional[dict[str, Any]]:
        """Fuzzy-match a spoken name against entity_id / friendly_name."""
        try:
            states = await self.states()
        except Exception as exc:
            log.warning("HA state lookup failed: %s", exc)
            return None
        needle = re.sub(r"[^a-z0-9 ]", " ", phrase.lower()).strip()
        if not needle:
            return None
        best, best_score = None, 0.0
        for state in states:
            name = str((state.get("attributes") or {}).get("friendly_name") or "")
            haystack = f"{state.get('entity_id','')} {name}".lower().replace("_", " ")
            score = 0.0
            if needle in haystack:
                score = 2.0 + len(needle) / max(len(haystack), 1)
            else:
                words = [w for w in needle.split() if len(w) > 2]
                if words and all(w in haystack for w in words):
                    score = 1.0 + len(words) / 10
            if score > best_score:
                best, best_score = state, score
        return best if best_score > 0 else None


# =====================================================================
# WEATHER / KNOWLEDGE / MATH
# =====================================================================
class Weather:
    def __init__(self, settings: Settings, http: aiohttp.ClientSession) -> None:
        self.settings = settings
        self.http = http
        self._geo: dict[str, tuple[float, float, str]] = {}

    async def geocode(self, city: str) -> Optional[tuple[float, float, str]]:
        key = city.lower()
        if key in self._geo:
            return self._geo[key]
        url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {"name": city, "count": 5, "language": "en", "format": "json"}
        try:
            async with self.http.get(url, params=params, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                data = await resp.json()
        except Exception as exc:
            log.warning("geocoding failed for %s: %s", city, exc)
            return None
        results = (data or {}).get("results") or []
        if not results:
            return None
        # Prefer the biggest place with that name over a same-named suburb.
        def rank(entry: dict[str, Any]) -> tuple[int, int]:
            exact = 1 if str(entry.get("name", "")).lower() == city.lower() else 0
            return (exact, int(entry.get("population") or 0))
        top = max(results, key=rank)
        value = (float(top["latitude"]), float(top["longitude"]), top.get("name") or city)
        self._geo[key] = value
        return value

    async def report(self, city: Optional[str] = None, italian: bool = False) -> str:
        if city:
            geo = await self.geocode(city)
            if geo is None:
                return (f"Non trovo un luogo chiamato {city}."
                        if italian else f"I couldn't find a place called {city}.")
            lat, lon, _ = geo
            resolved = city.strip().title()  # speak the name the user asked for
        elif self.settings.lat is not None and self.settings.lon is not None:
            lat, lon, resolved = self.settings.lat, self.settings.lon, self.settings.city
        else:
            geo = await self.geocode(self.settings.city)
            if geo is None:
                return (f"Non trovo un luogo chiamato {self.settings.city}."
                        if italian else f"I couldn't find a place called {self.settings.city}.")
            lat, lon, resolved = geo
        imperial = self.settings.units == "imperial"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 1,
            "timezone": "auto",
        }
        if imperial:
            params["temperature_unit"] = "fahrenheit"
        try:
            async with self.http.get(
                "https://api.open-meteo.com/v1/forecast", params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
        except Exception as exc:
            log.warning("weather fetch failed: %s", exc)
            return ("Il servizio meteo non risponde in questo momento."
                    if italian else "The weather service isn't answering right now.")
        current = (data or {}).get("current") or {}
        daily = (data or {}).get("daily") or {}
        unit = "Fahrenheit" if imperial else "Celsius"
        temp = current.get("temperature_2m")
        feels = current.get("apparent_temperature")
        if temp is None:
            return ("Non riesco a leggere i dati meteo." if italian
                    else "I couldn't read the weather data.")
        if italian:
            text = (f"Ci sono {round(temp)} gradi a {resolved}, "
                    f"con percepiti {round(feels)}")
            highs = daily.get("temperature_2m_max") or []
            lows = daily.get("temperature_2m_min") or []
            rain = daily.get("precipitation_probability_max") or []
            if highs and lows:
                text += f". Oggi da {round(lows[0])} a {round(highs[0])} gradi"
            if rain:
                text += f", {round(rain[0])} percento probabilità di pioggia"
            return text + "."
        text = (
            f"It's {round(temp)} degrees {unit} in {resolved}, "
            f"feeling like {round(feels)}"
        )
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        rain = daily.get("precipitation_probability_max") or []
        if highs and lows:
            text += f". Today {round(lows[0])} to {round(highs[0])} degrees"
        if rain:
            text += f", {round(rain[0])} percent chance of rain"
        return text + "."

    async def sunset(self, days: int = 2) -> Optional[dt.datetime]:
        """Next local sunset as an aware datetime (uses the location's offset)."""
        lat, lon = self.settings.lat, self.settings.lon
        if lat is None or lon is None:
            geo = await self.geocode(self.settings.city)
            if geo is None:
                return None
            lat, lon, _ = geo
        params = {
            "latitude": lat, "longitude": lon,
            "daily": "sunset", "forecast_days": max(1, int(days)),
            "timezone": "auto",
        }
        try:
            async with self.http.get(
                "https://api.open-meteo.com/v1/forecast", params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
        except Exception as exc:
            log.warning("sunset lookup failed: %s", exc)
            return None
        offset = int((data or {}).get("utc_offset_seconds") or 0)
        tz = dt.timezone(dt.timedelta(seconds=offset))
        now = time.time()
        for raw in (((data or {}).get("daily") or {}).get("sunset") or []):
            if not raw:
                continue
            try:
                value = dt.datetime.fromisoformat(str(raw)).replace(tzinfo=tz)
            except ValueError:
                continue
            if value.timestamp() > now:
                return value
        return None


class Knowledge:
    def __init__(self, http: aiohttp.ClientSession) -> None:
        self.http = http

    async def lookup(self, query: str) -> Optional[str]:
        headers = {"User-Agent": "OliveHub/1.0"}
        try:
            async with self.http.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                headers=headers, timeout=aiohttp.ClientTimeout(total=4),
            ) as resp:
                data = await resp.json(content_type=None)
            if data.get("AbstractText"):
                return data["AbstractText"]
        except Exception as exc:
            log.debug("DDG lookup failed: %s", exc)
        try:
            slug = urllib.parse.quote(query.replace(" ", "_"))
            async with self.http.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}",
                headers=headers, timeout=aiohttp.ClientTimeout(total=4),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("extract")
        except Exception as exc:
            log.debug("Wikipedia lookup failed: %s", exc)
        return None


def safe_math(expression: str) -> Optional[float]:
    """Evaluate arithmetic only — never eval() a transcript."""
    allowed_bin = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv}
    allowed_unary = {ast.UAdd, ast.USub}

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in allowed_bin:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            return left ** right
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_unary:
            value = walk(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        raise ValueError("unsupported expression")

    try:
        return walk(ast.parse(expression, mode="eval"))
    except Exception:
        return None


# =====================================================================
# INTENTS
# =====================================================================
@dataclass
class Conversation:
    """Short-lived voice thread: forgotten after `ttl` seconds of silence."""

    ttl: float = 120.0
    max_turns: int = 10
    _turns: list[dict[str, Any]] = field(default_factory=list)
    _last_at: float = 0.0

    def _fresh(self) -> bool:
        return bool(self._turns) and (time.monotonic() - self._last_at) < self.ttl

    def expired(self) -> bool:
        return bool(self._turns) and not self._fresh()

    def history(self) -> list[dict[str, str]]:
        if not self._fresh():
            self._turns.clear()
            return []
        return [{"role": t["role"], "content": t["content"]} for t in self._turns]

    def append(self, role: str, content: str) -> None:
        if not content:
            return
        now = time.monotonic()
        if not self._fresh():
            self._turns.clear()
        self._turns.append({"role": role, "content": content, "at": now})
        self._last_at = now
        if len(self._turns) > self.max_turns:
            del self._turns[: len(self._turns) - self.max_turns]

    def clear(self) -> None:
        self._turns.clear()
        self._last_at = 0.0

    def drain(self) -> list[dict[str, Any]]:
        """Hand the finished thread over to permanent memory, then forget it."""
        turns = list(self._turns)
        self.clear()
        return turns

    def active(self) -> bool:
        return self._fresh()


@dataclass
class ScheduleItem:
    id: str
    kind: str          # reminder | alarm | routine
    due_at: float      # unix timestamp
    label: str
    announce: str
    action: str = ""   # "on" | "off" for scheduled device routines
    target: str = ""   # spoken device name, e.g. "kitchen light"
    repeat: str = ""  # daily | weekdays | weekends | weekly
    repeat_hour: int = 0
    repeat_minute: int = 0


def _parse_recurrence(text: str) -> tuple[str, int, int, str]:
    """Return (repeat, hour, minute, text_without_recurrence_words)."""
    low = normalise(text)
    m = re.search(r"\bevery\s+(morning|evening|night|day|weekday|weekend|week)\b", low)
    if not m:
        return "", 0, 0, text
    word = m.group(1)
    repeat = {"morning": "daily", "day": "daily",
              "week": "weekly", "weekday": "weekdays",
              "weekend": "weekends", "evening": "daily",
              "night": "daily"}[word]
    hour, minute = 8, 0
    if word in ("evening", "night"):
        hour = 20
    clock = re.search(r"\b(?:at\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\b", low)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2) or 0)
        amp = clock.group(3)
        if amp == "pm" and hour < 12:
            hour += 12
        elif amp == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            hour, minute = 8, 0
    cleaned = re.sub(r"\bevery\s+(?:morning|evening|night|day|weekday|weekend|week)\b",
                     " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:at|around|by|alle|al)\s+\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?\b",
                     " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;")
    return repeat, hour, minute, cleaned


def _next_repeat(item: "ScheduleItem", now: dt.datetime) -> Optional[float]:
    """Next occurrence timestamp for a recurring reminder/alarm."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=LOCAL_TZ)
    candidate = now.replace(hour=item.repeat_hour, minute=item.repeat_minute,
                            second=0, microsecond=0)
    for _ in range(370):
        day = candidate.weekday()
        ok = (item.repeat == "daily" or
              (item.repeat == "weekly" and day == 0) or
              (item.repeat == "weekdays" and day < 5) or
              (item.repeat == "weekends" and day >= 5))
        if ok and candidate > now:
            return candidate.timestamp()
        candidate += dt.timedelta(days=1)
    return None


class Scheduler:
    """Reminders and device routines that survive a hub restart."""

    def __init__(self, speech: "Speech", path: str = SCHEDULE_FILE,
                 action_fn=None) -> None:
        self.speech = speech
        self.path = path
        self.action_fn = action_fn
        self.items: dict[str, ScheduleItem] = {}
        self._task: Optional[asyncio.Task] = None
        self.load()
        self._drop_expired()

    def load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8-sig") as handle:
                rows = json.load(handle)
        except Exception as exc:
            log.warning("schedule load failed: %s", exc)
            return
        for row in rows if isinstance(rows, list) else []:
            try:
                item = ScheduleItem(str(row["id"]), str(row["kind"]), float(row["due_at"]),
                                    str(row.get("label", "")), str(row.get("announce", "")),
                                    str(row.get("action", "")), str(row.get("target", "")),
                                    str(row.get("repeat", "")),
                                    int(row.get("repeat_hour", 0) or 0),
                                    int(row.get("repeat_minute", 0) or 0))
            except (KeyError, TypeError, ValueError):
                continue
            self.items[item.id] = item

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump([item.__dict__ for item in self.items.values()], handle, indent=2)
        except Exception as exc:
            log.warning("schedule save failed: %s", exc)

    def _drop_expired(self) -> None:
        """Anything more than a minute late when the hub starts is stale."""
        cutoff = time.time() - 60
        stale = [k for k, item in self.items.items()
                 if item.due_at <= cutoff and item.kind != "alarm"]
        for key in stale:
            self.items.pop(key, None)
        if stale:
            self._save()

    def list(self, kind: Optional[str] = None) -> list[ScheduleItem]:
        return sorted((i for i in self.items.values() if kind is None or i.kind == kind),
                      key=lambda i: i.due_at)

    async def _loop(self) -> None:
        while True:
            try:
                now = time.time()
                for item in [i for i in self.items.values() if i.due_at <= now]:
                    self.items.pop(item.id, None)
                    if item.repeat and item.kind in ("reminder", "alarm"):
                        next_due = _next_repeat(item, dt.datetime.now(LOCAL_TZ))
                        if next_due is not None:
                            item.due_at = next_due.timestamp()
                            self.items[item.id] = item
                    self._save()
                    log.info("scheduler firing %s %r", item.kind, item.announce)
                    if item.announce:
                        asyncio.create_task(self.speech.say(item.announce))
                    if item.action and self.action_fn:
                        asyncio.create_task(self._safe_action(item))
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("scheduler: %s", exc)
                await asyncio.sleep(2)

    async def _safe_action(self, item: ScheduleItem) -> None:
        try:
            assert self.action_fn is not None
            await self.action_fn(item)
        except Exception as exc:
            log.warning("scheduled action %s failed: %s", item.id, exc)

    async def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(Exception):
                await self._task
            self._task = None

    async def add(self, kind: str, due_at: float, label: str, announce: str,
                  action: str = "", target: str = "", repeat: str = "",
                  repeat_hour: int = 0, repeat_minute: int = 0) -> ScheduleItem:
        item = ScheduleItem(uuid.uuid4().hex[:8], kind, due_at, label, announce,
                            action, target, repeat, repeat_hour, repeat_minute)
        self.items[item.id] = item
        self._save()
        return item

    async def cancel(self, kind: Optional[str] = None) -> int:
        targets = [key for key, item in self.items.items()
                   if kind is None or item.kind == kind]
        for key in targets:
            self.items.pop(key, None)
        if targets:
            self._save()
        return len(targets)


@dataclass
class Reply:
    text: str
    speak: str = "pipeline"  # pipeline | device | none
    intent: str = ""


@dataclass
class Context:
    settings: Settings
    device: LibreEcho
    speech: Speech
    ha: HomeAssistant
    weather: Weather
    knowledge: Knowledge
    http: aiohttp.ClientSession
    radio: RadioLibrary
    tuya: TuyaHome
    scheduler: Scheduler
    facts: PersonalFacts
    music_catalog: MusicCatalog
    music_assistant: MusicAssistant
    tv: TvControl
    memory: PermanentMemory
    iot: IotRegistry
    places: Optional["Places"] = None
    shopping: Optional["ShoppingList"] = None
    paused_station: Optional[str] = None  # live radio stopped by "pause"
    conversation: Conversation = field(default_factory=Conversation)
    pending: Optional[dict[str, Any]] = None  # clarification awaiting an answer
    follow_up_depth: int = 0  # consecutive empty/wake-only turns
    last_intent: str = ""     # intent of the last spoken turn (follow-up gate)
    follow_up_window: float = 0.0  # monotonic deadline of a hub-opened mic window
    recent_speech: list[tuple[float, str]] = field(default_factory=list)
    follow_up_task: Optional[asyncio.Task] = None
    listening_duck: Optional[tuple[str, int, int]] = None  # music ducked for this turn


Handler = Callable[[str, "re.Match[str] | None", Context], Awaitable[Reply]]


@dataclass
class Intent:
    name: str
    patterns: list[re.Pattern]
    handler: Handler
    examples: list[str] = field(default_factory=list)

    def match(self, text: str) -> Optional[re.Match]:
        for pattern in self.patterns:
            hit = pattern.search(text)
            if hit:
                return hit
        return None


def compile_all(*patterns: str) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def normalise(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .!?")


# Lightweight language hint for the LLM prompt.  The TTS router makes the
# final voice choice from the spoken text, so this only needs to be reliable
# enough to keep an Italian question from getting an English answer.
_ITALIAN_HINTS = {
    "ciao", "buongiorno", "buonasera", "grazie", "perché", "perche", "come",
    "stai", "sono", "sei", "questo", "quella", "oggi", "domani", "metti",
    "accendi", "spegni", "che", "ore", "della", "nel", "nella", "per", "non",
    "più", "piu", "musica", "radio", "tempo", "meteo", "italiano", "italiana",
    "mi", "ti", "una", "un", "di", "da", "voglio", "puoi", "per favore",
}


def correct_clone_phrase(text: str) -> str:
    """Map common ASR mishearings of 'Alexa clone' back to the command.

    The small Whisper model sometimes turns 'Alexa clone' into 'Alexa cold',
    'Alex Lacool' or 'Alex recalled' depending on the voice. Only a short,
    single-word command tail is rewritten, so 'it's cold' stays untouched.
    """
    raw = str(text or "").strip()
    if re.match(r"^(?:alexa|alex)\b", raw, re.IGNORECASE) and "clone" in raw.lower():
        return "alexa clone"
    hit = re.match(r"^(?:alexa|alex|hey\s+olive)\s+([A-Za-z'-]{2,12})[ .!?]*$",
                   raw, re.IGNORECASE)
    if not hit:
        return text
    tail = hit.group(1)
    if "clone" in tail.lower():
        return "alexa clone"
    for word in ("clone", "cloned", "cold", "clod", "clown", "cone", "clue",
                 "recalled", "record", "recorded", "colt", "lacool", "cloan"):
        if difflib.SequenceMatcher(None, tail.lower(), word).ratio() >= 0.6:
            log.info("ASR correction: %r -> 'alexa clone'", raw)
            return "alexa clone"
    return text


def correct_common_asr(text: str) -> str:
    """Fix recurring Whisper mishearings that change the meaning of a command.

    'alarm' is often transcribed as 'timer'. When the phrase contains a clock
    time or a day word it is an alarm, not a duration timer — and the speaker's
    own timer parser would otherwise answer "how long?" and never reach us.
    """
    out = str(text or "").strip()
    if re.search(r"\btimer\b", out, re.IGNORECASE) and re.search(
            r"\b(?:tomorrow|tonight|morning|evening|am|pm|domani|stasera|mattina|"
            r"at\s+\d|alle\s+\d|alle\s+\d{1,2})\b", out, re.IGNORECASE):
        fixed = re.sub(r"\btimer\b", "alarm", out, flags=re.IGNORECASE)
        log.info("ASR correction: %r -> %r", out, fixed)
        return fixed
    return out


def _looks_italian(text: str) -> bool:
    words = set(re.findall(r"[a-zàèìòù]+", str(text or "").lower()))
    score = len(words & _ITALIAN_HINTS)
    if any(ch in str(text) for ch in "àèìòù"):
        score += 2
    return score >= 2


# Used by the station-name fallback: a question must never be mistaken for
# "play that station".
QUESTION_START = re.compile(
    r"^(?:who|what|when|where|why|how|which|is|are|was|were|do|does|did|"
    r"can|could|would|will|should|tell me|chi|cosa|quando|dove|perche|perché)\b",
    re.IGNORECASE,
)

# Bare "stop" (including "Alexa stop" after the wake word) must first silence
# an alarm, an announcement or music. Only silence ends the conversation.
STOP_COMMAND = re.compile(
    r"^(?:(?:alexa|olive|hey olive|hey alexa)[, ]+)?"
    r"(?:please[, ]+)?"
    r"(?:stop(?:[ ,]+stop)*(?: (?:it|that|now|please|already))?"
    r"|stop (?:the )?(?:alarm|timer|ringing|beeping|noise|music|radio|song)"
    r"|shut up|be quiet|quiet|silence|basta|smettila|stai zitto|zitto)"
    r"[\s.!?]*$",
    re.IGNORECASE,
)


def looks_like_echo(text: str, spoken: str) -> bool:
    """True when a transcript is the speaker hearing its own announcement."""
    heard = normalise(text)
    said = normalise(spoken)
    if not heard or not said:
        return False
    if heard == said:
        return True
    if len(heard) < 12:
        return False
    if difflib.SequenceMatcher(None, heard, said).ratio() >= 0.8:
        return True
    tokens = [t for t in re.findall(r"[a-z0-9']+", heard) if len(t) > 2]
    if len(tokens) < 4 or len(tokens) > 20:
        # Short commands ("stop the music") share words with any reply but are
        # never an echo of it; the playback-state guard covers the rest.
        return False
    said_tokens = set(re.findall(r"[a-z0-9']+", said))
    hits = sum(1 for token in tokens if token in said_tokens)
    return hits / len(tokens) >= 0.6


# Whisper invents these lines when fed silence, fan noise or music. They are
# never real requests, and answering them breaks the follow-up flow.
NOISE_TRANSCRIPTS = re.compile(
    r"^\s*(?:that'?s (?:the )?(?:end of the sentence|system)|"
    r"(?:thank you|thanks) for watching|please (?:like and )?subscribe|"
    r"(?:like and )?(?:subscribe|share)(?: to (?:my|the) channel)?|"
    r"see you (?:next time|in the next video)|bye bye|the end)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def is_noise_transcript(text: str) -> bool:
    return bool(NOISE_TRANSCRIPTS.match(text or ""))


async def h_time(text: str, match, ctx: Context) -> Reply:
    now = dt.datetime.now(LOCAL_TZ)
    if _looks_italian(text):
        return Reply(f"Sono le {now.strftime('%H:%M')}.", intent="time")
    return Reply(f"It's {now.strftime('%H:%M')}.", intent="time")


_ITALIAN_DAYS = {
    "Monday": "lunedì", "Tuesday": "martedì", "Wednesday": "mercoledì",
    "Thursday": "giovedì", "Friday": "venerdì", "Saturday": "sabato",
    "Sunday": "domenica",
}
_ITALIAN_MONTHS = {
    "January": "gennaio", "February": "febbraio", "March": "marzo",
    "April": "aprile", "May": "maggio", "June": "giugno", "July": "luglio",
    "August": "agosto", "September": "settembre", "October": "ottobre",
    "November": "novembre", "December": "dicembre",
}


async def h_date(text: str, match, ctx: Context) -> Reply:
    today = dt.datetime.now(LOCAL_TZ)
    if _looks_italian(text):
        day = _ITALIAN_DAYS.get(today.strftime("%A"), today.strftime("%A"))
        month = _ITALIAN_MONTHS.get(today.strftime("%B"), today.strftime("%B"))
        return Reply(f"Oggi è {day}, {today.day} {month} {today.year}.", intent="date")
    return Reply(f"Today is {today.strftime('%A, %B %d, %Y')}.", intent="date")


CITY_TAIL = re.compile(
    r"\s+(?:for|di|per)\s+(?:today|tomorrow|domani|oggi|the weekend)\s*$", re.IGNORECASE
)
CITY_EXTRACT = re.compile(r"\b(?:in|a|at|di|for)\s+([\w' \-]+)$", re.IGNORECASE)


async def h_weather(text: str, match, ctx: Context) -> Reply:
    remaining = CITY_TAIL.sub("", normalise(text))
    city = None
    hit = CITY_EXTRACT.search(remaining)
    if hit:
        candidate = hit.group(1).strip()
        if candidate and candidate not in ("the", "here"):
            city = candidate
    italian = _looks_italian(text) or ctx.settings.language.startswith("it")
    return Reply(await ctx.weather.report(city, italian=italian), intent="weather")


MATH_WORDS = [
    (r"\bplus\b|\band\b", "+"),
    (r"\bminus\b", "-"),
    (r"\btimes\b|\bmultiplied by\b|\bx\b", "*"),
    (r"\bdivided by\b|\bover\b", "/"),
    (r"\bto the power of\b", "**"),
]


async def h_math(text: str, match, ctx: Context) -> Reply:
    cleaned = normalise(text)
    # "15 percent of 80" / "10% of 50" / "20 per cento di 200" must be handled
    # before the generic expression scanner, which would otherwise return the
    # first number (15) instead of the result (12).
    percent = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(?:%|per\s*cent(?:o)?)\s+(?:of|di)\s+(\d+(?:\.\d+)?)",
        cleaned, re.IGNORECASE,
    )
    if percent:
        value = float(percent.group(1)) / 100.0 * float(percent.group(2))
        pretty = int(value) if float(value).is_integer() else round(value, 4)
        return Reply(f"The answer is {pretty}.", intent="math")
    for pattern, symbol in MATH_WORDS:
        cleaned = re.sub(pattern, symbol, cleaned)
    expr = re.search(r"([0-9][0-9\s\.\+\-\*/%\(\)]*[0-9])", cleaned)
    if not expr:
        return Reply("I didn't catch the numbers.", intent="math")
    value = safe_math(expr.group(1).strip())
    if value is None:
        return Reply("I couldn't work that one out.", intent="math")
    pretty = int(value) if float(value).is_integer() else round(value, 4)
    return Reply(f"The answer is {pretty}.", intent="math")


async def h_coin(text: str, match, ctx: Context) -> Reply:
    return Reply(random.choice(["Heads!", "Tails!"]), intent="coin")


async def h_dice(text: str, match, ctx: Context) -> Reply:
    faces = 20 if "d20" in text else (6 if "dice" in text or "die" in text else 6)
    return Reply(f"You rolled a {random.randint(1, faces)}.", intent="dice")


GENERIC_MUSIC = {"music", "musica", "some music", "della musica", "the radio", "radio",
                 "la radio", "something", "qualcosa", "un po di musica"}
PLAY_VERB = re.compile(
    r"^(?:please\s+)?(?:play|put on|metti su|metti|riproduci|accendi|play some|play the)\s+(.+)$",
    re.IGNORECASE,
)


STATION_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "first": "1", "second": "2", "third": "3",
}
STATION_FILLER = {"radio", "fm", "the", "station", "live", "music"}


def station_key(value: str) -> str:
    """Phonetic-ish key so "BBC one extra" == "BBC Radio 1Xtra"."""
    text = normalise(value).replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = [STATION_NUMBER_WORDS.get(word, word) for word in text.split()]
    key = "".join(word for word in words if word not in STATION_FILLER)
    # Spoken "extra" ("BBC one extra") is the written "XTRA".
    return key.replace("extra", "xtra")


def station_keys(station: dict[str, Any]) -> set[str]:
    keys = {
        station_key(str(station.get("name") or "")),
        station_key(str(station.get("word") or "")),
    }
    keys.discard("")
    return keys


def find_station(stations: list[dict[str, Any]], needle: str) -> Optional[dict[str, Any]]:
    """Match a spoken station name against the device's stored stations."""
    wanted = normalise(needle)
    if not wanted:
        return None
    wanted_key = station_key(needle)
    if len(wanted_key) >= 3:
        for station in stations:
            if wanted_key in station_keys(station):
                return station
        best: Optional[dict[str, Any]] = None
        best_len = 0
        for station in stations:
            for key in station_keys(station):
                if len(key) >= 5 and (key in wanted_key or wanted_key in key):
                    if len(key) > best_len:
                        best, best_len = station, len(key)
        if best is not None:
            return best
    for station in stations:
        haystack = normalise(f"{station.get('name','')} {station.get('word','')}")
        if haystack and (wanted in haystack or haystack in wanted):
            return station
    tokens = [word for word in wanted.split() if len(word) > 2]
    if tokens:
        for station in stations:
            haystack = normalise(f"{station.get('name','')} {station.get('word','')}")
            if haystack and all(token in haystack for token in tokens):
                return station
    return None


def station_in_text(stations: list[dict[str, Any]], text: str) -> Optional[dict[str, Any]]:
    """A short utterance that is really a station name ("and BBC one extra")."""
    clean = normalise(text)
    if not clean or "?" in text or len(clean.split()) > 8:
        return None
    if QUESTION_START.match(clean):
        return None
    key = station_key(text)
    if len(key) < 5:
        return None
    best: Optional[dict[str, Any]] = None
    best_len = 0
    for station in stations:
        for candidate in station_keys(station):
            if len(candidate) >= 5 and (candidate in key or key in candidate):
                if len(candidate) > best_len:
                    best, best_len = station, len(candidate)
    return best


def clean_station_name(value: str) -> str:
    """Drop a leading article so "metti la radio" resolves like "radio"."""
    value = normalise(value)
    return re.sub(r"^(?:the|la|il|lo|l'|le|gli|i|a)\s+", "", value).strip()


MUSIC_QUESTION = "Which kind of music would you like?"


MA_HINT = re.compile(
    r"\b(?:music\s+assistant|musicassistant|multi-?room|whole\s+house|"
    r"everywhere|tutta\s+la\s+casa|in\s+ogni\s+stanza)\b",
    re.IGNORECASE,
)

_BACKGROUND: set[asyncio.Task] = set()
_SCAN_STATE: dict[str, Any] = {"running": False, "last": 0.0, "new": 0, "error": ""}
_SCAN_TASK: Optional[asyncio.Task] = None
# Set by "clone" so the next utterance's Wyoming audio is saved as a reference.
_VOICE_CAPTURE: dict[str, Any] = {"active": False, "chunks": [], "rate": 16000,
                                   "width": 2, "channels": 1, "path": ""}


def save_voice_capture() -> str:
    """Write the captured Wyoming audio to a WAV file for a voice reference."""
    cap = _VOICE_CAPTURE
    if not cap.get("chunks"):
        return ""
    root = os.path.dirname(os.path.abspath(__file__))
    refs = os.path.join(root, "voice_refs")
    os.makedirs(refs, exist_ok=True)
    path = os.path.join(refs, "capture.wav")
    try:
        with wave.open(path, "wb") as handle:
            handle.setnchannels(int(cap.get("channels") or 1))
            handle.setsampwidth(int(cap.get("width") or 2))
            handle.setframerate(int(cap.get("rate") or 16000))
            handle.writeframes(b"".join(cap["chunks"]))
        cap["path"] = path
        cap["chunks"] = []
        log.info("voice reference captured: %s", path)
        return path
    except Exception as exc:
        log.warning("voice capture save failed: %s", exc)
        cap["chunks"] = []
        return ""


def background(coro: Awaitable[Any]) -> asyncio.Task:
    """Run a slow side effect without delaying the spoken reply."""
    task = asyncio.ensure_future(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


async def h_radio(text: str, match, ctx: Context) -> Reply:
    wanted = (match.group("station") if match and "station" in match.groupdict() else "") or ""
    if not wanted:
        verb = PLAY_VERB.match(text.strip())
        if verb:
            wanted = verb.group(1)
        else:
            # A bare station name ("and BBC one extra" after a question).
            wanted = re.sub(r"^(?:and|also|now|ok|okay|please|then)\b[\s,]*",
                            " ", normalise(text)).strip(" ,.")
    # Strip the Music Assistant hint so "play jazz on music assistant" asks MA
    # for "jazz", not for "jazz on music assistant".
    wanted = MA_HINT.sub(" ", wanted)
    wanted = re.sub(r"\b(?:on|in|through|via|su|sul|sulla)\s*$", " ", wanted, flags=re.IGNORECASE)
    wanted = re.sub(r"\s+", " ", wanted).strip(" ,.")
    # "play music" without a genre/station: ask which kind and remember the
    # question so the next turn ("hiphop") can answer it directly.
    if normalise(wanted) in {"music", "musica", "some music", "della musica",
                             "qualcosa", "something", "un po di musica",
                             "a bit of music", "any music"}:
        ctx.pending = {"kind": "music", "prompt": MUSIC_QUESTION,
                       "expires": time.monotonic() + ctx.settings.pending_ttl}
        return Reply(MUSIC_QUESTION, intent="music.mood")
    # Device-native radio is the reliable path. Music Assistant is used when
    # asked for explicitly, when a *specific* station is named (so the stream
    # can be routed/multi-room), or as a fallback when the device has no such
    # station. A named station is resolved once so both paths use the same URL.
    named = None
    if wanted:
        try:
            named = find_station(await ctx.radio.all(), wanted)
        except Exception as exc:
            log.debug("station resolve failed: %s", exc)
    via_ma = bool(MA_HINT.search(text)) or bool(
        named is not None and ctx.settings.radio_prefer_ma
    )
    if ctx.music_assistant.enabled:
        candidate = named or (await ctx.radio.best(wanted) if wanted else None)
        if candidate and (via_ma or not await ctx.radio.find_device_station(wanted)):
            name = str(candidate.get("name") or candidate.get("word"))
            stream_url = await ctx.radio.ma_url(candidate)
            accepted = await ctx.music_assistant.play_url(stream_url)
            # MA can queue the item and only start streaming after our request
            # timed out, so check the speaker before declaring failure.
            if await ctx.music_assistant.verify_streaming(ctx.device):
                return Reply(f"Playing {name} through Music Assistant.", intent="radio")
            # The speaker's Sendspin client sometimes stops accepting audio
            # until it is rebooted. Never leave the room in silence.
            background(ctx.music_assistant.stop())
            log.warning("MA %s for %r but the speaker never streamed; "
                        "falling back to native playback",
                        "accepted" if accepted else "did not answer", name)
            station = await ctx.radio.play(wanted or name)
            if station is not None:
                lead = ("Music Assistant stayed silent, so it's on the speaker now. "
                        if via_ma else "")
                return Reply(f"{lead}Playing {station.get('name') or station.get('word')}.",
                             intent="radio")
            return Reply("Music Assistant didn't start on the speaker.", intent="radio")
        if via_ma:
            return Reply("I couldn't find that station for Music Assistant.", intent="radio")
    elif via_ma:
        return Reply("Music Assistant isn't enabled.", intent="radio")
    if ctx.music_assistant.enabled:
        background(ctx.music_assistant.stop())
    station = await ctx.radio.play(wanted)
    if station is None:
        return Reply("I couldn't find a station for that.", intent="radio")
    return Reply(f"Playing {station.get('name') or station.get('word')}.", intent="radio")


async def h_playlist(text: str, match, ctx: Context) -> Reply:
    query = ""
    if match and "query" in match.groupdict():
        query = (match.group("query") or "").strip()
    if not query:
        query = re.sub(
            r"\b(?:make|create|build|start|fammi|crea|prepara)\b|\bplaylist\b|\b(?:for|of|di|per|da)\b",
            " ", text,
        )
    if ctx.music_assistant.enabled:
        background(ctx.music_assistant.stop())
    picked = await ctx.radio.playlist(query)
    if not picked:
        return Reply("I couldn't build a playlist for that.", intent="music.playlist")
    names = ", ".join((s.get("name") or s.get("word") or "?") for s in picked)
    return Reply(
        f"Playlist ready: {names}. Playing {picked[0].get('name') or picked[0].get('word')}.",
        intent="music.playlist",
    )


async def h_next(text: str, match, ctx: Context) -> Reply:
    if ctx.music_assistant.enabled:
        background(ctx.music_assistant.stop())
    station = await ctx.radio.next()
    if station is None:
        return Reply("There's nothing to skip to.", intent="music.next")
    return Reply(f"Playing {station.get('name') or station.get('word')}.", intent="music.next")


async def h_stop_music(text: str, match, ctx: Context) -> Reply:
    # 409 "nothing is playing that LibreEcho can stop" is not an error for us.
    # Never stop the device transport while Sendspin is streaming: the speaker's
    # Sendspin client stays stuck until it is rebooted.
    ma_playing = await ctx.music_assistant.playing()
    if ma_playing:
        background(ctx.music_assistant.stop())
    else:
        with contextlib.suppress(Exception):
            await ctx.device.transport("stop")
        with contextlib.suppress(Exception):
            await ctx.device.stop_radio()
    ctx.paused_station = None
    return Reply("Stopped.", intent="music.stop")


async def h_speaker_reboot(text: str, match, ctx: Context) -> Reply:
    """Reboot the speaker: the only reliable fix for a stuck Sendspin client."""
    try:
        await ctx.device.call("POST", "/system/reboot", confirm=True, timeout=10)
    except Exception as exc:
        log.warning("speaker reboot failed: %r", exc)
        return Reply("I couldn't reboot the speaker.", intent="speaker.reboot")
    return Reply("Rebooting the speaker. I'll be back in about a minute.",
                 intent="speaker.reboot")


async def h_pause_music(text: str, match, ctx: Context) -> Reply:
    if await ctx.music_assistant.playing():
        # Live streams cannot pause; stopping MA avoids wedging the device.
        background(ctx.music_assistant.stop())
        return Reply("Paused the music on Music Assistant.", intent="music.pause")
    with contextlib.suppress(Exception):
        await ctx.device.transport("pause")
    playback = {}
    with contextlib.suppress(Exception):
        playback = await ctx.device.playback()
    if str(playback.get("state") or "").lower() == "playing":
        # Live radio cannot pause: stop it and remember where we were.
        word = await ctx.device.current_station_word()
        if word:
            with contextlib.suppress(Exception):
                await ctx.device.stop_radio()
            ctx.paused_station = word
            return Reply("Paused the radio. Say resume to pick it up again.", intent="music.pause")
        return Reply("This stream can only be stopped, not paused.", intent="music.pause")
    return Reply("Paused.", intent="music.pause")


async def h_resume_music(text: str, match, ctx: Context) -> Reply:
    resumed = False
    with contextlib.suppress(Exception):
        await ctx.device.transport("play")
        playback = await ctx.device.playback()
        resumed = str(playback.get("state") or "").lower() in ("playing", "announcing")
    if not resumed and ctx.paused_station:
        word = ctx.paused_station
        ctx.paused_station = None
        await ctx.device.play_radio(word)
        return Reply("Back on.", intent="music.play")
    return Reply("Resuming.", intent="music.play")


async def h_now_playing(text: str, match, ctx: Context) -> Reply:
    state = await ctx.device.playback()
    if str(state.get("state") or "").lower() != "playing":
        return Reply("Nothing is playing right now.", intent="music.status")
    meta = state.get("metadata") or {}
    title = meta.get("title") or state.get("title") or state.get("media_title")
    artist = meta.get("artist") or state.get("artist") or state.get("media_artist")
    station = meta.get("station") or state.get("station")
    if not title:
        if station:
            return Reply(f"Playing {station}.", intent="music.status")
        return Reply("Something is playing, but I can't read the details.", intent="music.status")
    if artist:
        return Reply(f"Playing {title} by {artist}.", intent="music.status")
    if station and station not in title:
        return Reply(f"Playing {title} on {station}.", intent="music.status")
    return Reply(f"Playing {title}.", intent="music.status")


async def h_volume(text: str, match, ctx: Context) -> Reply:
    groups = match.groupdict() if match else {}
    direction = (groups.get("direction") or "").lower()
    if not direction:
        if re.search(r"\b(up|raise|increase|louder|alza|alzare|su|piu forte|più forte)\b", text):
            direction = "up"
        elif re.search(r"\b(down|lower|decrease|quieter|softer|abbassa|abbassare|giu|giù|piu piano|più piano)\b", text):
            direction = "down"
    if direction:
        current = await ctx.device.current_volume()
        current = 30 if current is None else current
        step = int(groups.get("step") or groups.get("level") or 10)
        level = current + step if direction in ("up", "su") else current - step
    elif groups.get("level"):
        level = int(groups["level"])
    else:
        volume = await ctx.device.current_volume()
        return Reply(f"Volume is {volume}." if volume is not None else "I can't read the volume.", intent="volume.get")
    level = max(0, min(100, level))
    await ctx.device.set_volume(level)
    return Reply(f"Volume {level}.", intent="volume.set")


TIMER_UNITS = {"second": 1, "seconds": 1, "sec": 1, "secondi": 1, "secondo": 1,
               "minute": 60, "minutes": 60, "min": 60, "mins": 60, "minuti": 60, "minuto": 60,
               "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
               "ora": 3600, "ore": 3600}

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "uno": 1, "una": 1, "un": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
    "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "quindici": 15,
    "venti": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50,
    "a": 1, "an": 1,
}
NUMBER_PATTERN = r"\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
TIMER_UNIT_PATTERN = "|".join(sorted(TIMER_UNITS, key=len, reverse=True))


def parse_amount(raw: str | None) -> int:
    if not raw:
        return 0
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    return NUMBER_WORDS.get(raw, 0)


async def h_timer_set(text: str, match, ctx: Context) -> Reply:
    groups = match.groupdict() if match else {}
    unit = (groups.get("unit") or "minutes").lower()
    seconds = parse_amount(groups.get("amount")) * TIMER_UNITS.get(unit, 60)
    if seconds <= 0:
        seconds = parse_duration(text) or 0
    if seconds <= 0:
        ctx.pending = {"kind": "timer", "expires": time.monotonic() + ctx.settings.pending_ttl}
        return Reply("How long should the timer be?", intent="timer.set")
    label = (groups.get("label") or pretty_duration(seconds)).strip()
    await ctx.device.add_timer(seconds, label)
    return Reply(f"Timer set for {pretty_duration(seconds)}.", intent="timer.set")


async def h_timer_list(text: str, match, ctx: Context) -> Reply:
    timers = await ctx.device.list_timers()
    if not timers:
        return Reply("No timers running.", intent="timer.list")
    parts = []
    for timer in timers:
        remaining = (timer.get("remaining_seconds") or timer.get("remaining")
                     or timer.get("seconds_left") or timer.get("seconds"))
        label = timer.get("label") or timer.get("name") or "timer"
        if isinstance(remaining, (int, float)) and remaining > 0:
            minutes = int(remaining) // 60
            parts.append(f"{label} with {minutes} minutes left" if minutes >= 1
                         else f"{label} with {int(remaining)} seconds left")
        else:
            parts.append(str(label))
    return Reply("You have " + ", and ".join(parts) + ".", intent="timer.list")


async def h_timer_cancel(text: str, match, ctx: Context) -> Reply:
    timers = await ctx.device.list_timers()
    if not timers:
        return Reply("There's no timer to cancel.", intent="timer.cancel")
    cancelled = 0
    ringing = False
    for timer in timers:
        state = str(timer.get("state") or "").lower()
        remaining = timer.get("seconds_remaining")
        if state == "ringing" or (isinstance(remaining, (int, float)) and remaining <= 0):
            ringing = True
            continue
        timer_id = timer.get("id") or timer.get("timer_id") or timer.get("uuid")
        if timer_id is None:
            continue
        try:
            await ctx.device.cancel_timer(timer_id)
            cancelled += 1
        except Exception as exc:
            # A timer that just fired is no longer cancellable by id.
            log.debug("cancel timer %s failed: %s", timer_id, exc)
            ringing = True
    if ringing:
        try:
            await ctx.device.dismiss_timers()
            cancelled += 1
        except Exception as exc:
            log.debug("dismiss ringing timers failed: %s", exc)
    if not cancelled:
        return Reply("I couldn't find a timer to cancel.", intent="timer.cancel")
    return Reply(f"Cancelled {cancelled} timer{'s' if cancelled != 1 else ''}.",
                 intent="timer.cancel")


def timer_is_ringing(timer: dict[str, Any]) -> bool:
    state = str(timer.get("state") or "").lower()
    remaining = timer.get("seconds_remaining")
    return state in ("ringing", "fired", "alarm") or (
        isinstance(remaining, (int, float)) and remaining <= 0)


async def ringing_timers(ctx: Context) -> list[dict[str, Any]]:
    try:
        timers = await ctx.device.list_timers()
    except Exception as exc:
        log.debug("timer read failed: %s", exc)
        return []
    return [timer for timer in timers if timer_is_ringing(timer)]


async def stop_everything(ctx: Context) -> Optional[str]:
    """Stop an alarm, announcement or playback.

    Returns the line to speak, or None when nothing was active so the caller
    can treat a bare "stop" as "end the conversation" instead.
    """
    if await ringing_timers(ctx):
        try:
            await ctx.device.dismiss_timers()
            return "Stopped the alarm."
        except Exception as exc:
            log.warning("dismiss timers failed: %s", exc)

    try:
        buses = (await ctx.device.playback()).get("buses") or {}
        if buses.get("alarm"):
            await ctx.device.dismiss_timers()
            return "Stopped the alarm."
        if buses.get("announcement"):
            await ctx.device.stop_announce()
            return "Stopped."
    except Exception:
        pass

    try:
        if await ctx.music_assistant.playing():
            await ctx.music_assistant.stop()
            return "Stopped the music."
    except Exception:
        pass

    try:
        if await ctx.device.is_playing():
            await ctx.device.stop_radio()
            return "Stopped."
    except Exception:
        pass
    return None


async def h_stop_everything(text: str, match, ctx: Context) -> Reply:
    ctx.pending = None
    said = await stop_everything(ctx)
    return Reply(said or "There's nothing to stop.", intent="stop")


async def home_toggle(ctx: Context, target: str, on: bool) -> Reply:
    """Turn a device on/off through whichever home backend is configured."""
    if ctx.ha.enabled:
        entity = await ctx.ha.resolve(target)
        if not entity:
            return Reply(f"I couldn't find {target or 'that device'}.", intent="ha.toggle")
        domain = str(entity.get("entity_id", "")).split(".")[0]
        await ctx.ha.call(domain, "turn_on" if on else "turn_off", {"entity_id": entity["entity_id"]})
        name = (entity.get("attributes") or {}).get("friendly_name") or entity["entity_id"]
        return Reply(f"{'Turning on' if on else 'Turning off'} {name}.", intent="ha.toggle")
    if ctx.iot.enabled and ctx.iot.ready:
        device = ctx.iot.resolve(target)
        if device:
            name = device.get("name") or device.get("ip")
            if await ctx.iot.set_power(device, on):
                return Reply(f"{'Turning on' if on else 'Turning off'} {name}.",
                             intent="iot.toggle")
            return Reply(f"I couldn't reach {name} on the local network.",
                         intent="iot.toggle")
    if ctx.tuya.enabled:
        device = await ctx.tuya.resolve(target)
        if not device:
            if getattr(ctx.tuya, "last_error", ""):
                return Reply(
                    "The Tuya cloud isn't available. Add the lights' local keys "
                    "to iot_keys.json for offline control.",
                    intent="tuya.toggle",
                )
            return Reply(
                f"I couldn't find {target or 'that device'}. Add its local key "
                "to iot_keys.json to control it offline.",
                intent="tuya.toggle",
            )
        name = device.get("name") or "the device"
        if not await ctx.tuya.set_power(device, on):
            return Reply(
                f"I couldn't switch {name} {'on' if on else 'off'} through Tuya.",
                intent="tuya.toggle",
            )
        return Reply(f"{'Turning on' if on else 'Turning off'} {name}.", intent="tuya.toggle")
    return Reply(
        "No home backend is configured yet. Add local keys to iot_keys.json, "
        "or Home Assistant / Tuya credentials.",
        intent="home.toggle",
    )


async def h_ha_on(text: str, match, ctx: Context) -> Reply:
    target = (match.group("target") if match and "target" in match.groupdict() else "").strip()
    return await home_toggle(ctx, target, True)


async def h_ha_off(text: str, match, ctx: Context) -> Reply:
    target = (match.group("target") if match and "target" in match.groupdict() else "").strip()
    return await home_toggle(ctx, target, False)


async def h_light_brightness(text: str, match, ctx: Context) -> Reply:
    groups = match.groupdict() if match else {}
    target = (groups.get("target") or "").strip()
    level = groups.get("level")
    if not (ctx.iot.enabled and ctx.iot.ready):
        return Reply("I don't have any local lights configured yet.", intent="iot.brightness")
    device = ctx.iot.resolve(target)
    if not device:
        return Reply(f"I couldn't find {target or 'that light'}.", intent="iot.brightness")
    name = device.get("name") or device.get("ip")
    if level:
        percent = int(level)
    else:
        percent = 20 if re.search(r"\bdim\b|\babassa\b", text.lower()) else 90
    percent = max(1, min(100, percent))
    if await ctx.iot.set_brightness(device, percent):
        return Reply(f"Setting {name} to {percent} percent.", intent="iot.brightness")
    return Reply(f"I couldn't reach {name} on the local network.", intent="iot.brightness")


async def h_ha_state(text: str, match, ctx: Context) -> Reply:
    target = (match.group("target") if match and "target" in match.groupdict() else "").strip()
    if ctx.ha.enabled:
        entity = await ctx.ha.resolve(target)
        if not entity:
            return Reply(f"I couldn't find {target or 'that device'}.", intent="ha.state")
        name = (entity.get("attributes") or {}).get("friendly_name") or entity["entity_id"]
        return Reply(f"{name} is {entity.get('state')}.", intent="ha.state")
    if ctx.iot.enabled and ctx.iot.ready:
        device = ctx.iot.resolve(target)
        if device:
            name = device.get("name") or device.get("ip")
            on = await ctx.iot.is_on(device)
            if on is None:
                return Reply(f"I can't read {name}'s state.", intent="iot.state")
            return Reply(f"{name} is {'on' if on else 'off'}.", intent="iot.state")
    if ctx.tuya.enabled:
        device = await ctx.tuya.resolve(target)
        if not device:
            if getattr(ctx.tuya, "last_error", ""):
                return Reply(
                    "The Tuya cloud isn't available. Add the lights' local keys "
                    "to iot_keys.json for offline control.",
                    intent="tuya.state",
                )
            return Reply(
                f"I couldn't find {target or 'that device'}. Add its local key "
                "to iot_keys.json to control it offline.",
                intent="tuya.state",
            )
        on = await ctx.tuya.is_on(device)
        name = device.get("name") or "that device"
        if on is None:
            return Reply(f"I can't read {name}'s state.", intent="tuya.state")
        return Reply(f"{name} is {'on' if on else 'off'}.", intent="tuya.state")
    return Reply(
        "No home backend is configured yet. Add local keys to iot_keys.json, "
        "or Home Assistant / Tuya credentials.",
        intent="home.state",
    )


async def h_ha_scene(text: str, match, ctx: Context) -> Reply:
    if not ctx.ha.enabled:
        return Reply("Home Assistant isn't configured yet.", intent="ha.scene")
    target = (match.group("target") if match and "target" in match.groupdict() else "").strip()
    entity = await ctx.ha.resolve(target)
    if entity and str(entity.get("entity_id", "")).startswith("scene."):
        await ctx.ha.call("scene", "turn_on", {"entity_id": entity["entity_id"]})
        name = (entity.get("attributes") or {}).get("friendly_name") or entity["entity_id"]
        return Reply(f"Activating {name}.", intent="ha.scene")
    return Reply(f"I couldn't find a scene called {target or 'that'}.", intent="ha.scene")


async def h_knowledge(text: str, match, ctx: Context) -> Reply:
    answer = await ctx.knowledge.lookup(text)
    if not answer:
        return Reply("I couldn't find an answer to that.", intent="knowledge")
    short = " ".join(re.split(r"(?<=[.!?])\s+", answer)[:2])
    return Reply(short, intent="knowledge")


DURATION_RE = re.compile(
    r"\b(?P<amount>\d+|" + NUMBER_PATTERN + r")\s*(?P<unit>" + TIMER_UNIT_PATTERN + r")\b",
    re.IGNORECASE,
)


def parse_duration(text: str) -> Optional[int]:
    """'4 mins', 'in twenty minutes', 'for an hour' -> seconds."""
    hit = DURATION_RE.search(normalise(text))
    if not hit:
        return None
    seconds = parse_amount(hit.group("amount")) * TIMER_UNITS.get(hit.group("unit").lower(), 60)
    return seconds or None


def pretty_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    if seconds < 3600:
        minutes, rest = divmod(seconds, 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}" + (
            f" {rest} seconds" if rest else "")
    hours, rest = divmod(seconds, 3600)
    return f"{hours} hour{'s' if hours != 1 else ''}" + (
        f" {rest // 60} minutes" if rest >= 60 else "")


def parse_clock_target(text: str, allow_bare: bool = False) -> Optional[dt.datetime]:
    """'at 7', '7:30 am', '19.45' -> the next matching local datetime.

    A bare number only counts when `allow_bare` is set (a direct answer such as
    "7" to "Alarm for what time?"), so "remind me to buy 2 apples" stays safe.
    """
    cleaned = normalise(text).replace("a.m.", "am").replace("p.m.", "pm")
    hit = re.search(
        r"(?P<prefix>\b(?:at|around|by|for|alle|al)\s+)?(?P<hour>\d{1,2})"
        r"(?:[:.](?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?\b",
        cleaned,
    )
    if hit is None:
        if allow_bare and re.fullmatch(r"\d{1,2}", cleaned):
            hour, minute, ampm = int(cleaned), 0, ""
        else:
            return None
    else:
        hour = int(hit.group("hour"))
        minute = int(hit.group("minute") or 0)
        ampm = hit.group("ampm") or ""
        if not (hit.group("prefix") or hit.group("minute") or ampm or allow_bare):
            return None
    if minute > 59 or hour > 24:
        return None
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23:
        return None
    now = dt.datetime.now(LOCAL_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return target


def parse_when(text: str, allow_bare: bool = False) -> Optional[dt.datetime]:
    """Parse a spoken time, honouring tomorrow / tonight / morning / night.

    parse_clock_target only handles the clock and rolls past times forward one
    day; it ignores words like 'tomorrow' and 'night'.  Alarms and reminders
    need those words to choose the right day and am/pm.
    """
    seconds = parse_duration(text)
    if seconds:
        return dt.datetime.now(LOCAL_TZ) + dt.timedelta(seconds=seconds)
    target = parse_clock_target(text, allow_bare=allow_bare)
    if target is None:
        return None
    low = normalise(text)
    night = bool(re.search(r"\b(?:night|evening|tonight|stasera|notte|sera)\b", low))
    morning = bool(re.search(r"\b(?:morning|mattina|am)\b", low))
    if re.search(r"\b(?:tonight|stasera|this evening)\b", low):
        target = dt.datetime.combine(dt.date.today(), target.time(), LOCAL_TZ)
        if night and not morning and target.hour < 12:
            target = target.replace(hour=target.hour + 12)
    elif re.search(r"\b(?:tomorrow|domani)\b", low):
        target = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1),
                                     target.time(), LOCAL_TZ)
        if night and not morning and target.hour < 12:
            target = target.replace(hour=target.hour + 12)
    else:
        if night and not morning and target.hour < 12:
            target = target.replace(hour=target.hour + 12)
        if morning and target.hour == 12:
            target = target.replace(hour=0)
    return target


def parse_event_when(text: str) -> tuple[Optional[dt.datetime], int]:
    """Resolve 'tomorrow at 9' / 'in 20 minutes' for calendar events."""
    seconds = parse_duration(text)
    if seconds:
        return dt.datetime.now(LOCAL_TZ) + dt.timedelta(seconds=seconds), seconds
    target = parse_clock_target(text)
    if target is None:
        return None, 0
    if re.search(r"\b(?:tomorrow|domani)\b", text, re.IGNORECASE):
        # parse_clock_target already rolls a past time forward a day; setting
        # the date explicitly avoids rolling twice.
        target = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1),
                                     target.time(), LOCAL_TZ)
    return target, 0


async def h_alarm_set(text: str, match, ctx: Context) -> Reply:
    target = parse_when(text, allow_bare=True)
    if target is not None and target.tzinfo is None:
        target = target.replace(tzinfo=LOCAL_TZ)
    if target is None:
        ctx.pending = {"kind": "alarm", "expires": time.monotonic() + ctx.settings.pending_ttl}
        return Reply("Alarm for what time?", intent="alarm.set")
    wait = int((target - dt.datetime.now(LOCAL_TZ)).total_seconds())
    if wait <= 0:
        wait += 86400
    label = f"alarm {target:%Y-%m-%d %H:%M}"
    # The device timer is the reliable ringer; the hub keeps a record only.
    await ctx.device.add_timer(wait, label)
    await ctx.scheduler.add("alarm", target.timestamp(), label, "")
    pretty = target.strftime("%H:%M") if target.date() == dt.date.today() else target.strftime("%A %H:%M")
    return Reply(f"Alarm set for {pretty}.", intent="alarm.set")


def _reminder_what(text: str) -> str:
    """Extract the task text from a reminder request, dropping time words."""
    cleaned = str(text or "")
    cleaned = re.sub(r"^(?:alexa[,\s]+)?(?:please\s+)?", "", cleaned,
                     flags=re.IGNORECASE)
    # Command prefixes (English + Italian).
    cleaned = re.sub(
        r"^\s*(?:remind me|ricordami|set|create|add|make|remember)\b"
        r"(?:\s+(?:an|a|my|the|up|me))?\s*"
        r"(?:reminder|promemoria)?\s*",
        "", cleaned, flags=re.IGNORECASE)
    # Time expressions.
    cleaned = re.sub(r"\bevery\s+(?:morning|evening|night|day|weekday|weekend|week)\b",
                     " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:in|for|after)\s+\d+\s*"
                     r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b",
                     " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:at|around|by|alle|al)\s+\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?\b",
                     " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:tomorrow|today|tonight|domani|oggi|stasera)\b",
                     " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bnext\s+(?:monday|tuesday|wednesday|thursday|friday|"
                     r"saturday|sunday)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bon\s+\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?[a-z]+\b",
                     " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    cleaned = re.sub(r"\b(?:in|at|on|by|for|around|about|to|that|about)\s*$",
                     "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^(?:to|that|about|di|che)\s+", "", cleaned,
                     flags=re.IGNORECASE).strip()
    return re.sub(r"\s+", " ", cleaned).strip(" ,.;:")


async def h_reminder_set(text: str, match, ctx: Context) -> Reply:
    what = _reminder_what(text)
    repeat, rep_hour, rep_min, cleaned = _parse_recurrence(what)
    what = cleaned or what
    if repeat:
        text = f"at {rep_hour:02d}:{rep_min:02d}"

    seconds = parse_duration(text)
    target: Optional[dt.datetime] = None
    if seconds:
        target = dt.datetime.now(LOCAL_TZ) + dt.timedelta(seconds=seconds)
    else:
        target = parse_when(text)
    if not what:
        return Reply("What should I remind you about?", intent="reminder.set")
    if not what:
        return Reply("What should I remind you about?", intent="reminder.set")
    if target is None:
        ctx.pending = {"kind": "reminder", "what": what,
                       "expires": time.monotonic() + ctx.settings.pending_ttl}
        return Reply("When should I remind you?", intent="reminder.set")

    if target.tzinfo is None:
        target = target.replace(tzinfo=LOCAL_TZ)
    await ctx.scheduler.add("reminder", target.timestamp(), f"reminder {what[:40]}",
                            f"Reminder: {what}.", repeat=repeat,
                            repeat_hour=rep_hour, repeat_minute=rep_min)
    when = target.strftime("%H:%M") if target.date() == dt.date.today() else target.strftime("%A %H:%M")
    suffix = f" ({repeat})" if repeat else ""
    return Reply(f"Reminder set for {when}{suffix}: {what}.", intent="reminder.set")


async def h_routine_sunset(text: str, match, ctx: Context) -> Reply:
    """Schedule a device action a few minutes before the next local sunset."""
    groups = match.groupdict() if match else {}
    target = (groups.get("target") or "").strip(" ,.")
    if not target:
        target = re.sub(r"^\s*(?:please\s+)?(?:turn|switch|accendi)\s+(?:on\s+)?", "", text,
                        flags=re.IGNORECASE)
        target = re.sub(r"\b(?:\d+|[a-z]+)\s*(?:minutes?|mins?|hours?|minuti|ore)\b.*$", "",
                        target, flags=re.IGNORECASE)
        target = re.sub(r"\b(?:before|prima)\b.*$", "", target, flags=re.IGNORECASE).strip(" ,.")
    device = ctx.iot.resolve(target) if ctx.iot else None
    if not device:
        return Reply(f"I couldn't find a device called {target or 'that'}.",
                     intent="routine.sunset")
    name = str(device.get("name") or target)
    seconds = parse_duration(text) or 240
    sunset = await ctx.weather.sunset()
    if sunset is None:
        return Reply("I can't get sunset times right now.", intent="routine.sunset")
    due = sunset - dt.timedelta(seconds=seconds)
    if due.timestamp() <= time.time():
        return Reply("That sunset time has already passed.", intent="routine.sunset")
    await ctx.scheduler.add(
        "routine", due.timestamp(), f"{name} on before sunset", "",
        action="on", target=name,
    )
    when = due.strftime("%H:%M") if due.date() == dt.date.today() else due.strftime("%A %H:%M")
    return Reply(f"Okay, I'll turn on {name} at {when}, "
                 f"{pretty_duration(seconds)} before sunset.", intent="routine.sunset")


async def h_schedule_list(text: str, match, ctx: Context) -> Reply:
    if re.search(r"\balarms?\b", text):
        items = ctx.scheduler.list("alarm")
        if not items:
            return Reply("No alarms are set.", intent="alarm.list")
        parts = [dt.datetime.fromtimestamp(i.due_at).strftime("%H:%M") for i in items]
        return Reply("Alarms: " + ", ".join(parts) + ".", intent="alarm.list")
    items = ctx.scheduler.list("reminder")
    if not items:
        return Reply("No reminders are set.", intent="reminder.list")
    parts = [f"{i.label.replace('reminder ', '')} at "
             f"{dt.datetime.fromtimestamp(i.due_at).strftime('%H:%M')}" for i in items]
    return Reply("Reminders: " + ", ".join(parts) + ".", intent="reminder.list")


async def h_schedule_cancel(text: str, match, ctx: Context) -> Reply:
    kind = "alarm" if re.search(r"\balarms?\b", text) else "reminder"
    count = await ctx.scheduler.cancel(kind)
    ctx.pending = None
    noun = "alarm" if kind == "alarm" else "reminder"

    # Alarms also live on the device as native timers (that is what rings).
    # Cancel those too, and dismiss one that is already ringing.
    if kind == "alarm":
        try:
            for timer in await ctx.device.list_timers():
                label = str(timer.get("label") or "").lower()
                if "alarm" not in label:
                    continue
                if timer_is_ringing(timer):
                    await ctx.device.dismiss_timers()
                    count += 1
                    continue
                timer_id = timer.get("id")
                if timer_id is not None:
                    try:
                        await ctx.device.cancel_timer(timer_id)
                        count += 1
                    except Exception as exc:
                        log.debug("cancel alarm timer %s failed: %s", timer_id, exc)
        except Exception as exc:
            log.warning("reading device timers failed: %s", exc)

    if not count:
        return Reply(f"There are no {noun}s to cancel.", intent=f"{kind}.cancel")
    return Reply(f"Cancelled {count} {noun}{'s' if count != 1 else ''}.",
                 intent=f"{kind}.cancel")


async def h_news(text: str, match, ctx: Context) -> Reply:
    cleaned = re.sub(
        r"\b(?:what'?s|what is|tell me|give me|the|latest|top|today'?s|news|headlines|"
        r"notizie|ultime|please|about|read|say)\b",
        " ", normalise(text),
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.")
    country = ""
    hit = re.search(r"\b(?:in|from)\s+([a-z]{3,})\b", cleaned)
    if hit:
        country = hit.group(1)
        cleaned = cleaned.replace(hit.group(0), "").strip(" ,.")
    summary = await search_news(ctx.http, cleaned, country)
    return Reply(summary or "I couldn't find any news just now.", intent="news")


async def h_calendar(text: str, match, ctx: Context) -> Reply:
    summary = await get_today_events(ctx.http)
    if not summary:
        return Reply(
            "There's no calendar connected yet. Set CALENDAR_ICS_URL in the .env file.",
            intent="calendar",
        )
    return Reply(summary, intent="calendar")


async def h_calendar_add(text: str, match, ctx: Context) -> Reply:
    """Add a timed reminder/event to the local calendar.ics."""
    groups = match.groupdict() if match else {}
    what = str(groups.get("what") or "").strip(" ,.;:-")
    if re.fullmatch(r"(?:an?|the)\s+(?:reminder|event|appointment)", what, re.IGNORECASE):
        tail = re.split(r"(?:calendar|agenda)\s*[:,-]?\s*", text, maxsplit=1, flags=re.IGNORECASE)
        if len(tail) > 1 and tail[1].strip():
            what = tail[1].strip(" ,.;:-")
    if not what and (ctx.pending or {}).get("kind") == "calendar":
        what = str(ctx.pending.get("what") or "").strip(" ,.;")
    if not what:
        hit = re.search(r"\b(?:event|appointment|reminder)\b\s*(.+)", text, re.IGNORECASE)
        what = (hit.group(1) if hit else "").strip(" ,.;")
    what = DURATION_RE.sub(" ", what)
    what = re.sub(r"\b(?:at|for|around|by)\s+\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?\b",
                  " ", what, flags=re.IGNORECASE)
    what = re.sub(r"\b(?:to|on|in|at|for)\s+(?:my\s+)?(?:calendar|agenda)\b", " ",
                  what, flags=re.IGNORECASE)
    what = re.sub(r"\b(?:tomorrow|domani|today|oggi)\b", " ", what, flags=re.IGNORECASE)
    what = re.sub(r"\s+", " ", what).strip(" ,.;")
    if not what:
        ctx.pending = {"kind": "calendar", "expires": time.monotonic() + ctx.settings.pending_ttl}
        return Reply("What should I add to your calendar?", intent="calendar.add")

    when, _ = parse_event_when(text)
    if when is None:
        ctx.pending = {"kind": "calendar", "what": what,
                       "expires": time.monotonic() + ctx.settings.pending_ttl}
        return Reply("When should I add it to your calendar?", intent="calendar.add")
    try:
        append_event(what, when)
    except Exception as exc:
        log.warning("calendar write failed: %s", exc)
        return Reply("I couldn't write to the calendar.", intent="calendar.add")
    pretty = when.strftime("%H:%M") if when.date() == dt.date.today() else when.strftime("%A %H:%M")
    return Reply(f"Added {what} to your calendar for {pretty}.", intent="calendar.add")


async def _scan_and_report(ctx: Context) -> None:
    global _SCAN_STATE
    found: list[dict] = []
    try:
        previous: dict = {}
        if os.path.isfile(IOT_REPORT):
            try:
                with open(IOT_REPORT, encoding="utf-8-sig") as handle:
                    previous = json.load(handle)
            except Exception:
                previous = {}
        known = iot_known_ips()
        known.update(str(d.get("ip")) for d in (ctx.iot.devices if ctx.iot else []))
        current = await iot_run_scan()
        found = new_since(previous, current, known)
        if found:
            iot_stage(found)
        if found:
            labels = ", ".join(f"{d.get('ip')} ({'/'.join(d.get('protocols') or []) or 'unknown'})"
                               for d in found[:4])
            summary = (f"Found {len(found)} new device{'s' if len(found) != 1 else ''}: "
                       f"{labels}. I saved placeholders; add an ID and local key to control them.")
        else:
            summary = "Scan finished. No new devices found."
        _SCAN_STATE["error"] = ""
    except Exception as exc:
        summary = "The network scan didn't finish. " + friendly_error(exc)
        _SCAN_STATE["error"] = str(exc)[:200]
        log.warning("iot scan failed: %s", exc)
    _SCAN_STATE.update(running=False, last=time.time(), new=len(found))
    now = time.monotonic()
    ctx.recent_speech.append((now, summary))
    ctx.recent_speech = [(t, s) for t, s in ctx.recent_speech if now - t < 30.0][-4:]
    await ctx.speech.say(summary)


async def h_iot_scan(text: str, match, ctx: Context) -> Reply:
    global _SCAN_TASK, _SCAN_STATE
    if _SCAN_STATE["running"] and _SCAN_TASK and not _SCAN_TASK.done():
        return Reply("A scan is already running; I'll report when it finishes.",
                     intent="iot.scan")
    _SCAN_STATE["running"] = True
    _SCAN_TASK = background(_scan_and_report(ctx))
    return Reply("Scanning the local network now. I'll let you know what I find.",
                 intent="iot.scan")


async def h_iot_scan_status(text: str, match, ctx: Context) -> Reply:
    if _SCAN_STATE["running"]:
        return Reply("The scan is still running.", intent="iot.scan.status")
    if not _SCAN_STATE["last"]:
        return Reply("I haven't scanned for devices yet.", intent="iot.scan.status")
    age = int(time.time() - _SCAN_STATE["last"])
    if _SCAN_STATE["error"]:
        return Reply(f"The last scan failed: {_SCAN_STATE['error'][:80]}",
                     intent="iot.scan.status")
    return Reply(f"The last scan was {age} seconds ago and found "
                 f"{_SCAN_STATE['new']} new device(s).", intent="iot.scan.status")


async def h_places(text: str, match, ctx: Context) -> Reply:
    low = normalise(text)
    what = "restaurants"
    for keyword, value in (("pizza", "pizzerias"), ("pizzeria", "pizzerias"),
                           ("sushi", "sushi restaurants"), ("vegan", "vegan restaurants"),
                           ("vegetarian", "vegetarian restaurants"),
                           ("seafood", "seafood restaurants")):
        if keyword in low:
            what = value
            break
    city = ""
    hit = re.search(r"\b(?:in|at|near|around|a)\s+([A-Za-z][A-Za-z' ]{1,30})", text, re.IGNORECASE)
    if hit:
        city = hit.group(1).strip(" ,.")
    if city.lower() in {"me", "here", "you", "us", "there"}:
        city = ""
    if not city and ctx.places is None:
        return Reply("Place lookup isn't available right now.", intent="places")
    names = await ctx.places.search(what, city) if ctx.places else []
    if not names:
        return Reply(f"I couldn't find {what} there.", intent="places")
    if ctx.places:
        background(asyncio.to_thread(webbrowser.open, ctx.places.map_url(what, city)))
    top = names[:3]
    spoken = ", ".join(top[:-1]) + f" and {top[-1]}" if len(top) > 1 else top[0]
    where = f" in {city}" if city else " near you"
    return Reply(f"{spoken.title()}{where}. I've opened a map with the full list.",
                 intent="places")


async def h_flights(text: str, match, ctx: Context) -> Reply:
    if re.search(r"\b(?:book|buy|purchase|pay for)\b", text, re.IGNORECASE):
        return Reply("I can look up flights, but I can't book or buy tickets.",
                     intent="flights")
    url, start, end, destination = build_flights_url(text)
    pretty = f"for {start.strftime('%d %B')}"
    result = await flights.search(ctx.http, destination, start.isoformat())
    if result.get("ok") and result.get("offers"):
        offers = result["offers"][:2]
        pieces = []
        for offer in offers:
            price = offer.get("price")
            if price is None:
                continue
            stops = "direct" if not offer.get("stops") else f"{offer['stops']} stop"
            depart = offer.get("depart") or ""
            pieces.append(f"{offer.get('airline')} {price} euros, {stops}"
                          + (f", leaving {depart}" if depart else ""))
        background(asyncio.to_thread(webbrowser.open, result.get("url") or url))
        if pieces:
            origin_code = result.get("origin") or flights.status().get("origin") or ""
            origin_name = {"FLR": "Florence", "PSA": "Pisa", "MXP": "Milan",
                           "VCE": "Venice", "BLQ": "Bologna"}.get(origin_code, origin_code)
            return Reply(f"From {origin_name} to " + destination + " " + pretty + ": "
                         + "; ".join(pieces) + ". Prices are estimates, and I've "
                         "opened the search on the computer.", intent="flights")
    background(asyncio.to_thread(webbrowser.open, url))
    if result.get("reason") == "daily_limit":
        return Reply("I've used today's free flight searches, so I opened a "
                     f"Google Flights search to {destination} {pretty} instead.",
                     intent="flights")
    return Reply(
        f"I can't check live fares right now, but I've opened a Google Flights "
        f"search to {destination} {pretty} on the computer.",
        intent="flights",
    )


async def h_location(text: str, match, ctx: Context) -> Reply:
    city = str(ctx.settings.city or "").strip()
    lat, lon = ctx.settings.lat, ctx.settings.lon
    if lat is None or lon is None:
        return Reply("I don't have a configured location. Set WEATHER_LAT and "
                     "WEATHER_LON in .env.", intent="location")
    place = await train_provider.reverse(ctx.http, lat, lon)
    near = place.get("name") or ""
    country = place.get("country") or ""
    bits = []
    if near:
        bits.append(f"near {near}")
    elif city:
        bits.append(f"near {city}")
    if country and country != "IT":
        bits.append(country)
    where = " ".join(bits) or "at your configured home"
    coords = f"{abs(lat):.2f} degrees {'north' if lat >= 0 else 'south'}, " \
             f"{abs(lon):.2f} degrees {'east' if lon >= 0 else 'west'}"
    home = f" Your home city is set to {city}." if city else ""
    return Reply(f"You are {where}, around {coords}.{home}", intent="location")


async def h_trains(text: str, match, ctx: Context) -> Reply:
    parsed = train_provider.parse_request(text)
    origin, destination = parsed["origin"], parsed["destination"]
    if not origin or not destination:
        return Reply("Tell me the departure and destination cities, for example "
                     "trains from Rome to Milan tomorrow at eight.", intent="trains")

    when = parsed["when"]
    a, b = await asyncio.gather(
        train_provider.geocode(ctx.http, origin),
        train_provider.geocode(ctx.http, destination),
    )
    if not a or not b:
        return Reply(f"I couldn't look up {origin} or {destination} on the map.",
                     intent="trains")
    trips = await train_provider.plan(ctx.http, a, b, when, count=3)
    if not trips:
        return Reply(f"No trains found from {a['name']} to {b['name']} "
                     f"{'on that date' if when else 'today'}.", intent="trains")

    day = ""
    if when:
        today = dt.date.today()
        day = "Today " if when.date() == today else (
            "Tomorrow " if when.date() == today + dt.timedelta(days=1)
            else when.strftime("%A %d %B ") + "")
    parts = []
    for trip in trips[:2]:
        train = trip["trains"].split(",")[0].strip()
        change = "direct" if not trip["transfers"] else f"{trip['transfers']} change{'s' if trip['transfers'] > 1 else ''}"
        parts.append(f"at {trip['depart']}, arriving {trip['arrive']}, "
                     f"{trip['duration']}, {change}" +
                     (f", {train}" if train else ""))
    reply = f"{day}from {origin} to {destination}: " + "; ".join(parts) + "."
    if not any(trip["prices"] for trip in trips):
        reply += " The free timetable doesn't include ticket prices."
        url = train_provider.price_url(origin, destination, when)
        background(asyncio.to_thread(webbrowser.open, url))
        reply += " I've opened a price check on the computer."
    else:
        prices = sorted({p for trip in trips for p in trip["prices"]})
        if prices:
            reply += f" Fares from {prices[0]:.0f}."
    return Reply(reply, intent="trains")


async def h_youtube(text: str, match, ctx: Context) -> Reply:
    # "Alexa, show me Al Jazeera live on YouTube", "play any Jamaican dancehall
    # music video" -> keep the useful search words, drop only the command shell.
    query = re.sub(r"^\s*(?:alexa[,\s]+)?(?:please\s+)?(?:i\s+(?:want|would like)\s+to\s+)?"
                   r"(?:show\s+me|can\s+you\s+(?:show|play)|play|watch|put\s+on|find|"
                   r"riproduci|mostrami|guarda)\s+", "", text, flags=re.IGNORECASE)
    query = re.sub(r"^(?:any|some|the|a|an)\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+(?:on|in|su|onto)\s+(?:youtube|yt)\b.*$", "", query,
                   flags=re.IGNORECASE)
    query = re.sub(r"\s+(?:su|youtube|yt)\s*$", "", query, flags=re.IGNORECASE)
    query = query.strip(" ,.?\"'")
    if not query:
        return Reply("What would you like to watch on YouTube?", intent="youtube.play")

    # A media command must work when the screen is off: wake it first.
    awake = await ctx.tv.ensure_awake()
    if not awake.ok:
        log.warning("youtube: TV unavailable: %s", awake.message)
        url, _resolved = await resolve_youtube_url(ctx.http, query)
        background(asyncio.to_thread(webbrowser.open, url))
        return Reply(f"The TV is off and I couldn't wake it ({awake.message}). "
                     f"I opened YouTube on the computer instead.", intent="youtube.play")

    url, resolved = await resolve_youtube_url(ctx.http, query)
    result = await ctx.tv.adb_view_url(url, package="com.google.android.youtube.tv")
    log.info("youtube TV result: ok=%s message=%r", result.ok, result.message)
    if result.ok:
        label = f'"{query}"' if resolved else f"a YouTube search for {query}"
        prefix = "Waking the TV. " if not awake.message.startswith("TV is on") else ""
        return Reply(f"{prefix}Playing {label} on the TV.", intent="youtube.play")
    background(asyncio.to_thread(webbrowser.open, url))
    return Reply("The TV didn't answer, so I opened YouTube on the computer.",
                 intent="youtube.play")


async def h_briefing(text: str, match, ctx: Context) -> Reply:
    now = dt.datetime.now(LOCAL_TZ)
    greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"
    parts = [f"{greeting}. It's {now:%H:%M}."]
    try:
        parts.append(await ctx.weather.report(None, italian=_looks_italian(text)))
    except Exception:
        pass
    try:
        news = await search_news(ctx.http, "", max_records=3)
        if news:
            parts.append(news)
    except Exception:
        pass
    try:
        events = await get_today_events(ctx.http)
        if events:
            parts.append(events)
    except Exception:
        pass
    return Reply(" ".join(parts), intent="briefing")


async def h_shopping_add(text: str, match, ctx: Context) -> Reply:
    groups = match.groupdict() if match else {}
    item = groups.get("item") or re.sub(
        r"^(?:add|put)\s+", "", text, flags=re.IGNORECASE).split(" to my shopping list")[0]
    return Reply(ctx.shopping.add(item) if ctx.shopping else "Shopping list isn't available.",
                 intent="shopping.add")


async def h_shopping_remove(text: str, match, ctx: Context) -> Reply:
    groups = match.groupdict() if match else {}
    item = groups.get("item") or ""
    return Reply(ctx.shopping.remove(item) if ctx.shopping else "Shopping list isn't available.",
                 intent="shopping.remove")


async def h_shopping_list(text: str, match, ctx: Context) -> Reply:
    if re.search(r"\b(?:clear|empty|wipe)\b", text, re.IGNORECASE):
        return Reply(ctx.shopping.clear() if ctx.shopping else "Shopping list isn't available.",
                     intent="shopping.clear")
    return Reply(ctx.shopping.read() if ctx.shopping else "Shopping list isn't available.",
                 intent="shopping.list")


async def h_joke(text: str, match, ctx: Context) -> Reply:
    return Reply(tell_joke(), intent="joke")


async def h_quote(text: str, match, ctx: Context) -> Reply:
    return Reply(daily_quote(), intent="quote")


async def h_units(text: str, match, ctx: Context) -> Reply:
    result = convert_units(text)
    return Reply(result or "I couldn't work out those units.", intent="unit.convert")


async def h_currency(text: str, match, ctx: Context) -> Reply:
    hit = re.search(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)s?\b.*?\b(?:in|to)\s+([a-zA-Z]+)s?\b",
                    text, re.IGNORECASE)
    if not hit:
        return Reply("I didn't catch the amount and currencies.", intent="currency.convert")
    result = await fx_convert(ctx.http, float(hit.group(1)), hit.group(2), hit.group(3))
    return Reply(result or "I couldn't get an exchange rate just now.", intent="currency.convert")


_DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def speak_phone(value: str) -> str:
    """Spoken form of a phone number: grouped digits, never a raw digit string."""
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw
    country = 2 if digits.startswith("39") else 0
    rest = digits[country:]
    chunks, i = [], 0
    while i < len(rest):
        remaining = len(rest) - i
        size = 3 if remaining > 4 else remaining
        chunks.append(rest[i:i + size])
        i += size
    groups = ([digits[:country]] if country else []) + chunks
    spoken = ", ".join(
        " ".join(_DIGIT_WORDS[d] for d in chunk) for chunk in groups
    )
    return "plus " + spoken if raw.startswith("+") else spoken


def wa_message_from(text: str) -> str:
    body = re.sub(r"^\s*(?:saying|that says|message|text)\s*", "", text,
                  flags=re.IGNORECASE).strip()
    return body.strip(" \"'“”‘’ ") if body else ""


def wa_display(candidate: dict) -> str:
    name = str((candidate or {}).get("name") or "").strip()
    if name:
        return name
    return speak_phone(str((candidate or {}).get("number") or ""))


async def _wa_deliver(ctx: Context, target: str, body: str, display: str) -> Reply:
    state = await whatsapp_bridge.status(ctx.http)
    if state.get("ok") and state.get("status") != "ready":
        return Reply("WhatsApp isn't connected yet. Scan the QR code on the computer "
                     "with WhatsApp, Linked devices.", intent="whatsapp.send")
    result = await whatsapp_bridge.send(ctx.http, target, body)
    if not result.get("ok"):
        return Reply("I couldn't send that WhatsApp: " + str(result.get("error"))[:100],
                     intent="whatsapp.send")
    playback = f' It said: {body}.' if 0 < len(body) <= 120 else ""
    return Reply(f"WhatsApp message sent to {display}.{playback}", intent="whatsapp.send")


def _is_phone_number(value: str) -> bool:
    return bool(re.fullmatch(r"\+?[\d][\d\s().-]{5,}", str(value or "")))


async def h_whatsapp_send(text: str, match, ctx: Context) -> Reply:
    groups = match.groupdict() if match else {}
    contact = str(groups.get("contact") or "").strip(" ,.")
    body = wa_message_from(str(groups.get("body") or ""))
    if not contact or not body:
        hit = re.search(
            r"\b(?:send|text|message)\s+(?:a\s+)?whatsapp\s+(?:message|msg)?\s*to\s+"
            r"(?P<contact>.+?)\s*,?\s+(?:saying|that says|:)\s*(?P<body>.+)$",
            text, re.IGNORECASE)
        if hit:
            contact = contact or hit.group("contact").strip(" ,.")
            body = body or wa_message_from(hit.group("body"))
    if not contact:
        return Reply("Who should I WhatsApp?", intent="whatsapp.send")
    if not body:
        ctx.pending = {"kind": "whatsapp.body", "contact": contact,
                       "expires": time.monotonic() + ctx.settings.pending_ttl}
        return Reply("What should the message say?", intent="whatsapp.send")

    if _is_phone_number(contact):
        digits = re.sub(r"\D", "", contact)
        return await _wa_deliver(ctx, digits + "@c.us", body, speak_phone(contact))

    result = await whatsapp_bridge.contacts(ctx.http, contact)
    matches = [c for c in (result.get("contacts") or []) if not c.get("isGroup")]
    # WhatsApp often returns the same chat twice (saved contact + phone entry).
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for c in matches:
        key = str(c.get("id") or c.get("number") or c.get("name") or "")
        if key and key not in seen_ids:
            seen_ids.add(key)
            unique.append(c)
    matches = unique
    needle = contact.lower()
    exact = [c for c in matches if str(c.get("name") or "").lower() == needle]
    candidates = exact or matches
    if not candidates:
        return Reply(f"I couldn't find {contact} in WhatsApp. Try their full name "
                     "or give me their number.", intent="whatsapp.send")
    if len(candidates) == 1:
        chosen = candidates[0]
        return await _wa_deliver(ctx, chosen.get("id") or chosen.get("number") or contact,
                                 body, wa_display(chosen))

    names = ", ".join(wa_display(c) for c in candidates[:4])
    ctx.pending = {
        "kind": "whatsapp", "body": body, "candidates": candidates[:5],
        "asked": f"I found {len(candidates)} contacts for {contact}: {names}. Which one?",
        "expires": time.monotonic() + ctx.settings.pending_ttl,
    }
    return Reply(ctx.pending["asked"], intent="whatsapp.send")


async def h_whatsapp_status(text: str, match, ctx: Context) -> Reply:
    state = await whatsapp_bridge.status(ctx.http)
    if not state.get("ok"):
        return Reply("The WhatsApp bridge isn't running.", intent="whatsapp.status")
    status = str(state.get("status") or "unknown")
    if status == "qr":
        return Reply("WhatsApp is waiting for a QR scan on the computer.", intent="whatsapp.status")
    if status == "ready":
        return Reply("WhatsApp is connected and ready.", intent="whatsapp.status")
    return Reply(f"WhatsApp status is {status}.", intent="whatsapp.status")


async def h_whatsapp_contacts(text: str, match, ctx: Context) -> Reply:
    query = ""
    hit = re.search(r"\bfind\s+(.+?)\s+on\s+whatsapp\b", text, re.IGNORECASE)
    if hit:
        query = hit.group(1).strip()
    result = await whatsapp_bridge.contacts(ctx.http, query)
    if not result.get("ok"):
        return Reply("I couldn't read WhatsApp contacts: " + str(result.get("error"))[:80],
                     intent="whatsapp.contacts")
    names = [c.get("name") or c.get("number") for c in (result.get("contacts") or [])][:8]
    if not names:
        return Reply("No WhatsApp contacts matched.", intent="whatsapp.contacts")
    return Reply("WhatsApp contacts: " + ", ".join(str(n) for n in names) + ".",
                 intent="whatsapp.contacts")


async def h_clone_start(text: str, match, ctx: Context) -> Reply:
    _VOICE_CAPTURE.update(active=True, chunks=[], path="")
    return Reply("Clone mode armed. Say a short sentence — about five seconds — "
                 "and I'll save your voice as a new persona.", intent="voice.clone")


async def h_clone_status(text: str, match, ctx: Context) -> Reply:
    if _VOICE_CAPTURE.get("active"):
        return Reply("Clone mode is armed and waiting for your voice sample.",
                     intent="voice.clone.status")
    if _VOICE_CAPTURE.get("path"):
        return Reply("Sample captured. Waiting for a name — who is this?",
                     intent="voice.clone.status")
    personas = voices.all_personas()
    return Reply(f"Clone mode is idle. {len(personas)} saved voice(s). "
                 "Say activate clone mode to begin.", intent="voice.clone.status")


async def h_voice_use(text: str, match, ctx: Context) -> Reply:
    groups = match.groupdict() if match else {}
    name = str(groups.get("name") or re.sub(
        r"^\s*(?:please\s+)?(?:speak|talk)\s+like\s+", "", text,
        flags=re.IGNORECASE).strip(" .!?"))
    if name.lower() in {"normal", "default", "off", "my normal voice", "your normal voice"}:
        voices.set_active("")
        return Reply("Back to my normal voice.", intent="voice.use")
    personas = voices.all_personas()
    key = " ".join(name.lower().split())
    chosen = personas.get(key)
    if chosen is None:
        for candidate_key, candidate in personas.items():
            if key in candidate_key or candidate_key in key:
                chosen, key = candidate, candidate_key
                break
    if chosen is None:
        return Reply(f"I don't have a voice called {name}. Say clone to create one.",
                     intent="voice.use")
    voices.set_active(key)
    if chosen.get("ready"):
        return Reply(f"Speaking like {chosen.get('name') or name}.", intent="voice.use")
    return Reply(
        f"Selected {chosen.get('name') or name}, but its {chosen.get('provider')} "
        "backend isn't available here yet, so I'll keep the normal voice.",
        intent="voice.use",
    )


async def h_voice_list(text: str, match, ctx: Context) -> Reply:
    personas = voices.all_personas()
    if not personas:
        return Reply("No saved voices yet. Say clone to create one.", intent="voice.list")
    active, _ = voices.active()
    parts = [f"{p.get('name')}{' (active)' if k == active else ''}"
             for k, p in personas.items()]
    return Reply("Saved voices: " + ", ".join(parts) + ".", intent="voice.list")


async def h_messenger(text: str, match, ctx: Context) -> Reply:
    body = None
    hit = re.search(r"\b(?:saying|that says|with the message)\s+(.+)$", text, re.IGNORECASE)
    if hit:
        body = hit.group(1)
    else:
        hit = re.search(r":\s*(.+)$", text)
        if hit:
            body = hit.group(1)
    if not body:
        return Reply("What should the message say?", intent="messenger")
    result = await send_telegram(ctx.http, body.strip())
    return Reply(result or "Message sent.", intent="messenger")


async def h_music_mood(text: str, match, ctx: Context) -> Reply:
    query = re.sub(r"\b(?:play|put on|metti|riproduci|some|a bit of|the)\b",
                   " ", text, flags=re.IGNORECASE)
    query = re.sub(r"\b(?:music|musica|radio|please)\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+", " ", query).strip(" ,.")
    if not query:
        ctx.pending = {"kind": "music", "prompt": MUSIC_QUESTION,
                       "expires": time.monotonic() + ctx.settings.pending_ttl}
        return Reply(MUSIC_QUESTION, intent="music.mood")

    if ctx.music_assistant.enabled:
        # Native playback takes over the speaker; stop MA so both never fight.
        background(ctx.music_assistant.stop())

    cached = ctx.music_catalog.lookup(query)
    if cached:
        stations = await ctx.radio.all()
        known = next((s for s in stations if str(s.get("word")) == cached.get("word")), None)
        if known is None and cached.get("url"):
            known = await ctx.radio.add(cached)
        if known is not None:
            await ctx.device.play_radio(known.get("word") or cached.get("word"))
            return Reply(f"Playing {cached.get('name')}.", intent="music.mood")

    chosen = await ctx.radio.play(query)
    if not chosen:
        return Reply(f"I couldn't find a station for {query}.", intent="music.mood")
    return Reply(f"Playing {chosen.get('name')}.", intent="music.mood")


async def h_personal_fact(text: str, match, ctx: Context) -> Reply:
    memory = ctx.memory
    low = normalise(text)

    # "what do you remember (about me)?" -> show the long-term store
    if re.search(r"\b(?:what do you (?:remember|know)(?: about me)?|cosa (?:ricordi|sai) (?:di me)?)\b", low):
        facts = memory.snapshot().get("facts") if memory else {}
        notes = memory.snapshot().get("notes") if memory else []
        if not facts and not notes:
            return Reply("I don't have anything stored about you yet.", intent="facts.list")
        pieces = [f"your {key.replace('_', ' ')} is {value}" for key, value in list(facts.items())[:8]]
        pieces += list(notes)[:4]
        return Reply("I remember that " + "; ".join(pieces) + ".", intent="facts.list")

    # "remember that ..." -> a note, or a fact when it looks like "my X is Y"
    hit = re.search(r"\b(?:remember|keep in mind|ricorda)\b(?:\s+that)?\s+(.+)$", text, re.IGNORECASE)
    if hit:
        body = hit.group(1).strip(" .")
        inner = re.search(r"\bmy\s+(\w+(?:\s+\w+)?)\s+(?:is|are)\s+(.+)", body, re.IGNORECASE)
        if inner and memory:
            key, value = inner.group(1).strip(), inner.group(2).strip(" .")
            memory.remember(key, value)
            return Reply(f"I'll remember that your {key.lower()} is {value}.",
                         intent="facts.remember")
        if memory and body:
            return Reply(memory.add_note(body), intent="facts.remember")

    # "my birthday is october 29"
    inner = re.search(r"\bmy\s+(\w+(?:\s+\w+)?)\s+(?:is|are)\s+(.+)", text, re.IGNORECASE)
    if inner and memory:
        key, value = inner.group(1).strip(), inner.group(2).strip(" .")
        memory.remember(key, value)
        return Reply(f"I'll remember that your {key.lower()} is {value}.", intent="facts.remember")

    # "I like jazz" — keep it short. Long ASR-garbled clauses used to be stored
    # as both a fact and a note and then injected into every LLM prompt.
    inner = re.search(r"\bI\s+(?:like|love|prefer)\s+(.+)", text, re.IGNORECASE)
    if inner and memory:
        thing = re.split(r"[,;:]|\b(?:but|because|and then)\b", inner.group(1), 1)[0].strip(" .")
        words = thing.split()
        if not words or len(words) > 6 or len(thing) < 3:
            return Reply("I didn't catch what you like.", intent="facts.remember")
        memory.remember(f"likes {thing.lower()}", thing.lower())
        return Reply(f"I'll remember that you like {thing}.", intent="facts.remember")

    # Recall: ask permanent memory first, then the legacy fact file.
    query = re.sub(r"^(?:what(?:'s| is)|do you (?:know|remember)|when is|tell me)\s+(?:my\s+)?", "",
                   low).strip(" ?.")
    recalled = memory.recall(query) if memory else None
    if not recalled:
        recalled = ctx.facts.recall(query or low)
    if recalled:
        return Reply(f"You told me: {recalled}.", intent="facts.recall")
    return Reply("I don't have anything stored about that yet.", intent="facts.recall")


async def h_tv_on(text: str, match, ctx: Context) -> Reply:
    result = await ctx.tv.power_on()
    return Reply(result.message, intent="tv.on")


async def h_tv_off(text: str, match, ctx: Context) -> Reply:
    result = await ctx.tv.power_off()
    return Reply(result.message, intent="tv.off")


async def h_tv_pause(text: str, match, ctx: Context) -> Reply:
    result = await ctx.tv.cast_pause()
    return Reply(result.message, intent="tv.pause")


async def h_tv_resume(text: str, match, ctx: Context) -> Reply:
    result = await ctx.tv.cast_resume()
    return Reply(result.message, intent="tv.resume")


async def h_tv_stop(text: str, match, ctx: Context) -> Reply:
    result = await ctx.tv.cast_stop()
    return Reply(result.message, intent="tv.stop")


async def h_tv_volume(text: str, match, ctx: Context) -> Reply:
    low = normalise(text)
    if re.search(r"\b(unmute|riattiva l'audio|togli il muto)\b", low):
        result = await ctx.tv.cast_volume(mute=False)
    elif re.search(r"\b(mute|muta|muto)\b", low):
        result = await ctx.tv.cast_volume(mute=True)
    else:
        level = re.search(r"\b(?:to|at|al|a)\s*(\d{1,3})\b", low)
        if level:
            result = await ctx.tv.cast_volume(level=int(level.group(1)))
        elif re.search(r"\b(up|raise|increase|alza|alzare|su)\b", low):
            result = await ctx.tv.cast_volume(direction="up")
        elif re.search(r"\b(down|lower|decrease|abbassa|abbassare|giu|giù)\b", low):
            result = await ctx.tv.cast_volume(direction="down")
        else:
            result = await ctx.tv.cast_volume()
    return Reply(result.message, intent="tv.volume")


async def h_tv_status(text: str, match, ctx: Context) -> Reply:
    result = await ctx.tv.status()
    return Reply(result.message, intent="tv.status")


TV_TARGET = r"(?:the\s+)?(?:tv|telly|television|tele|televisore|chromecast)"


async def h_tv_play(text: str, match, ctx: Context) -> Reply:
    query = re.sub(r"\b(?:play|cast|put on|stream|metti|riproduci|trasmetti)\b",
                   " ", text, flags=re.IGNORECASE)
    query = re.sub(r"\b(?:on|to|sulla|sul|alla|al)\s+" + TV_TARGET + r"\b",
                   " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+", " ", query).strip(" ,.")
    if not query:
        return Reply("What should I put on the TV?", intent="tv.play")
    if re.match(r"^[a-z]+://", query):
        result = await ctx.tv.cast_audio(query, title=query)
    else:
        station = await ctx.radio.best(query)
        if station is None:
            return Reply(f"I couldn't find anything to cast for {query}.", intent="tv.play")
        result = await ctx.tv.cast_audio(str(station.get("url") or ""),
                                         title=str(station.get("name") or ""))
    return Reply(result.message, intent="tv.play")


async def h_tv_launch(text: str, match, ctx: Context) -> Reply:
    groups = match.groupdict() if match else {}
    app = (groups.get("app") or "").strip()
    if not app:
        return Reply("Which app should I open?", intent="tv.app")
    result = await ctx.tv.launch(app)
    return Reply(result.message, intent="tv.app")


async def h_help(text: str, match, ctx: Context) -> Reply:
    return Reply(
        "I can do time, date, weather, news, calendar, radio, volume, timers, alarms, "
        "reminders, messages, personal facts and home control. "
        "Try: what's the weather in Florence, play Radio Italia, timer for five minutes, "
        "remind me to call mum in twenty minutes, turn on the kitchen light.",
        intent="help",
    )



# =====================================================================
# PHASE-1 INTENT HANDLERS (functional only; no placeholder registrations)
# =====================================================================
_MUTED_VOLUME: Optional[int] = None


def _spoken_arg(text: str) -> str:
    """Strip the leading command verb(s) from a spoken phrase."""
    return re.sub(
        r"^(?:alexa[,\s]+)?(?:please\s+)?(?:i\s+(?:want|would like)\s+to\s+)?"
        r"(?:announce|say|note|remember|write down|set|make|check|show|read|list|"
        r"turn|switch|mute|unmute|duck|lower|raise|increase|decrease)\s+",
        "", str(text or "").strip(), flags=re.IGNORECASE).strip(" ,.?")


async def h_wakeword_status(text: str, match, ctx: Context) -> Reply:
    try:
        d = (await ctx.device.call("GET", "/wake-word", timeout=8)).get("data") or {}
    except Exception as exc:
        return Reply(f"I couldn't read the wake word status: {exc}", intent="wakeword.status")
    sens = d.get("sensitivity")
    threshold = round(0.85 - 0.0044 * float(sens), 3) if sens is not None else None
    bits = [f"wake word {d.get('wake_word', 'unknown')}",
            f"model {d.get('model_status', 'unknown')}"]
    if sens is not None:
        bits.append(f"sensitivity {sens} (threshold {threshold})")
    bits.append("ready" if d.get("capture_active") and d.get("inference_active") else "not ready")
    return Reply("; ".join(bits) + ".", intent="wakeword.status")


async def h_wakeword_sensitivity(text: str, match, ctx: Context) -> Reply:
    return await h_wakeword_status(text, match, ctx)


async def h_wakeword_set(text: str, match, ctx: Context) -> Reply:
    hit = re.search(r"\d{1,3}", text)
    if not hit:
        return Reply("Tell me a sensitivity from 0 to 100.", intent="wakeword.set")
    value = max(0, min(100, int(hit.group(0))))
    try:
        await ctx.device.call("PUT", "/wake-word", {"sensitivity": value},
                              confirm=True, timeout=8)
    except Exception as exc:
        return Reply(f"I couldn't set wake sensitivity: {exc}", intent="wakeword.set")
    return Reply(f"Wake sensitivity set to {value}.", intent="wakeword.set")


async def h_wakeword_list(text: str, match, ctx: Context) -> Reply:
    base = await h_wakeword_status(text, match, ctx)
    try:
        data = await ctx.device.call("GET", "/wake-word/models", timeout=6)
        models = (data.get("data") or {}).get("models") or []
        names = ", ".join(str(m.get("name")) for m in models) or "none staged"
        return Reply(base.text + f" Available models: {names}.", intent="wakeword.list")
    except Exception:
        return Reply(base.text + " Custom model management requires LibreEcho 0.14.6 or newer.",
                     intent="wakeword.list")


async def h_wakeword_test(text: str, match, ctx: Context) -> Reply:
    try:
        await ctx.device.call("POST", "/wake-word/test", {}, timeout=8)
    except Exception as exc:
        return Reply(f"The wake word test failed: {exc}", intent="wakeword.test")
    return Reply("That was a synthetic wake test; real speech was not evaluated.",
                 intent="wakeword.test")


async def h_microphone_status(text: str, match, ctx: Context) -> Reply:
    try:
        audio = (await ctx.device.call("GET", "/audio", timeout=8)).get("data") or {}
        beams = (await ctx.device.call("GET", "/microphones/beams", timeout=8)).get("data") or {}
    except Exception as exc:
        return Reply(f"I couldn't read the microphone status: {exc}", intent="microphone.status")
    return Reply(
        f"Microphone gain {audio.get('microphone_gain', '?')}, "
        f"{'muted' if audio.get('microphone_muted') else 'unmuted'}. "
        f"Capture running: {beams.get('running', False)}; "
        f"selected beam {beams.get('selected_deg', '?')} degrees, "
        f"dominant direction {beams.get('doa_deg', '?')} degrees.",
        intent="microphone.status")


async def h_microphone_test(text: str, match, ctx: Context) -> Reply:
    try:
        first = (await ctx.device.call("GET", "/microphones/beams", timeout=8)).get("data") or {}
        await asyncio.sleep(1.5)
        second = (await ctx.device.call("GET", "/microphones/beams", timeout=8)).get("data") or {}
    except Exception as exc:
        return Reply(f"The microphone check failed: {exc}", intent="microphone.test")
    return Reply(
        f"Microphone capture is {'active' if second.get('running') else 'inactive'}. "
        f"Peak level {second.get('peak_db', first.get('peak_db', '?'))} dBFS, "
        f"direction {second.get('doa_deg', '?')} degrees, "
        f"contrast {second.get('contrast_db', '?')} dB, "
        f"beam switches {int(second.get('switches', 0)) - int(first.get('switches', 0))}.",
        intent="microphone.test")


async def h_audio_diagnostics(text: str, match, ctx: Context) -> Reply:
    try:
        dev = (await ctx.device.call("GET", "/device", timeout=8)).get("data") or {}
        audio = (await ctx.device.call("GET", "/audio", timeout=8)).get("data") or {}
        wake = (await ctx.device.call("GET", "/wake-word", timeout=8)).get("data") or {}
        beams = (await ctx.device.call("GET", "/microphones/beams", timeout=8)).get("data") or {}
    except Exception as exc:
        return Reply(f"Audio diagnostics failed: {exc}", intent="audio.diagnostics")
    cap = ((dev.get("audio") or {}).get("capture") or {})
    return Reply(
        f"Audio diagnostics: {cap.get('microphones', '?')} microphones at "
        f"{cap.get('rate_hz', '?')} Hz, {cap.get('digital_gain', '?')} digital gain, "
        f"noise floor {cap.get('noise_floor_dbfs', '?')} dBFS. "
        f"Speaker volume {audio.get('volume', '?')}, mic gain {audio.get('microphone_gain', '?')}. "
        f"Wake sensitivity {wake.get('sensitivity', '?')}, "
        f"beam {beams.get('selected_deg', '?')} vs direction {beams.get('doa_deg', '?')}, "
        f"peak {beams.get('peak_db', '?')} dBFS.",
        intent="audio.diagnostics")


async def h_music_duck(text: str, match, ctx: Context) -> Reply:
    current = await ctx.device.current_volume()
    if current is None:
        return Reply("I couldn't read the speaker volume.", intent="music.duck")
    target = max(5, int(current * 0.35))
    try:
        await ctx.device.set_volume(target)
    except Exception as exc:
        return Reply(f"I couldn't lower the music: {exc}", intent="music.duck")

    async def restore() -> None:
        await asyncio.sleep(30)
        try:
            await ctx.device.set_volume(current)
        except Exception as exc:
            log.debug("music duck restore failed: %s", exc)

    background(restore())
    return Reply(f"Music lowered from {current} to {target} for 30 seconds.", intent="music.duck")


async def h_volume_mute(text: str, match, ctx: Context) -> Reply:
    global _MUTED_VOLUME
    current = await ctx.device.current_volume()
    if current is None:
        return Reply("I couldn't read the speaker volume.", intent="volume.mute")
    _MUTED_VOLUME = current
    try:
        await ctx.device.set_volume(0)
    except Exception as exc:
        return Reply(f"I couldn't mute the speaker: {exc}", intent="volume.mute")
    return Reply("Speaker muted.", intent="volume.mute")


async def h_volume_unmute(text: str, match, ctx: Context) -> Reply:
    global _MUTED_VOLUME
    level = _MUTED_VOLUME if _MUTED_VOLUME is not None else 30
    try:
        await ctx.device.set_volume(level)
    except Exception as exc:
        return Reply(f"I couldn't unmute the speaker: {exc}", intent="volume.unmute")
    _MUTED_VOLUME = None
    return Reply(f"Speaker unmuted to {level}.", intent="volume.unmute")


async def h_volume_relative(text: str, match, ctx: Context) -> Reply:
    current = await ctx.device.current_volume()
    if current is None:
        return Reply("I couldn't read the speaker volume.", intent="volume.relative")
    hit = re.search(r"\d{1,3}", text)
    step = int(hit.group(0)) if hit else 10
    direction = -1 if re.search(r"\b(down|lower|decrease|less|giù|abbassa)\b", text, re.I) else 1
    target = max(0, min(100, current + step * direction))
    try:
        await ctx.device.set_volume(target)
    except Exception as exc:
        return Reply(f"I couldn't change the volume: {exc}", intent="volume.relative")
    return Reply(f"Volume {target}.", intent="volume.relative")


async def h_announce(text: str, match, ctx: Context) -> Reply:
    message = _spoken_arg(text)
    if not message:
        return Reply("What should I announce?", intent="announce")
    try:
        await ctx.device.announce(message)
    except Exception as exc:
        return Reply(f"I couldn't make that announcement: {exc}", intent="announce")
    return Reply(f"Announced: {message}", intent="announce")


async def h_note_add(text: str, match, ctx: Context) -> Reply:
    note = _spoken_arg(text)
    if not note:
        return Reply("What should I note?", intent="note.add")
    if not ctx.memory:
        return Reply("Note storage isn't available.", intent="note.add")
    reply = ctx.memory.add_note(note)
    return Reply(reply or f"Noted: {note}", intent="note.add")


async def h_note_list(text: str, match, ctx: Context) -> Reply:
    notes = list((getattr(ctx.memory, "notes", None) or []))[-5:]
    if not notes:
        return Reply("You have no notes.", intent="note.list")
    text_notes = [str(n.get("text") or "") for n in notes if isinstance(n, dict)]
    return Reply("Notes: " + "; ".join(text_notes) + ".", intent="note.list")


async def h_note_read(text: str, match, ctx: Context) -> Reply:
    notes = list((getattr(ctx.memory, "notes", None) or []))
    if not notes:
        return Reply("You have no notes.", intent="note.read")
    hit = re.search(r"\d{1,2}", text)
    idx = int(hit.group(0)) if hit else len(notes)
    idx = max(1, min(len(notes), idx))
    note = notes[idx - 1]
    return Reply(f"Note {idx}: {note.get('text', '')}.", intent="note.read")


async def h_alarm_snooze(text: str, match, ctx: Context) -> Reply:
    alarms = ctx.scheduler.list("alarm")
    if not alarms:
        return Reply("There is no alarm to snooze.", intent="alarm.snooze")
    item = alarms[0]
    ctx.scheduler.items.pop(item.id, None)
    new_due = item.due_at + 9 * 60
    await ctx.scheduler.add("alarm", new_due, item.label, item.announce,
                            item.action, item.target, item.repeat,
                            item.repeat_hour, item.repeat_minute)
    when = dt.datetime.fromtimestamp(new_due, LOCAL_TZ).strftime("%H:%M")
    return Reply(f"Alarm snoozed until {when}.", intent="alarm.snooze")


async def h_system_status(text: str, match, ctx: Context) -> Reply:
    alarms = len(ctx.scheduler.list("alarm"))
    reminders = len(ctx.scheduler.list("reminder"))
    return Reply(
        f"Olive is online. {len(oh_intents_for_status())} intents registered, "
        f"{reminders} reminders and {alarms} alarms scheduled. "
        f"Assistant model {ctx.settings.llm_model}.",
        intent="system.status")


def oh_intents_for_status() -> list:
    return INTENTS


async def h_system_diagnostics(text: str, match, ctx: Context) -> Reply:
    try:
        status = await ctx.device.call("GET", "/status", timeout=8)
        data = status.get("data") or {}
    except Exception as exc:
        return Reply(f"Diagnostics failed: {exc}", intent="system.diagnostics")
    return Reply(
        f"Speaker online for {int(data.get('uptime_seconds', 0)) // 60} minutes, "
        f"CPU {data.get('cpu_percent', '?')} percent, memory "
        f"{data.get('memory_percent', '?')} percent, "
        f"features {data.get('features_state', '?')}. "
        f"Hub has {len(INTENTS)} intents, {len(ctx.scheduler.items)} scheduled items.",
        intent="system.diagnostics")


async def h_light_toggle(text: str, match, ctx: Context) -> Reply:
    target = _spoken_arg(text) or "light"
    target = re.sub(r"\b(?:light|lights|lamp|lamps)\b", "", target,
                    flags=re.IGNORECASE).strip() or "light"
    on = bool(re.search(r"\b(on|accendi|attiva)\b", text, re.IGNORECASE))
    if re.search(r"\b(off|spegni|disattiva)\b", text, re.IGNORECASE):
        on = False
    return await home_toggle(ctx, target, on)


async def h_plug_on(text: str, match, ctx: Context) -> Reply:
    target = _spoken_arg(text) or "plug"
    return await home_toggle(ctx, target, True)


async def h_plug_off(text: str, match, ctx: Context) -> Reply:
    target = _spoken_arg(text) or "plug"
    return await home_toggle(ctx, target, False)


async def h_routine_goodnight(text: str, match, ctx: Context) -> Reply:
    changed = 0
    for device in (ctx.iot.ready if ctx.iot and ctx.iot.enabled else []):
        try:
            if await ctx.iot.set_power(device, False):
                changed += 1
        except Exception as exc:
            log.debug("goodnight power off failed: %s", exc)
    try:
        await ctx.device.set_volume(10)
    except Exception:
        pass
    return Reply(f"Goodnight. Turned off {changed} local device(s) and lowered the volume.",
                 intent="routine.goodnight")


async def h_routine_goodmorning(text: str, match, ctx: Context) -> Reply:
    changed = 0
    for device in (ctx.iot.ready if ctx.iot and ctx.iot.enabled else []):
        try:
            if await ctx.iot.set_power(device, True):
                changed += 1
        except Exception as exc:
            log.debug("goodmorning power on failed: %s", exc)
    return Reply(f"Good morning. Turned on {changed} local device(s). " +
                 (await h_briefing(text, match, ctx)).text, intent="routine.goodmorning")


INTENTS: list[Intent] = [
    Intent("help", compile_all(r"\b(help|what can you do|cosa sai fare|aiuto)\b"), h_help),
    Intent("iot.scan", compile_all(
        r"\b(?:find|scan (?:for|the)?|look for|discover)\b.*\b(?:new\s+)?(?:devices?|lights?|bulbs?|things)\b",
        r"\b(?:scan|search)\b.*\b(?:network|lan|local network)\b",
        r"\b(?:cerca|trova|scansiona)\b.*\b(?:dispositivi|luci|lampade)\b",
    ), h_iot_scan),
    Intent("iot.scan.status", compile_all(
        r"\b(?:what did you find|any new devices|scan results|what.?s new)\b",
    ), h_iot_scan_status),
    Intent("briefing", compile_all(
        r"\b(?:morning briefing|daily briefing|give me my briefing|brief me|"
        r"riassunto della giornata)\b",
    ), h_briefing),
    Intent("joke", compile_all(
        r"\b(?:tell me|say|give me)\s+(?:a|another|one)\s+joke\b",
    ), h_joke),
    Intent("quote", compile_all(
        r"\b(?:quote of the day|give me a quote|tell me something inspiring)\b",
    ), h_quote),
    Intent("voice.clone.status", compile_all(
        r"\b(?:clone|voice clone)\s+status\b",
        r"^\s*clone\s+mode\s+status\s*$",
    ), h_clone_status),
    Intent("voice.clone", compile_all(
        r"^\s*(?:alexa\s+)?clone(?:\s+my\s+voice)?\s*$",
        r"^\s*(?:alexa\s+)?(?:activate|start|begin)\s+clone\s+mode\s*$",
        r"^\s*(?:alexa\s+)?clone\s+mode\s*$",
        r"\bclone\s+my\s+voice\b",
        r"\bstart\s+cloning\b",
        # Whisper tiny can hear "clone" as "cold"/"clod" with some accents.
        r"^\s*(?:alexa|hey\s+olive)\s+(?:cold|clod|clown|cone|cloned|cloan)\s*$",
    ), h_clone_start),
    Intent("voice.list", compile_all(
        r"\b(?:list|show|what are)\s+(?:my\s+)?(?:voices|voice personas)\b",
    ), h_voice_list),
    Intent("voice.use", compile_all(
        r"\b(?:speak|talk)\s+like\s+(?P<name>.+)$",
        r"\buse\s+(?P<name>.+?)\s+voice\b",
    ), h_voice_use),
    Intent("alarm.cancel", compile_all(r"\b(?:cancel|stop|delete|annulla|ferma)\b.*\balarms?\b",
                                       r"\balarms?\b.*\b(?:cancel|off|annulla)\b"), h_schedule_cancel),
    Intent("alarm.list", compile_all(
        r"\b(?:what|which|list|any|how many|do i have|are there)\b.*\balarms?\b",
        r"\balarms?\b.*\b(?:set|scheduled|do i have)\b",
    ), h_schedule_list),
    Intent(
        "alarm.set",
        compile_all(
            r"\b(?:set|create|make|imposta|metti)\b.*?\balarm\b",
            r"\bwake me up\b",
            r"\bwake me\b.*\b(?:tomorrow|tonight|morning|at\s+\d|domani|stasera)\b",
            r"\balarm\b.*\b(?:tomorrow|tonight|morning|at\s+\d|domani|stasera)\b",
            r"\bsvegliami\b",
        ),
        h_alarm_set,
    ),
    Intent("reminder.cancel", compile_all(r"\b(?:cancel|delete|remove|annulla)\b.*\breminders?\b"), h_schedule_cancel),
    Intent("reminder.list", compile_all(
        r"\b(?:what|which|list|any|how many|do i have|are there)\b.*\breminders?\b",
        r"\breminders?\b.*\b(?:set|scheduled|do i have)\b",
    ), h_schedule_list),
    Intent(
        "reminder.set",
        compile_all(
            r"\bremind me\b(?P<what>.*)$",
            r"\bricordami\b(?P<what>.*)$",
            r"\b(?:set|create|add|make)\b\s+(?:a\s+|an\s+|my\s+)?reminders?\b(?P<what>.*)$",
            r"\b(?:set|create|add|make)\b\s+(?:a\s+|an\s+|my\s+)?promemoria\b(?P<what>.*)$",
            r"\bremember to\b(?P<what>.*)$",
        ),
        h_reminder_set,
        ["remind me in 10 minutes to check the oven"],
    ),
    Intent("timer.cancel", compile_all(r"\b(cancel|stop|annulla|ferma)\b.*\btimers?\b"), h_timer_cancel),
    Intent("timer.list", compile_all(r"\b(list|what|which|quali|elenca)\b.*\btimers?\b"), h_timer_list),
    Intent(
        "timer.set",
        compile_all(
            r"\b(?:set|start|imposta|avvia)\b.*?(?P<amount>" + NUMBER_PATTERN + r")\s*(?P<unit>" + TIMER_UNIT_PATTERN + r")\b"
            r"(?:.*?\b(?:for|per|labelled|labeled|chiamato|called)\s+(?P<label>.+))?",
            r"\btimer\b\s*(?:for|per|of|da|di)?\s*(?P<amount>" + NUMBER_PATTERN + r")\s*(?P<unit>" + TIMER_UNIT_PATTERN + r")\b"
            r"(?:.*?\b(?:for|per|labelled|labeled|chiamato|called)\s+(?P<label>.+))?",
            r"^\s*(?:set|start|create|imposta|avvia)?\s*(?:a\s+|an\s+)?timer\s*$",
        ),
        h_timer_set,
        ["timer for five minutes"],
    ),
    Intent(
        "stop",
        compile_all(
            r"\b(?:stop|silence|quiet|shut up|zittisciti)\b.*\b(?:ringing|beeping|noise|beep)\b",
            r"\b(?:please\s+)?(?:stop it|stop that|stop now|shut up|be quiet|silence)\b",
        ),
        h_stop_everything,
    ),
    Intent("music.stop", compile_all(r"\b(stop|ferma|spegni)\b.*\b(music|musica|radio|playback)\b"), h_stop_music),
    Intent(
        "speaker.reboot",
        compile_all(
            r"\b(?:reboot|restart|riavvia)\b.*\b(?:speaker|echo|olive|device|cassa|altoparlante)\b",
        ),
        h_speaker_reboot,
    ),
    Intent("music.pause", compile_all(r"\b(pause|pausa|metti in pausa)\b"), h_pause_music),
    Intent("music.resume", compile_all(r"\b(resume|riprendi|continua)\b.*\b(music|musica|radio)\b|\bresume\b"), h_resume_music),
    Intent("music.status", compile_all(r"\b(what'?s playing|now playing|che cosa (suona|stai suonando)|cosa stai suonando)\b"), h_now_playing),
    Intent(
        "music.playlist",
        compile_all(
            r"\b(?:make|create|build|start|fammi|crea|prepara)\b.*\bplaylist\b(?:\s+(?:for|of|di|per|da)\s+(?P<query>.+))?",
            r"^\s*playlist\b(?:\s+(?:for|of|di|per|da)\s+(?P<query>.+))?",
        ),
        h_playlist,
    ),
    Intent(
        "music.next",
        compile_all(
            r"\b(?:next|skip|prossima|prossimo|avanti)\b.*\b(?:station|song|track|radio|canzone|stazione|brano)\b",
            r"^\s*next\s*$",
        ),
        h_next,
    ),
    Intent("youtube.play", compile_all(
        r"\b(?:play|watch|put on|riproduci)\b.*\b(?:on\s+)?(?:youtube|yt)\b",
        r"\b(?:show me|find|watch|play|put on|mostrami|cerca)\b.*\b(?:on\s+)?(?:youtube|yt)\b",
        # A "music video" request means YouTube even when the word is omitted.
        r"\b(?:play|show me|watch|find|put on|riproduci)\b.*\b(?:music\s+)?videos?\b",
    ), h_youtube),
    Intent(
        "tv.play",
        compile_all(
            r"\b(?:play|cast|put on|stream|metti|riproduci|trasmetti)\b.*\b(?:on|to|sulla|sul|alla|al)\s+"
            r"(?:the\s+)?(?:tv|telly|television|tele|televisore|chromecast)\b",
            r"\b(?:cast|trasmetti)\b.*\b(?:tv|television|tele|chromecast)\b",
        ),
        h_tv_play,
    ),
    Intent(
        "tv.on",
        compile_all(
            r"\b(?:turn|switch|power)\s+on\b.*\b(?:tv|telly|television|tele|televisore|chromecast)\b",
            r"\b(?:tv|telly|television|tele)\b.*\b(?:on|wake)\b",
            r"\b(?:accendi|riaccendi)\b.*\b(?:tv|tele|televisore)\b",
            r"\bwake\b.*\b(?:tv|television)\b",
        ),
        h_tv_on,
    ),
    Intent(
        "tv.off",
        compile_all(
            r"\b(?:turn|switch|power)\s+off\b.*\b(?:tv|telly|television|tele|televisore|chromecast)\b",
            r"\b(?:spegni|spegnete)\b.*\b(?:tv|tele|televisore)\b",
            r"\b(?:tv|television)\b.*\b(?:off|standby)\b",
        ),
        h_tv_off,
    ),
    Intent(
        "tv.pause",
        compile_all(
            r"\bpause\b.*\b(?:tv|television|chromecast)\b",
            r"\b(?:tv|television)\b.*\bpause\b",
            r"\bpausa\b.*\b(?:tv|tele)\b",
        ),
        h_tv_pause,
    ),
    Intent(
        "tv.resume",
        compile_all(
            r"\b(?:resume|continue|unpause)\b.*\b(?:tv|television|chromecast)\b",
            r"\b(?:riprendi|continua)\b.*\b(?:tv|tele)\b",
        ),
        h_tv_resume,
    ),
    Intent(
        "tv.stop",
        compile_all(
            r"\b(?:stop|quit|kill|close)\b.*\b(?:tv|television|chromecast)\b",
            r"\b(?:ferma|chiudi)\b.*\b(?:tv|tele)\b",
        ),
        h_tv_stop,
    ),
    Intent(
        "tv.volume",
        compile_all(
            r"\b(?:volume|mute|unmute)\b.*\b(?:tv|television|tele|chromecast)\b",
            r"\b(?:tv|television)\b.*\b(?:volume|mute)\b",
            r"\b(?:alza|abbassa|muta)\b.*\b(?:tv|tele|televisore)\b",
        ),
        h_tv_volume,
    ),
    Intent(
        "tv.status",
        compile_all(
            r"\bwhat(?:'s| is)\b.*\b(?:on the tv|on tv|playing on the tv)\b",
            r"\b(?:tv|television|chromecast)\b.*\b(?:status|state|playing)\b",
        ),
        h_tv_status,
    ),
    Intent(
        "tv.app",
        compile_all(
            r"\b(?:open|launch|start|apri|avvia)\b\s+(?P<app>youtube|netflix|prime video|amazon prime|"
            r"disney(?:\s*plus)?|spotify|plex|kodi|vlc|browser)\b",
            r"\b(?:open|launch|start|apri)\b\s+(?P<app>.+?)\s+on\s+(?:the\s+)?(?:tv|television)\b",
        ),
        h_tv_launch,
    ),
    Intent(
        "radio.ma",
        compile_all(
            r"\b(?:play|put on|metti|riproduci|accendi)\b.*\bmusic\s*assistant\b",
            r"\b(?:play|put on|metti|riproduci)\b.*\b(?:everywhere|whole\s+house|"
            r"multi-?room|tutta\s+la\s+casa|in\s+ogni\s+stanza)\b",
        ),
        h_radio,
    ),
    Intent(
        "radio",
        compile_all(
            r"\b(?:play|put on|metti|riproduci|accendi)\b.*?\b(?:radio|station|stazione)\b(?:\s+(?P<station>.+))?$",
            r"\b(?:play|put on|metti|riproduci)\s+(?P<station>radio\s+.+|.+?\s+radio)$",
            r"\b(?:play|put on|metti|riproduci)\s+(?P<station>music|musica|some music|della musica)\b",
        ),
        h_radio,
    ),
    Intent(
        "music.mood",
        compile_all(
            r"\b(?:play|put on|metti|riproduci)\b.*\b(?:music|musica)\b",
            r"\bplay\b.*\b(?:rainy ?day|chill|relax(?:ing)?|party|focus|study|sleep|workout|"
            r"happy|sad|jazz|ambient|lofi|lo-fi)\b",
        ),
        h_music_mood,
    ),
    Intent(
        "news",
        compile_all(
            r"\b(?:news|headlines|notizie|ultime notizie)\b",
            r"\bwhat'?s happening\b",
        ),
        h_news,
    ),
    Intent("places", compile_all(
        r"\b(?:find|look for|search|show me|where are|any|trova|cerca)\b.*"
        r"\b(?:restaurants?|pizzerias?|pizza|places to eat|trattorie|ristoranti)\b",
        r"\b(?:italian|sushi|vegan|vegetarian|seafood)\b.*"
        r"\b(?:restaurants?|places to eat|ristoranti)\b",
    ), h_places),
    Intent("flights", compile_all(
        r"\b(?:find|look for|search|show me|check)\b.*\bflights?\b",
        r"\bflights?\b.*\b(?:to|for|per)\b.*\b(?:rome|roma|next week|tomorrow)\b",
    ), h_flights),
    Intent("location", compile_all(
        r"\bwhat'?s?\s+(?:my|your|the)\s+(?:current\s+)?location\b",
        r"\bwhere\s+(?:am\s+i|are\s+we|are\s+you)\b",
        r"\bmy\s+(?:current\s+)?(?:location|position)\b",
        r"\bqual'?\s*[èe]\s+la\s+mia\s+posizione\b",
        r"\bdove\s+(?:siamo|sono)\b",
    ), h_location),
    Intent("trains", compile_all(
        r"\btrains?\b",
        r"\btrain\s+(?:times?|tickets?|costs?|prices?|fares?|schedule)\b",
        r"\b(?:orari|treni|biglietti)\b.*\btren\b",
    ), h_trains),
    Intent("wakeword.status", compile_all(
        r"\b(?:wake[\s-]?word|wake word)\s+(?:status|state|health|is (?:ok|ready))\b",
        r"\b(?:status|how is)\s+(?:the\s+)?wake[\s-]?word\b",
    ), h_wakeword_status),
    Intent("wakeword.set", compile_all(
        r"\bset\s+wake[\s-]?word\s+sensitivity\s+to\s+\d{1,3}\b",
        r"\bwake[\s-]?word\s+sensitivity\s+\d{1,3}\b",
    ), h_wakeword_set),
    Intent("wakeword.sensitivity", compile_all(
        r"\bwake[\s-]?word\s+sensitivity\b",
        r"\bhow\s+sensitive\s+is\s+(?:the\s+)?wake[\s-]?word\b",
    ), h_wakeword_sensitivity),
    Intent("wakeword.list", compile_all(
        r"\blist\s+(?:available\s+)?wake[\s-]?word\s+models?\b",
        r"\bwake[\s-]?word\s+models?\b",
    ), h_wakeword_list),
    Intent("wakeword.test", compile_all(
        r"\b(?:test|run|check)\s+(?:the\s+)?wake[\s-]?word(?:\s+test)?\b",
        r"\bwake[\s-]?word\s+test\b",
    ), h_wakeword_test),
    Intent("microphone.status", compile_all(
        r"\b(?:microphone|mic)\s+(?:status|state|level|gain|is (?:on|off|muted))\b",
        r"\bstatus\s+of\s+(?:the\s+)?(?:microphone|mic)\b",
    ), h_microphone_status),
    Intent("microphone.test", compile_all(
        r"\b(?:test|check)\s+(?:the\s+)?(?:microphone|mic)\b",
        r"\b(?:microphone|mic)\s+(?:test|check)\b",
    ), h_microphone_test),
    Intent("audio.diagnostics", compile_all(
        r"\b(?:audio|sound)\s+diagnostics?\b",
        r"\bdiagnose\s+(?:the\s+)?(?:audio|sound|microphone)\b",
    ), h_audio_diagnostics),
    Intent("music.duck", compile_all(
        r"\b(?:duck|lower|reduce)\s+(?:the\s+)?music\b",
        r"\bturn\s+down\s+(?:the\s+)?music\b",
    ), h_music_duck),
    Intent("volume.mute", compile_all(
        r"\b(?:mute|silence)\s+(?:the\s+)?(?:speaker|volume|olive)\b",
        r"\bmute\b",
    ), h_volume_mute),
    Intent("volume.unmute", compile_all(
        r"\b(?:unmute|un-mute|restore)\s+(?:the\s+)?(?:speaker|volume|olive)\b",
        r"\bunmute\b",
    ), h_volume_unmute),
    Intent("volume.relative", compile_all(
        r"\b(?:volume|turn)\s+(?:up|down|higher|lower)\b",
        r"\b(?:increase|decrease|raise|lower)\s+(?:the\s+)?volume\b",
        r"\bvolume\s+(?:plus|minus)\b",
    ), h_volume_relative),
    Intent("announce", compile_all(
        r"^\s*announce\s+.+$",
        r"^\s*say\s+(?!the\s+time|the\s+date|hello|hi)\s*.+$",
    ), h_announce),
    Intent("note.add", compile_all(
        r"^\s*(?:note|remember|write down)\s+(?:that\s+)?(.+)$",
        r"\b(?:make|take|add)\s+a\s+note\b",
    ), h_note_add),
    Intent("note.list", compile_all(
        r"\b(?:list|show|what are|read)\s+(?:my\s+)?notes?\b",
        r"\bnotes\?\s*$",
    ), h_note_list),
    Intent("note.read", compile_all(
        r"\bread\s+(?:note|the note)\b",
        r"\b(?:note|notes)\s+\d{1,2}\b",
    ), h_note_read),
    Intent("alarm.snooze", compile_all(
        r"\bsnooze\b",
        r"\b(?:alarm|timer)\b.*\bsnooze\b",
    ), h_alarm_snooze),
    Intent("system.status", compile_all(
        r"\b(?:system|olive)\s+status\b",
        r"\bhow\s+is\s+(?:olive|the system)\b",
    ), h_system_status),
    Intent("system.diagnostics", compile_all(
        r"\bsystem\s+diagnostics\b",
        r"\brun\s+(?:system\s+)?diagnostics\b",
    ), h_system_diagnostics),
    Intent("light.toggle", compile_all(
        r"\b(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?(?:light|lights|lamp|lamps)\b",
        r"\b(?:accendi|spegni)\s+(?:la\s+|le\s+)?(?:luce|luci|lampada|lampade)\b",
    ), h_light_toggle),
    Intent("plug.on", compile_all(
        r"\b(?:turn|switch)\s+on\s+(?:the\s+)?(?:plug|outlet|socket)\b",
        r"\bplug\s+on\b",
    ), h_plug_on),
    Intent("plug.off", compile_all(
        r"\b(?:turn|switch)\s+off\s+(?:the\s+)?(?:plug|outlet|socket)\b",
        r"\bplug\s+off\b",
    ), h_plug_off),
    Intent("routine.goodnight", compile_all(
        r"\bgood\s?night\b",
        r"\b(?:run|start)\s+(?:the\s+)?goodnight\s+routine\b",
    ), h_routine_goodnight),
    Intent("routine.goodmorning", compile_all(
        r"\bgood\s?morning\b",
        r"\b(?:run|start)\s+(?:the\s+)?good\s?morning\s+routine\b",
    ), h_routine_goodmorning),
    Intent(
        "calendar.add",
        compile_all(
            r"\b(?:add|create|put)\b\s+(?!a\s+(?:reminder|event|appointment)\b)(?P<what>.+?)\s+(?:to|on|in)\s+(?:my\s+)?(?:calendar|agenda)\b",
            r"\b(?:add|create|put|schedule|write)\b.*?\b(?:event|appointment|reminder)\b"
            r"(?:\s+(?:to|on|in)\s+(?:my\s+)?(?:calendar|agenda))?\s*(?P<what>.+)$",
            r"\b(?:add|create|put)\b.*?\b(?:to|on|in)\s+(?:my\s+)?(?:calendar|agenda)\b\s*(?P<what>.+)$",
            r"\b(?:aggiungi|crea|metti)\b.*?\b(?:calendario|agenda)\b\s*(?P<what>.+)?$",
        ),
        h_calendar_add,
    ),
    Intent(
        "calendar",
        compile_all(
            r"\b(?:what'?s|what is|anything|what do i have)\b.*\b(?:calendar|schedule|agenda|appointments?)\b",
            r"\b(?:calendar|schedule|agenda)\b.*\b(?:today|tomorrow)\b",
        ),
        h_calendar,
    ),
    Intent("whatsapp.send", compile_all(
        r"\b(?:send|text|message)\s+(?:a\s+)?whatsapp\s+(?:message|msg)?\s*to\s+"
        r"(?P<contact>.+?)\s*,?\s+(?:saying|that says|:)\s*(?P<body>.+)$",
        r"\b(?:text|message)\s+(?P<contact>.+?)\s+on\s+whatsapp\s+"
        r"(?:saying|that says|:)?\s*(?P<body>.+)$",
        r"\bwhatsapp\s+to\s+(?P<contact>.+?)\s+saying\s+(?P<body>.+)$",
    ), h_whatsapp_send),
    Intent("whatsapp.status", compile_all(
        r"\bwhatsapp status\b", r"\bis whatsapp connected\b",
        r"\bwhatsapp (?:is )?(?:connected|ready|online)\b",
    ), h_whatsapp_status),
    Intent("whatsapp.contacts", compile_all(
        r"\b(?:show|list|what are|my)\s+(?:my\s+)?whatsapp contacts\b",
        r"^\s*whatsapp contacts\s*$",
        r"\bfind\s+.+\s+on\s+whatsapp\b",
    ), h_whatsapp_contacts),
    Intent(
        "messenger",
        compile_all(
            r"\b(?:send|invia)\b.*\b(?:message|telegram|messaggio)\b",
            r"\bmessage\b.*\bsaying\b",
            r"\btelegram\b.*\bsaying\b",
        ),
        h_messenger,
    ),
    Intent("shopping.remove", compile_all(
        r"\b(?:remove|delete|cross off|take)\b(?P<item>.+?)\b(?:from|off)\s+(?:my\s+)?shopping\s+list\b",
    ), h_shopping_remove),
    Intent("shopping.add", compile_all(
        r"\b(?:add|put)\b(?P<item>.+?)\b(?:to|on|in)\s+(?:my\s+)?shopping\s+list\b",
    ), h_shopping_add),
    Intent("shopping.list", compile_all(
        r"\b(?:clear|empty|wipe)\s+(?:my\s+)?shopping\s+list\b",
        r"\b(?:what'?s on|show|list|read|what is on)\b.*\bshopping\s+list\b",
        r"^\s*shopping\s+list\s*$",
    ), h_shopping_list),
    Intent(
        "facts.remember",
        compile_all(
            r"\b(?:remember|keep in mind|ricorda)\b.*\bmy\b",
            r"\bmy\s+\w+(?:\s+\w+)?\s+(?:is|are)\b",
            r"\bi\s+(?:like|love|prefer)\b",
        ),
        h_personal_fact,
    ),
    Intent(
        "facts.recall",
        compile_all(
            r"\bwhat(?:'s| is)\s+my\s+\w+",
            r"\bdo you (?:know|remember)\s+my\s+\w+",
            r"\bwhen is my\b",
        ),
        h_personal_fact,
    ),
    Intent(
        "facts.list",
        compile_all(
            r"\bwhat do you (?:remember|know)(?: about me)?\b",
            r"\bwhat have you (?:remembered|stored)\b",
            r"\bcosa (?:ricordi|sai) (?:di me)?\b",
        ),
        h_personal_fact,
    ),
    Intent(
        "volume.set",
        compile_all(
            r"\b(?:alza|abbassa|turn (?:up|down)|raise|lower|louder|quieter)\b.*?\bvolume\b.*?\b(?P<step>\d{1,3})\b",
            r"\b(?:alza|abbassa|turn (?:up|down)|raise|lower)\b.*?\bvolume\b",
            r"\bvolume\b.*?\b(?P<direction>up|down|su|giu|giù)\b(?:\s*(?P<step>\d{1,3}))?",
            r"\b(?:louder|piu forte|più forte)\b",
            r"\b(?:quieter|softer|piu piano|più piano)\b",
            r"\bturn (?:it )?(?:up|down)\b",
            r"\b(?:set|imposta|metti)\b.*?\bvolume\b.*?(?P<level>\d{1,3})\b",
            r"\bvolume\b.*?(?P<level>\d{1,3})\b",
        ),
        h_volume,
    ),
    Intent("volume.get", compile_all(r"\b(what'?s the volume|quanto e il volume|che volume c'e)\b"), h_volume),
    Intent("weather", compile_all(r"\b(weather|forecast|temperature|che tempo|meteo|piove)\b"), h_weather),
    Intent("time", compile_all(r"\b(what time is it|what'?s the time|che ore sono|che ora e|che ora è|current time)\b"), h_time),
    Intent("date", compile_all(r"\b(what'?s the date|what date|what day is it|today'?s date|che giorno|che data)\b"), h_date),
    Intent("coin", compile_all(r"\b(flip a coin|coin flip|testa o croce)\b"), h_coin),
    Intent("dice", compile_all(r"\b(roll a (die|dice|d20)|tira il dado)\b"), h_dice),
    Intent(
        "light.brightness",
        compile_all(
            r"\b(?:set|change|imposta|metti|porta)\b(?P<target>[^0-9]*?\b(?:light|lights|lamp|lamps|bulb|bulbs|lampada|lampadina|lampade|lightstrip)\b[^0-9]*?)\b(?:to|at|a|al)\b\s*(?P<level>\d{1,3})\s*(?:%|percent|per cent)?$",
            r"\b(?:set|change|imposta|metti)\b.*?\b(?:brightness|luminosita|luminosità)\b.*?(?P<level>\d{1,3})\b",
            r"\b(?:dim|brighten)\b\s+(?P<target>.+)$",
        ),
        h_light_brightness,
    ),
    Intent("ha.scene", compile_all(r"\b(?:activate|run|start|attiva|esegui)\b.*\bscene\b\s*(?P<target>.+)?$"), h_ha_scene),
    Intent(
        "ha.off",
        compile_all(
            r"\b(?:turn off|switch off|shut off|spegni|disattiva)\b\s+(?P<target>.+)$",
            r"\b(?:turn|switch|shut)\b\s+(?:(?:it|that)\s+)?(?P<target>.+?)\s+(?:off|out)$",
        ),
        h_ha_off,
    ),
    Intent(
        "routine.sunset",
        compile_all(
            r"\b(?:turn|switch)\s+on\b\s+(?P<target>.+?)\s+"
            r"(?P<amount>" + NUMBER_PATTERN + r")\s*(?P<unit>" + TIMER_UNIT_PATTERN + r")\s+"
            r"before\s+(?:the\s+)?sunset\b",
            r"\b(?:accendi)\b\s+(?P<target>.+?)\s+"
            r"(?P<amount>" + NUMBER_PATTERN + r")\s*(?P<unit>" + TIMER_UNIT_PATTERN + r")\s+"
            r"prima\s+del\s+tramonto\b",
        ),
        h_routine_sunset,
    ),
    Intent(
        "ha.on",
        compile_all(
            r"\b(?:turn on|switch on|accendi|attiva)\b\s+(?P<target>.+)$",
            r"\b(?:turn|switch)\b\s+(?:(?:it|that)\s+)?(?P<target>.+?)\s+on$",
        ),
        h_ha_on,
    ),
    Intent("ha.state", compile_all(r"\b(?:is|e|è)\b\s+(?P<target>.+?)\s+\b(?:on|off|acces[oa]|spent[oa])\b$"), h_ha_state),
    Intent("currency.convert", compile_all(
        r"\b(?:convert|how much is|what'?s|quanto fa)\b.*?\d.*?"
        r"\b(?:dollars?|euros?|pounds?|sterling|yen|francs?)\b.*?\b(?:in|to)\b",
    ), h_currency),
    Intent("unit.convert", compile_all(
        r"\b(?:convert|how many|how much)\b.*?\d+\s*[a-zA-Z]+\s+(?:in|to|into)\s+[a-zA-Z]+\b",
    ), h_units),
    Intent("math", compile_all(
        r"\b(?:what is|what'?s|calculate|how much is|quanto fa|calcola)\b.*?\d",
        # Bare expressions such as "2 plus 2" or "12 times 3" are safe to route
        # here; the AST whitelist in safe_math rejects anything else.
        r"^\s*\d[\d\s\.\+\-\*/%\(\)]*(?:(?:plus|minus|times|divided\s+by)\s+\d[\d\s\.\+\-\*/%\(\)]*)+\s*$",
        r"^\s*\d+(?:\.\d+)?\s*(?:%|per\s*cent(?:o)?)\s+(?:of|di)\s+\d+(?:\.\d+)?\s*$",
    ), h_math),
    Intent(
        "knowledge",
        compile_all(r"\b(?:who is|what is|tell me about|define|where is|how do|chi e|chi è|cos'?e|cos'?è)\b"),
        h_knowledge,
    ),
]


# =====================================================================
# USER-EDITABLE INTENTS (hot-reloaded, no hub restart needed)
# =====================================================================
# Stored in custom_intents.json next to this file:
#   {
#     "version": 1,
#     "disabled": ["joke", "quote"],          # built-in intent names to mute
#     "intents": [
#       {"name": "custom.coffee", "enabled": true,
#        "patterns": ["make coffee", "prepare the coffee"],
#        "reply": "Starting the coffee machine.", "speak": "pipeline"}
#     ]
#   }
# Patterns are regular expressions (case-insensitive, applied with search).
CUSTOM_INTENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "custom_intents.json")
CUSTOM_INTENTS: list[Intent] = []
DISABLED_INTENTS: set[str] = set()


def _make_custom_handler(spec: dict) -> Handler:
    async def handler(_text: str, _hit, _ctx: Context) -> Reply:
        reply = str(spec.get("reply") or "Okay.")
        for token, value in (("{text}", _text),):
            reply = reply.replace(token, value)
        return Reply(reply, speak=str(spec.get("speak") or "pipeline"),
                     intent=str(spec.get("name") or "custom"))
    return handler


def apply_intents_config(data: dict | None) -> tuple[list[str], list[str]]:
    """Apply an intents config dict in-memory. Returns (errors, active names)."""
    global CUSTOM_INTENTS, DISABLED_INTENTS
    errors: list[str] = []
    entries: list[Intent] = []
    if data is None:
        data = {"intents": [], "disabled": []}
    if not isinstance(data, dict):
        return (["intents config must be an object"], [])

    version = data.get("version", 1)
    if not isinstance(version, int) or version < 1:
        errors.append("version must be a positive integer")
    disabled_raw = data.get("disabled") or []
    if not isinstance(disabled_raw, list):
        return (["disabled must be a list"], [])
    disabled = {str(x) for x in disabled_raw if isinstance(x, str)}
    builtin_names = {i.name.lower() for i in INTENTS}
    seen: set[str] = set()
    intents_raw = data.get("intents") or []
    if not isinstance(intents_raw, list):
        return (["intents must be a list"], [])

    for item in intents_raw:
        if not isinstance(item, dict):
            errors.append("skipped a non-object intent entry")
            continue
        name = str(item.get("name") or "").strip()
        if not name or not re.fullmatch(r"[a-z0-9_.-]+", name, re.IGNORECASE):
            errors.append(f"invalid intent name: {name!r}")
            continue
        if not item.get("enabled", True):
            continue
        key = name.lower()
        if key in seen:
            errors.append(f"duplicate custom intent name: {name}")
            continue
        seen.add(key)
        if key in builtin_names:
            errors.append(f"intent {name} collides with a built-in intent")
            continue
        speak = str(item.get("speak") or "pipeline")
        if speak not in ("pipeline", "device", "none"):
            errors.append(f"intent {name}: invalid speak mode {speak!r}")
            continue
        patterns = [str(p) for p in (item.get("patterns") or []) if isinstance(p, str) and p.strip()]
        if not patterns:
            errors.append(f"intent {name}: no patterns")
            continue
        try:
            compiled = compile_all(*patterns)
        except re.error as exc:
            errors.append(f"intent {name}: bad regex: {exc}")
            continue
        entries.append(Intent(name, compiled, _make_custom_handler(item)))
    if errors:
        return errors, []
    CUSTOM_INTENTS = entries
    DISABLED_INTENTS = disabled
    return errors, [i.name for i in entries]


def reload_custom_intents() -> tuple[list[str], list[str]]:
    """Re-read custom_intents.json from disk and apply it in-memory."""
    try:
        with open(CUSTOM_INTENTS_FILE, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return (["custom_intents.json is missing; keeping the current configuration"], [])
    except Exception as exc:
        return ([f"cannot read custom_intents.json: {exc}"], [])
    return apply_intents_config(data)


def intents_config() -> dict:
    """Expose built-in + custom intents and their enabled state for the UI."""
    builtins = [{"name": i.name, "enabled": i.name not in DISABLED_INTENTS,
                 "custom": False, "examples": i.examples[:3]} for i in INTENTS]
    customs = []
    try:
        with open(CUSTOM_INTENTS_FILE, encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except Exception:
        raw = {}
    raw_by_name = {str(x.get("name")): x for x in (raw.get("intents") or [])
                   if isinstance(x, dict)}
    for i in CUSTOM_INTENTS:
        spec = raw_by_name.get(i.name, {})
        customs.append({"name": i.name, "enabled": True, "custom": True,
                        "patterns": [p.pattern for p in i.patterns],
                        "reply": str(spec.get("reply") or ""),
                        "speak": str(spec.get("speak") or "pipeline"),
                        "examples": []})
    return {"builtins": builtins, "customs": customs,
            "disabled": sorted(DISABLED_INTENTS)}


# Apply once at import; the HTTP endpoint can hot-reload later.
reload_custom_intents()


def friendly_error(exc: Exception) -> str:
    message = str(exc)
    if "LibreEcho login failed" in message:
        return "I can't log in to the speaker. Please check the speaker password."
    if "LibreEcho" in message:
        return "I couldn't reach the speaker just now."
    if message.startswith("HA "):
        return "Home Assistant didn't answer."
    log.debug("unmapped error: %s", message)
    return "Sorry, that didn't work."


NON_MUSIC_WORDS = {"game", "games", "chess", "movie", "film", "video", "audiobook", "story"}

# Spoken home-control phrases that reached the LLM because no backend matched.
# The LLM must never answer these on its own: it would claim it flipped a switch.
HOME_CONTROL = re.compile(
    r"\b(?:turn|switch|shut|spegni|accendi|disattiva|attiva|dim|brighten)\b"
    r".*\b(?:light|lights|lamp|lamps|bulb|bulbs|plug|socket|switch|"
    r"luce|luci|lampada|lampade|presa)\b",
    re.IGNORECASE,
)

# "could you clarify?" while a question is pending -> repeat the question.
CLARIFY_ASK = re.compile(
    r"\b(?:could you (?:clarify|explain|repeat)|can you (?:clarify|explain|repeat)|"
    r"what do you mean|i (?:don'?t|do not) understand|non ho capito|puoi (?:spiegare|ripetere))\b",
    re.IGNORECASE,
)

# A clarification answer is short. Anything that starts like a fresh question
# or command is a new turn, so an old pending question must step aside.
NEW_TURN = re.compile(
    r"^(?:who|what|when|where|why|how|which|is|are|was|were|do|does|did|"
    r"can|could|would|will|tell|play|set|remind|turn|open|launch|add|start|"
    r"stop|cancel|call|send|translate)\b",
    re.IGNORECASE,
)

# Courtesy questions do not need an answer; never hold the mic open for them.
COURTESY_QUESTION = re.compile(
    r"\b(?:anything else|something else|can i help|may i help|how can i help|"
    r"let me know if)\b",
    re.IGNORECASE,
)

# A short yes/no answering a clarification the previous turn asked for.
AFFIRMATIVE = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|please|ok|okay|do it|go ahead|that'?s right|"
    r"correct|si|sì|certo|vai|fallo)\b",
    re.IGNORECASE,
)
NEGATIVE = re.compile(
    r"^(?:no|nope|nah|not now|don'?t|do not|no thanks|no thank you|"
    r"non ora|no grazie)\b",
    re.IGNORECASE,
)

# Intents that a "yes" is allowed to replay from the original sentence.
REPLAY_INTENTS = ("radio", "music.", "tv.", "ha.", "iot.", "light.", "timer.",
                  "alarm.", "reminder.", "volume.")


class Router:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        # Wired up by make_app once the cloud helper exists.
        self.llm_answer: Optional[Callable[..., Awaitable[Optional[str]]]] = None

    async def _run(self, name: str, handler: Handler, text: str, hit) -> Reply:
        try:
            reply = await handler(text, hit, self.ctx)
        except Exception as exc:
            log.exception("intent %s failed", name)
            return Reply(friendly_error(exc), intent=name)
        reply.intent = reply.intent or name
        return reply

    def _supersedes_pending(self, clean: str, kind: str) -> bool:
        """A new explicit command must never be eaten by an old question."""
        if any(intent.match(clean) for intent in INTENTS):
            return True
        if kind == "music":
            return bool(PLAY_VERB.match(clean))
        if kind == "clarify":
            return len(clean.split()) > 8 or bool(NEW_TURN.match(clean))
        return False

    async def _resolve_pending(self, text: str, pending: dict[str, Any]) -> Optional[Reply]:
        """Answer a clarification the previous turn asked for."""
        kind = pending.get("kind")
        if kind == "music":
            return await h_music_mood(text, None, self.ctx)
        if kind == "clarify":
            question = str(pending.get("question") or "")
            original = str(pending.get("original") or "")
        if kind == "clarify":
            question = str(pending.get("question") or "")
            original = str(pending.get("original") or "")
            # If the short answer is itself a command or a station name
            # ("and BBC one extra"), replay it against the routers. Never let
            # the pending question swallow an actionable sentence.
            if original:
                async def replay(sentence: str) -> Optional[Reply]:
                    self.ctx.pending = None      # avoid recursing into this branch
                    try:
                        return await self.route(sentence)
                    finally:
                        self.ctx.pending = pending

                for sentence in (text, original if AFFIRMATIVE.match(text) else ""):
                    if not sentence:
                        continue
                    answer_reply = await replay(sentence)
                    if (answer_reply is not None and answer_reply.text
                            and str(answer_reply.intent or "").startswith(REPLAY_INTENTS)):
                        log.info("clarification replay %r -> intent=%s",
                                 sentence[:60], answer_reply.intent)
                        return answer_reply
            if NEGATIVE.match(text):
                return Reply("Okay.", intent="clarify.cancel")
            if not self.llm_answer:
                return None
            merged = f"{original}. Clarification: {text}" if original else text
            answer = await self.llm_answer(merged, self.ctx.conversation.history())
            if answer:
                return Reply(answer, intent="llm")
            return None
        if kind == "timer":
            seconds = parse_duration(text) or parse_amount(normalise(text))
            if not seconds:
                return None
            label = pending.get("label") or pretty_duration(seconds)
            await self.ctx.device.add_timer(seconds, str(label))
            return Reply(f"Timer set for {pretty_duration(seconds)}.", intent="timer.set")
        if kind == "alarm":
            target = parse_when(text, allow_bare=True)
            if target is not None and target.tzinfo is None:
                target = target.replace(tzinfo=LOCAL_TZ)
            if target is None:
                return None
            wait = int((target - dt.datetime.now(LOCAL_TZ)).total_seconds())
            if wait <= 0:
                wait += 86400
            label = f"alarm {target:%Y-%m-%d %H:%M}"
            await self.ctx.device.add_timer(wait, label)
            await self.ctx.scheduler.add("alarm", target.timestamp(), label, "")
            return Reply(f"Alarm set for {target:%H:%M}.", intent="alarm.set")
        if kind == "reminder":
            target = parse_when(text, allow_bare=True)
            if target is not None and target.tzinfo is None:
                target = target.replace(tzinfo=LOCAL_TZ)
            if target is None:
                return None
            what = str(pending.get("what") or "something")
            await self.ctx.scheduler.add("reminder", target.timestamp(),
                                         f"reminder {what[:40]}", f"Reminder: {what}.")
            return Reply(f"Reminder set for {target:%H:%M}: {what}.", intent="reminder.set")
        if kind == "calendar":
            return await h_calendar_add(text, None, self.ctx)
        if kind == "whatsapp.body":
            body = wa_message_from(text)
            if not body:
                return None
            contact = str(pending.get("contact") or "")
            return await _wa_deliver(self.ctx, contact, body, contact)
        if kind == "whatsapp":
            if NEGATIVE.match(text):
                return Reply("Okay.", intent="clarify.cancel")
            candidates = pending.get("candidates") or []
            answer = normalise(text)
            chosen = None
            ordinals = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
                        "primo": 0, "secondo": 1, "terzo": 2}
            for word, index in ordinals.items():
                if re.search(r"\b" + word + r"\b", answer) and index < len(candidates):
                    chosen = candidates[index]
                    break
            if chosen is None:
                for candidate in candidates:
                    name = str(candidate.get("name") or "").lower()
                    number = str(candidate.get("number") or "")
                    if answer and (name == answer or name in answer or
                                   (number and re.sub(r"\D", "", number) in re.sub(r"\D", "", answer))):
                        chosen = candidate
                        break
            if chosen is None:
                return Reply(str(pending.get("asked") or "Which contact?"), intent="whatsapp.send")
            return await _wa_deliver(self.ctx,
                                     chosen.get("id") or chosen.get("number") or "",
                                     str(pending.get("body") or ""), wa_display(chosen))
        if kind == "voice.name":
            name = re.sub(r"\s+", " ", str(text or "")).strip(" .!?")
            if not name or NEGATIVE.match(name):
                return Reply("Okay, cancelled.", intent="voice.clone")
            safe = re.sub(r"[^A-Za-z0-9 ]", "", name).strip()
            slug = safe.lower().replace(" ", "_") or "voice"
            ref_wav = str(pending.get("ref_wav") or "")
            ref_text = str(pending.get("ref_text") or "")
            if ref_wav and os.path.isfile(ref_wav):
                refs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_refs")
                os.makedirs(refs, exist_ok=True)
                wav_path = os.path.join(refs, slug + ".wav")
                txt_path = os.path.join(refs, slug + ".txt")
                with open(ref_wav, "rb") as src, open(wav_path, "wb") as dst:
                    dst.write(src.read())
                with open(txt_path, "w", encoding="utf-8") as handle:
                    handle.write(ref_text)
                ref_wav = wav_path
            voices.add(name, ref_wav=ref_wav, ref_text=ref_text,
                       provider="omnivoice", voice=name, tone="cloned from recording",
                       ready=False)
            voices.set_active(name)
            return Reply(
                f"Saved voice {name}. Say speak like {name} to use it. "
                "Full cloning needs the OmniVoice backend, which this PC cannot "
                "run yet, so I'll keep the normal voice for now.",
                intent="voice.clone",
            )
        return None

    async def route(self, text: str) -> Optional[Reply]:
        if not text.strip():
            return None
        text = correct_common_asr(correct_clone_phrase(text))
        clean = normalise(text)
        if FASTPATH_PREFIX in text:
            return Reply(text.split(FASTPATH_PREFIX, 1)[1].strip(), speak="pipeline", intent="fastpath")

        # "Stop!" must silence a ringing timer before anything else, including
        # the Music Assistant / radio paths and any pending clarification.
        if STOP_COMMAND.match(clean):
            log.info("intent=stop text=%r", clean[:80])
            return await self._run("stop", h_stop_everything, text, None)

        pending = self.ctx.pending
        if pending and float(pending.get("expires") or 0) <= time.monotonic():
            self.ctx.pending = None
            pending = None
        if pending and CLARIFY_ASK.search(clean):
            prompt = str(pending.get("prompt") or pending.get("question") or "")
            if prompt:
                return Reply(prompt, intent="clarify.repeat")
        if pending and self._supersedes_pending(clean, str(pending.get("kind") or "")):
            log.info("pending %s dropped: new command %r", pending.get("kind"), clean[:60])
            self.ctx.pending = None
            pending = None
        if pending:
            reply = await self._resolve_pending(clean, pending)
            if reply is not None:
                self.ctx.pending = None
                log.info("pending %s resolved by %r", pending.get("kind"), clean[:60])
                return reply
            # Not an answer to the question: keep waiting until the TTL.

        for intent in (*CUSTOM_INTENTS, *INTENTS):
            if intent.name in DISABLED_INTENTS:
                continue
            hit = intent.match(clean)
            if hit:
                log.info("intent=%s text=%r", intent.name, clean[:80])
                return await self._run(intent.name, intent.handler, text, hit)

        # "play Groove Salad" — a station name or a genre without the word radio/station.
        verb = PLAY_VERB.match(clean)
        if verb:
            tail = verb.group(1).strip()
            try:
                stations = await self.ctx.radio.all()
            except Exception as exc:
                log.debug("station lookup failed: %s", exc)
                return None
            if find_station(stations, tail) or clean_station_name(tail) in GENERIC_MUSIC:
                log.info("intent=radio (by name) text=%r", clean[:80])
                return await self._run("radio", h_radio, clean, None)
            words = keywords(tail)
            if words and any(score_station(s, words) >= 2 for s in stations):
                log.info("intent=radio (by tag) text=%r", clean[:80])
                return await self._run("radio", h_radio, clean, None)
            # Unknown genre: try the online directory, but never for "play a game".
            if (words and len(words) <= 4 and not (set(words) & NON_MUSIC_WORDS)
                    and self.ctx.settings.radio_search):
                log.info("intent=radio (online) text=%r", clean[:80])
                return await self._run("radio", h_radio, clean, None)

        # A station named without "play": "and BBC one extra", "capital fm".
        try:
            stations = await self.ctx.radio.all()
        except Exception as exc:
            log.debug("station lookup failed: %s", exc)
            return None
        if station_in_text(stations, clean):
            log.info("intent=radio (station spoken) text=%r", clean[:80])
            return await self._run("radio", h_radio, clean, None)
        return None


# =====================================================================
# HTTP API (OpenAI-compatible + control)
# =====================================================================
def make_app(settings: Settings, router: Router, ctx: Context) -> web.Application:
    app = web.Application()

    @web.middleware
    async def auth(request: web.Request, handler):
        if settings.hub_token and request.path not in ("/health",):
            header = request.headers.get("X-Olive-Token") or ""
            auth = request.headers.get("Authorization") or ""
            bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if header != settings.hub_token and bearer != settings.hub_token:
                return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    app.middlewares.append(auth)

    async def health(_request: web.Request) -> web.Response:
        # "configured" and "reachable" are different things: the MA container
        # can be stopped while the config still says true.
        ma_reachable = False
        if settings.ma_url and settings.ma_player_id:
            try:
                parsed = urllib.parse.urlparse(settings.ma_url)
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(parsed.hostname or "127.0.0.1", port),
                    timeout=1.5,
                )
                writer.close()
                await writer.wait_closed()
                ma_reachable = True
            except Exception:
                pass
        return web.json_response(
            {
                "ok": True,
                "dry_run": settings.dry_run,
                "speaker": settings.speaker_url,
                "home_assistant": bool(ctx.ha.enabled),
                "tuya": bool(ctx.tuya.enabled),
                "local_iot": ctx.iot.counts() if ctx.iot else {},
                "llm": bool(settings.llm_url),
                "llm_model": settings.llm_model,
                "conversation_active": ctx.conversation.active(),
                "conversation_ttl": settings.conversation_ttl,
                "conversation_turns": len(ctx.conversation.history()),
                "led_theme": settings.led_theme,
                "follow_up": settings.follow_up,
                "follow_up_depth": ctx.follow_up_depth,
                "scheduled_items": len(ctx.scheduler.items),
                "scheduled_routines": len(ctx.scheduler.list("routine")),
                "iot_scan": dict(_SCAN_STATE),
                "voice": {"active": voices.active()[0],
                          "personas": list(voices.all_personas()),
                          "capture": {"armed": _VOICE_CAPTURE["active"],
                                      "chunks": len(_VOICE_CAPTURE["chunks"]),
                                      "sample_ready": bool(_VOICE_CAPTURE.get("path"))}},
                "whatsapp_bridge": await whatsapp_bridge.status(ctx.http),
                "flights": flights.status(),
                "pending": (ctx.pending or {}).get("kind"),
                "memory": settings.memory_enabled,
                "memory_counts": ctx.memory.counts() if ctx.memory else {},
                "calendar": bool(os.environ.get("CALENDAR_ICS_URL")
                                 or os.path.isfile(calendar_path())),
                "calendar_file": calendar_path(),
                "calendar_events_today": len(events_for(dt.date.today())),
                "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
                "music_assistant": bool(settings.ma_url and settings.ma_player_id),
                "music_assistant_enabled": ctx.music_assistant.enabled,
                "music_assistant_reachable": ma_reachable,
                "tv": settings.tv_enabled,
                "tv_cast_name": settings.tv_cast_name,
                "tv_adb_host": settings.tv_adb_host,
                "adb_available": bool(ctx.tv.adb_path()),
                "intents": [i.name for i in (*CUSTOM_INTENTS, *INTENTS)
                            if i.name not in DISABLED_INTENTS],
            }
        )

    async def models(_request: web.Request) -> web.Response:
        return web.json_response({"object": "list", "data": [{"id": "olive", "object": "model"}]})

    async def reset(_request: web.Request) -> web.Response:
        """Forget the voice thread (used by dashboards and tests)."""
        ctx.conversation.clear()
        ctx.pending = None
        ctx.follow_up_depth = 0
        ctx.last_intent = ""
        return web.json_response({"ok": True})

    async def scan(_request: web.Request) -> web.Response:
        """Kick off a LAN device scan in the background."""
        reply = await h_iot_scan("", None, ctx)
        return web.json_response({"ok": True, "text": reply.text,
                                  "running": _SCAN_STATE["running"]})

    async def memory_get(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "enabled": settings.memory_enabled,
                                  "counts": ctx.memory.counts(), **ctx.memory.snapshot()})

    async def memory_post(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        key = str(payload.get("key") or "").strip()
        if key:
            value = payload.get("value")
            if value is None:
                return web.json_response({"error": "value required"}, status=400)
            ctx.memory.remember(key, str(value))
            return web.json_response({"ok": True, "key": key, "value": str(value)})
        text = str(payload.get("note") or payload.get("text") or "").strip()
        if not text:
            return web.json_response({"error": "key/value or note required"}, status=400)
        return web.json_response({"ok": True, "note": ctx.memory.add_note(text)})

    async def memory_delete(request: web.Request) -> web.Response:
        key = str(request.query.get("key") or "").strip()
        if key:
            return web.json_response({"ok": True, "removed": ctx.memory.forget(key)})
        ctx.memory.clear()
        return web.json_response({"ok": True, "cleared": True})

    async def command(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        text = str(payload.get("text") or "")
        reply = await router.route(text)
        if reply is None:
            return web.json_response({"handled": False, "text": text}, status=404)
        # /command is used by dashboards and scripts: speak through the device.
        note_spoken(reply.text)
        await ctx.speech.say(reply.text)
        return web.json_response({"handled": True, "intent": reply.intent, "text": reply.text})

    WAKE_ONLY = {
        "", "olive", "hey olive", "okay olive", "ok olive", "hi olive", "hello olive",
        "alexa", "hey alexa", "okay alexa", "hi alexa", "hello alexa", "wake up",
        "hello", "hi", "are you there", "olive are you there",
    }
    LISTENING_LINE = "I'm listening."
    MEDIA_INTENTS = {"radio", "music.mood", "music.stop", "music.pause", "music.resume",
                     "music.status", "music.playlist", "music.next"}
    chat_lock = asyncio.Lock()

    def chat_payload(model: str, content: str) -> dict[str, Any]:
        return {
            "id": "chatcmpl-olive-" + str(int(time.time() * 1000)),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content},
                 "finish_reason": "stop"}
            ],
        }

    def sse_response(model: str, content: str) -> web.StreamResponse:
        """OpenAI-style stream, for clients that ask for one (the speaker may)."""
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        return resp

    async def write_sse(request: web.Request, model: str, content: str) -> web.StreamResponse:
        resp = sse_response(model, content)
        await resp.prepare(request)
        start = {"id": "chatcmpl-olive-chunk", "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": model,
                 "choices": [{"index": 0,
                              "delta": {"role": "assistant", "content": ""},
                              "finish_reason": None}]}
        await resp.write(f"data: {json.dumps(start)}\n\n".encode())
        words = content.split()
        chunk: list[str] = []
        for word in words:
            chunk.append(word)
            if len(chunk) >= 4:
                await _emit(request, resp, model, " ".join(chunk) + " ")
                chunk = []
        if chunk:
            await _emit(request, resp, model, " ".join(chunk))
        done = {"id": "chatcmpl-olive-chunk", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        await resp.write(f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n".encode())
        await resp.write_eof()
        return resp

    async def _emit(request: web.Request, resp: web.StreamResponse, model: str, text: str) -> None:
        event = {"id": "chatcmpl-olive-chunk", "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": model,
                 "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
        await resp.write(f"data: {json.dumps(event)}\n\n".encode())

    async def llm_answer(text: str, history: list[dict[str, str]]) -> Optional[str]:
        if not settings.llm_url:
            return None
        system = settings.system_prompt
        if _looks_italian(text) or settings.language.startswith("it"):
            system += (
                "\nThe user is speaking Italian: reply in natural, standard Italian. "
                "Use Italian words and correct italian pronunciation; do not switch to English."
            )
        if settings.memory_enabled and ctx.memory:
            remembered = ctx.memory.context()
            if remembered:
                system = f"{system}\n\n{remembered}"
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": text})
        headers = {"Content-Type": "application/json"}
        if settings.llm_token:
            headers["Authorization"] = f"Bearer {settings.llm_token}"
        started = time.monotonic()
        models = [settings.llm_model]
        if settings.llm_model_fallback and settings.llm_model_fallback != settings.llm_model:
            models.append(settings.llm_model_fallback)
        for name in models:
            payload = {"model": name, "messages": messages, "stream": False,
                       "max_tokens": settings.llm_max_tokens}
            try:
                async with ctx.http.post(
                    settings.llm_url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=settings.llm_timeout),
                ) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:200]
                        log.error("LLM %s returned %s: %s; trying next model",
                                  name, resp.status, body)
                        continue
                    data = await resp.json(content_type=None)
                answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                if not answer.strip():
                    # Reasoning models can spend the whole budget before
                    # writing content; try the fallback model instead of
                    # returning an empty turn.
                    log.warning("LLM %s returned an empty answer; trying next model", name)
                    continue
                log.info("llm %s answered in %.1fs (%d chars)", name,
                         time.monotonic() - started, len(answer))
                return answer.strip()
            except Exception as exc:
                log.error("LLM %s request failed: %s; trying next model", name, exc)
                continue
        log.error("All LLM models failed for this turn")
        return None

    router.llm_answer = llm_answer

    async def answer(text: str) -> str:
        convo = ctx.conversation
        clean = normalise(text)

        # The 2-minute thread ended: keep only its topics, then start fresh.
        if settings.memory_enabled and ctx.memory and convo.expired():
            added = ctx.memory.absorb(convo.drain())
            log.info("memory absorbed %d topic(s) from the expired thread", added)

        # 0. The microphone sometimes hears the speaker's own announcement.
        if text.strip():
            if is_noise_transcript(text):
                log.info("ignoring ASR noise: %r", text[:70])
                return ""
            now = time.monotonic()
            ctx.recent_speech = [(t, s) for t, s in ctx.recent_speech
                                 if now - t < 20.0 and s]
            for _spoken_at, spoken in ctx.recent_speech:
                if looks_like_echo(text, spoken):
                    log.info("ignoring echo of a recent reply: %r", text[:70])
                    return ""
            # While the box is speaking, anything the mic picks up is its own
            # voice (or noise over it). Never answer that.
            try:
                state = str((await ctx.device.playback()).get("state") or "").lower()
            except Exception:
                state = ""
            if state in ("announcing", "speaking"):
                log.info("ignoring transcript while announcing: %r", text[:70])
                return ""

        # 0b. A pending clone capture: this transcript is the reference text.
        if _VOICE_CAPTURE.get("path"):
            ref_path = _VOICE_CAPTURE.get("path") or ""
            _VOICE_CAPTURE["path"] = ""
            ctx.pending = {
                "kind": "voice.name",
                "ref_wav": ref_path,
                "ref_text": text.strip(),
                "expires": time.monotonic() + ctx.settings.pending_ttl,
            }
            log.info("voice capture waiting for a name: %r", text[:60])
            return "Who is this?"

        # 1. Wake word only (or nothing captured): stay in the conversation.
        if clean in WAKE_ONLY:
            if time.monotonic() < ctx.follow_up_window:
                # The hub opened this mic window itself and the room stayed
                # quiet: end the chain instead of saying "I'm listening" again.
                ctx.follow_up_window = 0.0
                ctx.follow_up_depth = 0
                log.info("silent follow-up window closed")
                return ""
            ctx.follow_up_depth += 1
            ctx.last_intent = "conversation"
            convo.append("assistant", LISTENING_LINE)
            log.info("wake-only turn -> %r (follow-up depth %d)", LISTENING_LINE,
                     ctx.follow_up_depth)
            return LISTENING_LINE

        # 2. "Stop" / "Alexa stop": a ringing timer, an announcement or music
        # must be silenced before this can mean "end the conversation".
        if STOP_COMMAND.match(clean):
            convo.append("user", text)
            try:
                said = await stop_everything(ctx)
            except Exception as exc:
                log.warning("stop failed: %s", exc)
                said = None
            ctx.pending = None
            ctx.follow_up_depth = 0
            ctx.follow_up_window = 0.0
            if said:
                convo.append("assistant", said)
                ctx.last_intent = "stop"
                log.info("stop: %r -> %r", clean[:60], said)
                return said
            # Nothing was playing: fall through to closing the thread.

        # 2b. Explicit "that's all" closes the thread.
        if clean in {"stop", "stop listening", "that's all", "thats all", "thank you",
                     "thanks", "never mind", "nevermind", "goodbye", "bye"}:
            convo.append("user", text)
            if settings.memory_enabled and ctx.memory:
                ctx.memory.absorb(convo.drain())
            else:
                convo.clear()
            reply = "Okay."
            log.info("conversation closed by user")
            ctx.pending = None
            ctx.follow_up_depth = 0
            ctx.last_intent = "close"
            return reply

        # A real turn resets the empty-turn budget.
        ctx.follow_up_depth = 0
        ctx.follow_up_window = 0.0

        history = convo.history()
        log.info("chat turn %r (history=%d)", text[:80], len(history))

        # 3. Local intents first (radio, timers, home, time ...).
        try:
            reply = await router.route(text)
        except Exception as exc:
            log.exception("router failed: %s", exc)
            reply = None
        if reply is not None and reply.text:
            ctx.last_intent = reply.intent
            convo.append("user", text)
            convo.append("assistant", reply.text)
            return reply.text

        # 3b. A home command that no backend matched must not reach the LLM:
        # it would happily answer "Done." without switching anything.
        if HOME_CONTROL.search(clean):
            local = ctx.iot.ready if ctx.iot.enabled else []
            if ctx.ha.enabled and not local:
                refusal = "I couldn't find that device in Home Assistant."
            elif not local:
                refusal = ("I can't control that light yet. Add its local key to "
                           "iot_keys.json and I can switch it offline.")
            else:
                refusal = ("I couldn't find that light. Use the name you gave it "
                           "in iot_keys.json.")
            ctx.last_intent = "home.unavailable"
            convo.append("user", text)
            convo.append("assistant", refusal)
            log.info("home control with no match: %r -> %r", clean[:60], refusal)
            return refusal

        # 4. Cloud AI (Groq/OpenAI/Ollama) with the 2-minute thread as context.
        with contextlib.suppress(Exception):
            if settings.led_theme:
                await ctx.device.set_led_profile("thinking", settings.led_thinking_rgb,
                                                 settings.led_brightness)
        answer_text = await llm_answer(text, history)
        if answer_text is None:
            answer_text = "Sorry, I couldn't reach my reasoning core just now."
        with contextlib.suppress(Exception):
            if settings.led_theme:
                await ctx.device.set_led_profile("listening", settings.led_listening_rgb,
                                                 settings.led_brightness)
        convo.append("user", text)
        convo.append("assistant", answer_text)
        ctx.last_intent = "llm"
        if answer_text.rstrip().endswith("?") and not COURTESY_QUESTION.search(answer_text):
            # Open-ended question: remember it so a short answer ("the second
            # one", "hiphop", "yes") continues the research without the wake word.
            ctx.pending = {"kind": "clarify", "question": answer_text.strip(),
                           "original": text,
                           "expires": time.monotonic() + settings.pending_ttl}
            log.info("clarification pending: %r", answer_text[:100])
        return answer_text

    def note_spoken(text: str) -> None:
        """Remember what the box just said, for the echo guard."""
        line = (text or "").strip()
        if not line:
            return
        now = time.monotonic()
        ctx.recent_speech.append((now, line))
        ctx.recent_speech = [(t, s) for t, s in ctx.recent_speech if now - t < 30.0][-4:]

    async def follow_up_listen() -> None:
        """Reopen the microphone so the user can answer without the wake word."""
        await asyncio.sleep(max(0.2, settings.follow_up_delay))
        if ctx.follow_up_depth >= 2 or settings.dry_run:
            return
        if time.monotonic() < ctx.follow_up_window - 1.0:
            log.info("follow-up already open; not opening another")
            return
        # Never open the mic while the announcement is still playing: it would
        # record the speaker's own tail and the hub would answer itself.
        with contextlib.suppress(Exception):
            if await ctx.speech.wait_until_done():
                await asyncio.sleep(0.8)
        try:
            if await ctx.device.is_playing():
                log.info("follow-up skipped: media is playing")
                return
        except Exception:
            pass
        try:
            ctx.follow_up_window = time.monotonic() + 10.0
            await ctx.device.call("POST", "/wake-word/test", {})
            log.info("follow-up listening window opened")
        except Exception as exc:
            ctx.follow_up_window = 0.0
            log.warning("follow-up listening failed: %s", exc)

    async def chat(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        messages = body.get("messages") or []
        last = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        model = body.get("model", "olive")
        wants_stream = bool(body.get("stream")) and settings.stream_chat
        log.info("chat request: stream=%s (serving %s) messages=%d",
                 bool(body.get("stream")), "sse" if wants_stream else "json", len(messages))

        async with chat_lock:
            try:
                content = await answer(str(last or ""))
            except Exception as exc:
                log.exception("chat failed: %s", exc)
                content = "Sorry, something went wrong."

        follow = (settings.follow_up and bool(content) and content != "Okay."
                  and ctx.follow_up_depth < 2
                  and (ctx.last_intent not in MEDIA_INTENTS or content.rstrip().endswith("?")))

        if content:
            note_spoken(content)
            log.info("reply: %r", content[:140])

        if settings.suppress_tts_when_device_speaks:
            # The speaker's own TTS path drops assistant replies from local
            # endpoints, so the hub voices the line through /audio/announce.
            if content:
                with contextlib.suppress(Exception):
                    await ctx.speech.say(content)
            content = ""
        if follow:
            if ctx.follow_up_task and not ctx.follow_up_task.done():
                ctx.follow_up_task.cancel()
            ctx.follow_up_task = asyncio.create_task(follow_up_listen())

        # Music was lowered when the mic opened; restore it after the reply and
        # any follow-up listening window have finished.
        if ctx.listening_duck:
            async def restore_music() -> None:
                if follow:
                    await asyncio.sleep(max(1.0, settings.follow_up_delay + 12.0))
                else:
                    await asyncio.sleep(1.0)
                await speech.restore_duck(ctx.listening_duck)
                ctx.listening_duck = None
            background(restore_music())

        if wants_stream:
            return await write_sse(request, model, content)
        return web.json_response(chat_payload(model, content))


    def write_restart_helper() -> str:
        """A tiny batch file that outlives the hub and replaces it.

        The scheduled "Olive Hub" task runs PowerShell, whose startup can
        stall for minutes in session 0. This helper is spawned by the hub
        itself, so it shares the hub's token and can always stop it.
        """
        root = os.path.dirname(os.path.abspath(__file__))
        helper = os.path.join(root, "restart-helper.cmd")
        lines = [
            "@echo off",
            "setlocal",
            "rem Give the HTTP response and the in-process exit a moment.",
            "timeout /t 3 /nobreak >nul",
            "rem Use the same detached WMI starter as the watchdog.  Starting",
            "rem python with 'start /b cmd /c' from this helper proved unreliable",
            "rem (the child died with the helper on this machine).",
            'powershell -NoProfile -ExecutionPolicy Bypass -File "{starter}" >nul 2>&1'.format(
                starter=os.path.join(root, "start-hub-detached.ps1"),
            ),
            "exit /b 0",
            "",
        ]
        with open(helper, "w", encoding="ascii", newline="\r\n") as handle:
            handle.write("\n".join(lines))
        return helper

    async def restart(_request: web.Request) -> web.Response:
        """Restart the hub process without the Task Scheduler round-trip."""
        helper = write_restart_helper()
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", helper],
                cwd=os.path.dirname(helper),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
        except Exception as exc:
            log.exception("restart helper failed")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        log.warning("restart requested over HTTP; helper %s", helper)
        # Exit in-process: a child of the hub always has permission to end it,
        # so the helper only has to start the replacement.
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
        return web.json_response({"ok": True, "restarting": True, "pid": os.getpid()})

    async def intents_get(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, **intents_config()})

    async def intents_post(request: web.Request) -> web.Response:
        """Replace custom_intents.json and hot-reload it (no hub restart)."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        if not isinstance(data, dict) or "intents" not in data:
            return web.json_response({"ok": False, "error": "expected {intents: [...]}"}, status=400)
        data.setdefault("version", 1)
        # Validate/apply against the posted config first, keeping the old state
        # so it can be restored if the file write fails.
        global CUSTOM_INTENTS, DISABLED_INTENTS
        old_custom, old_disabled = CUSTOM_INTENTS, DISABLED_INTENTS
        try:
            errors, names = apply_intents_config(data)
        except Exception as exc:
            CUSTOM_INTENTS, DISABLED_INTENTS = old_custom, old_disabled
            log.exception("intent validation failed")
            return web.json_response({"ok": False, "error": f"validation failed: {exc}"}, status=400)
        if errors:
            CUSTOM_INTENTS, DISABLED_INTENTS = old_custom, old_disabled
            return web.json_response({"ok": False, "errors": errors}, status=400)
        tmp = ""
        try:
            directory = os.path.dirname(CUSTOM_INTENTS_FILE) or "."
            fd, tmp = tempfile.mkstemp(prefix=".custom-intents-", suffix=".json", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
            os.replace(tmp, CUSTOM_INTENTS_FILE)
        except Exception as exc:
            CUSTOM_INTENTS, DISABLED_INTENTS = old_custom, old_disabled
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            log.exception("failed to save custom intents")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        log.info("custom intents reloaded: %d active, %d built-ins disabled",
                 len(names), len(DISABLED_INTENTS))
        return web.json_response({"ok": True, "active": names,
                                  "disabled": sorted(DISABLED_INTENTS)})

    app.router.add_get("/health", health)
    async def intents_reload(_request: web.Request) -> web.Response:
        """Re-read custom_intents.json and apply atomically, with rollback."""
        global CUSTOM_INTENTS, DISABLED_INTENTS
        old_custom, old_disabled = CUSTOM_INTENTS, DISABLED_INTENTS
        try:
            errors, names = reload_custom_intents()
        except Exception as exc:
            CUSTOM_INTENTS, DISABLED_INTENTS = old_custom, old_disabled
            log.exception("intentReload failed")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        if errors:
            CUSTOM_INTENTS, DISABLED_INTENTS = old_custom, old_disabled
            return web.json_response({"ok": False, "errors": errors}, status=400)
        log.info("intentReload: %d custom intents active, %d built-ins disabled",
                 len(names), len(DISABLED_INTENTS))
        return web.json_response({"ok": True, "active": names,
                                  "disabled": sorted(DISABLED_INTENTS),
                                  "builtins": len(INTENTS)})

    app.router.add_get("/intents", intents_get)
    app.router.add_post("/intents", intents_post)
    app.router.add_post("/intents/reload", intents_reload)
    app.router.add_get("/v1/models", models)
    app.router.add_post("/command", command)
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_post("/reset", reset)
    app.router.add_post("/scan", scan)
    app.router.add_post("/restart", restart)
    app.router.add_get("/memory", memory_get)
    app.router.add_post("/memory", memory_post)
    app.router.add_delete("/memory", memory_delete)

    return app


# =====================================================================
# WYOMING ASR PROXY (thin, bounded, no intent logic)
# =====================================================================
if WYOMING_AVAILABLE:

    class AsrProxyHandler(AsyncEventHandler):
        """Forwards audio to Whisper and returns one transcript, with a timeout."""

        def __init__(self, reader, writer, settings: Settings, duck_fn=None) -> None:
            super().__init__(reader, writer)
            self.settings = settings
            self.client: Optional[AsyncTcpClient] = None
            self.upstream_failed = False
            self.last_upstream_warning = 0.0
            self.duck_fn = duck_fn
            self.ducked = False
            log.info("ASR client connected from %s", writer.get_extra_info("peername"))

        def _warn_upstream(self, exc: BaseException) -> None:
            """Log an upstream failure at most once per 30 seconds."""
            now = asyncio.get_running_loop().time()
            if now - self.last_upstream_warning < 30:
                return
            self.last_upstream_warning = now
            log.warning(
                "Whisper at %s:%s unavailable (%s); voice requests will return "
                "an empty transcript until it is started",
                self.settings.whisper_host, self.settings.whisper_port, exc,
            )

        async def _safe_write(self, event: Event) -> bool:
            if self.client is None:
                return False
            try:
                await self.client.write_event(event)
                return True
            except (OSError, ConnectionError) as exc:
                self._warn_upstream(exc)
                self.upstream_failed = True
                with contextlib.suppress(Exception):
                    await self.client.disconnect()
                self.client = None
                return False

        async def handle_event(self, event: Event) -> bool:
            try:
                if Transcribe.is_type(event.type) or AudioStart.is_type(event.type):
                    # Transcribe/AudioStart start a new request: allow a retry
                    # if Whisper has just come back.
                    if Transcribe.is_type(event.type):
                        self.ducked = False
                    self.upstream_failed = False
                    if self.duck_fn and not self.ducked:
                        self.ducked = True
                        # The speaker opened its mic: lower music before the
                        # user speaks, without delaying the audio stream.
                        asyncio.create_task(self.duck_fn())
                    if AudioStart.is_type(event.type) and _VOICE_CAPTURE["active"]:
                        fmt = AudioStart.from_event(event)
                        _VOICE_CAPTURE.update(chunks=[], rate=fmt.rate,
                                              width=fmt.width, channels=fmt.channels)
                    if self.client is None and not self.upstream_failed:
                        self.client = AsyncTcpClient(
                            self.settings.whisper_host,
                            self.settings.whisper_port,
                            connect_timeout=3.0,
                        )
                        try:
                            await self.client.connect()
                        except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
                            self._warn_upstream(exc)
                            self.upstream_failed = True
                            with contextlib.suppress(Exception):
                                await self.client.disconnect()
                            self.client = None
                    await self._safe_write(event)
                    return True

                if AudioChunk.is_type(event.type):
                    if _VOICE_CAPTURE["active"] and len(_VOICE_CAPTURE["chunks"]) < 16000:
                        _VOICE_CAPTURE["chunks"].append(AudioChunk.from_event(event).audio)
                    await self._safe_write(event)
                    return True

                if AudioStop.is_type(event.type):
                    if _VOICE_CAPTURE["active"]:
                        _VOICE_CAPTURE["active"] = False
                        save_voice_capture()
                    if self.client is None:
                        if not self.upstream_failed:
                            log.info("ASR audio stop without a Whisper connection")
                        # Never leave the device's pipeline waiting forever.
                        await self.write_event(Transcript(text="").event())
                        return True
                    if not await self._safe_write(event):
                        await self.write_event(Transcript(text="").event())
                        return True
                    try:
                        while True:
                            incoming = await asyncio.wait_for(
                                self.client.read_event(), timeout=self.settings.asr_timeout
                            )
                            if incoming is None:
                                break
                            if Transcript.is_type(incoming.type):
                                text = Transcript.from_event(incoming).text.strip()
                                log.info("transcript: %r", text)
                                if not text and self.settings.asr_empty_placeholder:
                                    # Wake word heard but nothing intelligible: nudge the
                                    # assistant so it can answer "I'm listening." and keep
                                    # the conversation open.
                                    text = self.settings.asr_empty_placeholder
                                    log.info("empty transcript -> placeholder %r", text)
                                await self.write_event(Transcript(text=text).event())
                                break
                    except asyncio.TimeoutError:
                        log.warning("Whisper timed out after %.1fs", self.settings.asr_timeout)
                        await self.write_event(Transcript(text="").event())
                    except (OSError, ConnectionError) as exc:
                        self._warn_upstream(exc)
                        await self.write_event(Transcript(text="").event())
                    finally:
                        client, self.client = self.client, None
                        if client is not None:
                            with contextlib.suppress(Exception):
                                await client.disconnect()
                    return True

                return True
            except (OSError, ConnectionError) as exc:
                # The device disconnected: end the handler cleanly instead of
                # leaving an unhandled task exception in the log.
                self._warn_upstream(exc)
                return False

        async def disconnect(self) -> None:
            if self.client is not None:
                with contextlib.suppress(Exception):
                    await self.client.disconnect()
                self.client = None
            await super().disconnect()


# =====================================================================
# ENTRY POINT
# =====================================================================
async def run(settings: Optional[Settings] = None) -> None:
    if settings is None:
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
        settings = Settings.from_env()

    http = aiohttp.ClientSession()
    device = LibreEcho(settings)
    await device.start()
    speech = Speech(device, settings)
    ha = HomeAssistant(settings)
    weather = Weather(settings, http)
    knowledge = Knowledge(http)
    places = Places(http, settings)
    shopping = ShoppingList()
    radio = RadioLibrary(device, settings, http)
    tuya = TuyaHome(settings)
    facts = PersonalFacts()
    music_catalog = MusicCatalog(http)
    music_assistant = MusicAssistant(settings, http)
    tv = TvControl(settings)
    memory = PermanentMemory(path=settings.memory_file, enabled=settings.memory_enabled)
    iot = IotRegistry(path=settings.iot_keys_file, enabled=settings.iot_enabled)
    scheduler = Scheduler(speech)
    ctx = Context(
        settings=settings, device=device, speech=speech, ha=ha,
        weather=weather, knowledge=knowledge, http=http, radio=radio, tuya=tuya,
        scheduler=scheduler, facts=facts, music_catalog=music_catalog,
        music_assistant=music_assistant, tv=tv, memory=memory, iot=iot,
        places=places, shopping=shopping,
    )
    router = Router(ctx)
    ctx.conversation = Conversation(ttl=settings.conversation_ttl,
                                    max_turns=settings.conversation_turns)

    async def run_scheduled_action(item: "ScheduleItem") -> None:
        if not item.action:
            return
        reply = await home_toggle(ctx, item.target, item.action == "on")
        log.info("scheduled action %s (%s): %s", item.id, item.action, reply.text)

    scheduler.action_fn = run_scheduled_action

    async def duck_for_voice() -> None:
        restore = await speech.duck_for_listening()
        if restore:
            ctx.listening_duck = restore

    async def seed_radio() -> None:
        try:
            await radio.ensure_seeded()
        except Exception as exc:
            log.warning("radio seeding failed: %s", exc)

    async def apply_led_theme() -> None:
        """Yellow while listening/conversing, orange while the cloud AI thinks."""
        if not settings.led_theme or settings.dry_run:
            return
        try:
            await device.set_led_profile("listening", settings.led_listening_rgb,
                                         settings.led_brightness)
            await device.set_led_profile("thinking", settings.led_thinking_rgb,
                                         settings.led_brightness)
            log.info("LED theme applied: listening=%s thinking=%s",
                     settings.led_listening_rgb, settings.led_thinking_rgb)
        except Exception as exc:
            log.warning("LED theme failed: %s", exc)

    if settings.dry_run:
        log.warning("DRY RUN: no speaker, Home Assistant, or LLM calls will be made")

    app = make_app(settings, router, ctx)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = web.TCPSite(runner, settings.hub_host, settings.hub_port)
        await site.start()
        log.info("HTTP listening on http://%s:%s (openai-compatible: /v1/chat/completions)",
                 settings.hub_host, settings.hub_port)

        asyncio.create_task(seed_radio())
        asyncio.create_task(apply_led_theme())
        await scheduler.start()
        log.info("scheduler ready with %d stored item(s)", len(scheduler.items))

        asr_task = None
        if WYOMING_AVAILABLE:
            asr_server = AsyncTcpServer(settings.asr_host, settings.asr_port)
            log.info("Wyoming ASR proxy listening on %s:%s", settings.asr_host, settings.asr_port)
            asr_task = asyncio.create_task(
                asr_server.run(lambda r, w: AsrProxyHandler(r, w, settings,
                                                            duck_fn=duck_for_voice))
            )
        else:
            log.warning("wyoming not installed: ASR proxy disabled (pip install wyoming)")

        if asr_task is not None:
            await asr_task
        else:
            await asyncio.Event().wait()
    finally:
        await scheduler.stop()
        await runner.cleanup()
        await device.close()
        await ha.close()
        await http.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Olive Hub")
    parser.add_argument("--dry-run", action="store_true", help="no device/HA/LLM network calls")
    parser.add_argument("--host", help="override HUB_HOST")
    parser.add_argument("--port", type=int, help="override HUB_PORT")
    args = parser.parse_args()

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    settings = Settings.from_env()
    if args.dry_run:
        settings.dry_run = True
    if args.host:
        settings.hub_host = args.host
    if args.port:
        settings.hub_port = args.port

    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        log.info("olive-hub stopped.")


if __name__ == "__main__":
    main()
