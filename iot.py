#!/usr/bin/env python3
"""Local IoT control for Olive: no cloud, no Home Assistant.

Devices are described in ``iot_keys.json``::

    {
      "devices": [
        {
          "name": "kitchen light",
          "aliases": ["kitchen", "cucina"],
          "ip": "192.168.1.71",
          "id": "bfa61b2c537b73b6adjap3",
          "key": "16-character-local-key",
          "version": "3.4",
          "kind": "bulb"
        }
      ]
    }

Only devices with a non-empty ``key`` become active, so it is safe to keep a
half-filled file around. ``kind`` is ``bulb`` (default), ``plug``, ``switch`` or
``outlet``; bulbs speak the DPS 20/21/22/24 schema, plugs use DPS 1.

Everything is blocking Tuya LAN protocol work, so calls run in a worker thread
and never stall the hub's event loop.

Get the keys once (they are per device, 16 characters) from any of:

* Tuya IoT platform: project → Devices → device → "Local key" (needs the
  cloud project and the Smart Life account linked).
* A rooted Android phone with Smart Life installed: the key is in the app's
  ``shared_prefs``/database.
* ``tuya-cloudcutter`` for supported chipsets: it replaces the cloud with a
  known key and leaves the device fully local.

``iot_scan.py`` finds the devices and their IDs, and can pre-fill this file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

log = logging.getLogger("OLIVE-HUB")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KEYS_FILE = os.environ.get("IOT_KEYS_FILE") or os.path.join(BASE_DIR, "iot_keys.json")

STOPWORDS = {"the", "a", "an", "my", "please", "in", "on", "at", "light", "lights",
             "lamp", "lamps", "bulb", "bulbs", "lights"}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return {word for word in words if word not in STOPWORDS}


def _digits(value: Any, default: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


class IotRegistry:
    """Load local devices, resolve spoken names and talk the Tuya LAN protocol."""

    def __init__(self, path: str = "", enabled: bool = True) -> None:
        self.path = path or DEFAULT_KEYS_FILE
        self.enabled = enabled
        self.devices: list[dict[str, Any]] = []
        self.error = ""
        self._mtime: Optional[float] = None
        if self.enabled:
            self.load()

    # -- setup -------------------------------------------------------
    def load(self) -> None:
        self.devices = []
        self.error = ""
        try:
            self._mtime = os.path.getmtime(self.path) if os.path.isfile(self.path) else None
        except OSError:
            self._mtime = None
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except Exception as exc:
            self.error = f"can't read {self.path}: {exc}"
            log.warning("iot: %s", self.error)
            return
        rows = data.get("devices") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not (row.get("id") and row.get("ip") and row.get("key")):
                continue
            entry = dict(row)
            entry.setdefault("kind", "bulb")
            entry.setdefault("version", "3.4")
            entry["name"] = str(entry.get("name") or "").strip()
            aliases = entry.get("aliases") or []
            entry["aliases"] = [str(alias) for alias in aliases if alias]
            self.devices.append(entry)

    def reload(self) -> int:
        self.load()
        return len(self.devices)

    def refresh(self) -> None:
        """Reload when iot_keys.json changed on disk (paste a key, no restart)."""
        try:
            mtime = os.path.getmtime(self.path) if os.path.isfile(self.path) else None
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self.load()

    @property
    def ready(self) -> list[dict[str, Any]]:
        self.refresh()
        return self.devices

    def counts(self) -> dict[str, Any]:
        self.refresh()
        return {
            "enabled": self.enabled,
            "devices": len(self.devices),
            "path": self.path,
            "names": [d.get("name") or d.get("ip") for d in self.devices],
        }

    # -- matching ----------------------------------------------------
    @staticmethod
    def _score(entry: dict[str, Any], wanted: str) -> int:
        words = _tokens(wanted)
        if not words:
            return 0
        best = 0
        for candidate in [entry.get("name", "")] + list(entry.get("aliases") or []):
            name = _tokens(candidate)
            if not name:
                continue
            if name == words:
                best = max(best, 100)
            elif name <= words:
                best = max(best, 80 + len(name))
            elif words <= name:
                best = max(best, 70 + len(words))
            else:
                overlap = len(name & words)
                best = max(best, int(50 * overlap / max(1, len(words))))
        return best

    def resolve(self, target: str) -> Optional[dict[str, Any]]:
        if not self.enabled:
            return None
        self.refresh()
        if not self.devices or not str(target or "").strip():
            return None
        scored = [(self._score(entry, target), entry) for entry in self.devices]
        score, entry = max(scored, key=lambda item: item[0])
        return entry if score >= 60 else None

    # -- Tuya LAN protocol -------------------------------------------
    def _device(self, entry: dict[str, Any]):
        import tinytuya

        kind = str(entry.get("kind") or "bulb").lower()
        version = float(entry.get("version") or 3.4)
        factory = tinytuya.OutletDevice if kind in {"plug", "switch", "outlet", "socket"} \
            else tinytuya.BulbDevice
        device = factory(entry["id"], entry["ip"], entry["key"], version=version)
        try:
            device.set_socketTimeout(5)
            device.set_socketRetryLimit(1)
            device.set_retry(False)
            device.set_socketPersistent(False)
        except Exception:
            pass
        return device

    @staticmethod
    def _failed(result: Any) -> bool:
        return not isinstance(result, dict) or bool(result.get("Error"))

    def _status_sync(self, entry: dict[str, Any]) -> dict[str, Any]:
        return self._device(entry).status()

    async def status(self, entry: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            data = await asyncio.to_thread(self._status_sync, entry)
        except Exception as exc:
            log.warning("iot status %s failed: %s", entry.get("name") or entry.get("ip"), exc)
            return None
        if self._failed(data):
            log.warning("iot status %s: %s", entry.get("name") or entry.get("ip"),
                        (data or {}).get("Error") if isinstance(data, dict) else data)
            return None
        return data

    async def is_on(self, entry: dict[str, Any]) -> Optional[bool]:
        data = await self.status(entry)
        if not data:
            return None
        dps = data.get("dps") or {}
        kind = str(entry.get("kind") or "bulb").lower()
        keys = ("1", "20") if kind in {"plug", "switch", "outlet", "socket"} else ("20", "1")
        for key in keys:
            if key in dps:
                return bool(dps[key])
        return None

    def _set_power_sync(self, entry: dict[str, Any], on: bool) -> Any:
        device = self._device(entry)
        # turn_on/turn_off let tinytuya pick the right DPS (20 for bulbs, 1 for
        # plugs) instead of hard-coding one schema.
        return device.turn_on() if on else device.turn_off()

    async def set_power(self, entry: dict[str, Any], on: bool) -> bool:
        try:
            result = await asyncio.to_thread(self._set_power_sync, entry, on)
        except Exception as exc:
            log.warning("iot power %s failed: %s", entry.get("name") or entry.get("ip"), exc)
            return False
        if self._failed(result):
            log.warning("iot power %s: %s", entry.get("name") or entry.get("ip"),
                        (result or {}).get("Error") if isinstance(result, dict) else result)
            return False
        return True

    def _brightness_sync(self, entry: dict[str, Any], percent: int) -> Any:
        device = self._device(entry)
        percent = max(1, min(100, percent))
        state = device.status()
        dps = (state or {}).get("dps") or {}
        if not dps:
            return state

        # Colour mode: brightness is the "v" component of colour_data_v2 (DPS 24).
        if str(dps.get("21") or "").lower() == "colour" and isinstance(dps.get("24"), str):
            try:
                colour = json.loads(dps["24"])
                colour["v"] = int(round(percent * 10))
                return device.set_value("24", json.dumps(colour, separators=(",", ":")))
            except Exception:
                pass

        # White mode: bright_value_v2 (DPS 22) is 10..1000, older bright_value
        # (DPS 2) is 25..255. tinytuya's set_brightness() is NOT percent-based,
        # so passing 40 would set 4% - map the percentage ourselves.
        if "22" in dps:
            key, low, high = "22", 10, 1000
        elif "2" in dps:
            key, low, high = "2", 25, 255
        else:
            return {"Error": "no brightness data point on this device"}
        value = int(round(low + (high - low) * (percent / 100.0)))
        return device.set_value(key, value)

    async def set_brightness(self, entry: dict[str, Any], percent: int) -> bool:
        kind = str(entry.get("kind") or "bulb").lower()
        if kind in {"plug", "switch", "outlet", "socket"}:
            return False
        try:
            result = await asyncio.to_thread(self._brightness_sync, entry, percent)
        except Exception as exc:
            log.warning("iot brightness %s failed: %s",
                        entry.get("name") or entry.get("ip"), exc)
            return False
        if self._failed(result):
            log.warning("iot brightness %s: %s", entry.get("name") or entry.get("ip"),
                        (result or {}).get("Error") if isinstance(result, dict) else result)
            return False
        return True

    # -- colour and scenes -------------------------------------------
    COLOURS: dict[str, tuple[float, float]] = {
        "red": (0.0, 1.0), "orange": (0.08, 1.0), "yellow": (0.16, 1.0),
        "green": (0.33, 1.0), "teal": (0.48, 1.0), "cyan": (0.5, 1.0),
        "blue": (0.67, 1.0), "purple": (0.78, 1.0), "violet": (0.78, 1.0),
        "pink": (0.9, 1.0), "magenta": (0.87, 1.0),
        "rosso": (0.0, 1.0), "arancione": (0.08, 1.0),
        "giallo": (0.16, 1.0), "verde": (0.33, 1.0),
        "azzurro": (0.5, 1.0), "blu": (0.67, 1.0),
        "viola": (0.78, 1.0), "rosa": (0.9, 1.0),
    }

    def _colour_sync(self, entry: dict[str, Any], colour: str,
                     brightness: int = 100) -> Any:
        device = self._device(entry)
        key = re.sub(r"\s+", " ", str(colour or "").strip().lower())
        if key in ("white", "warm white", "cool white", "bianco", "white light"):
            percent = max(1, min(100, brightness))
            bulb_brightness = int(10 + 990 * percent / 100.0)
            return device.set_white(brightness=bulb_brightness,
                                    colourtemp=0 if "cool" in key else 1000)
        if key.startswith("hue:") or key.isdigit():
            try:
                degrees = float(key.split(":", 1)[1] if ":" in key else key)
                return device.set_hsv((degrees % 360) / 360.0, 1.0,
                                      max(1, min(100, brightness)) / 100.0)
            except ValueError:
                pass
        hue, saturation = self.COLOURS.get(key, (0.0, 0.0))
        return device.set_hsv(hue, saturation, max(1, min(100, brightness)) / 100.0)

    async def set_colour(self, entry: dict[str, Any], colour: str,
                         brightness: int = 100) -> bool:
        kind = str(entry.get("kind") or "bulb").lower()
        if kind in {"plug", "switch", "outlet", "socket"}:
            return False
        try:
            result = await asyncio.to_thread(self._colour_sync, entry, colour, brightness)
        except Exception as exc:
            log.warning("iot colour %s failed: %s", entry.get("name") or entry.get("ip"), exc)
            return False
        if self._failed(result):
            log.warning("iot colour %s: %s", entry.get("name") or entry.get("ip"),
                        (result or {}).get("Error") if isinstance(result, dict) else result)
            return False
        return True

    SCENES: dict[str, int] = {
        "sunset": 1, "sunrise": 2, "party": 2, "romance": 3,
        "sleep": 1, "movie": 4, "romantic": 3, "festa": 2,
    }

    def _scene_sync(self, entry: dict[str, Any], scene: int) -> Any:
        return self._device(entry).set_scene(max(1, min(4, int(scene))))

    async def set_scene(self, entry: dict[str, Any], scene: int) -> bool:
        kind = str(entry.get("kind") or "bulb").lower()
        if kind in {"plug", "switch", "outlet", "socket"}:
            return False
        try:
            result = await asyncio.to_thread(self._scene_sync, entry, scene)
        except Exception as exc:
            log.warning("iot scene %s failed: %s", entry.get("name") or entry.get("ip"), exc)
            return False
        if self._failed(result):
            log.warning("iot scene %s: %s", entry.get("name") or entry.get("ip"),
                        (result or {}).get("Error") if isinstance(result, dict) else result)
            return False
        return True

    # -- reporting ---------------------------------------------------
    def describe(self) -> str:
        if not self.devices:
            return ("I don't have any local lights configured. Add their IDs and "
                    "keys to iot_keys.json.")
        return "Local lights: " + ", ".join(d.get("name") or d.get("ip")
                                            for d in self.devices) + "."
