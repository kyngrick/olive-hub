"""TV control for Olive Hub: Chromecast media + Android TV via ADB.

Two transports, because they cover different things:

* **Chromecast** (``pychromecast``) casts audio/video URLs, pauses, stops and
  changes volume. Casting also wakes most TVs through HDMI-CEC.
* **ADB** (the platform-tools ``adb`` binary) powers an Android TV on/off,
  sends remote keys and launches apps such as YouTube or Netflix.

Everything blocking runs in a worker thread, so the hub's event loop never
stalls while mDNS discovery or ``adb`` runs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import struct
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("OLIVE-HUB")

DEFAULT_RECEIVER = "CC1AD845"  # Google's Default Media Receiver

# Enter on the remote, i.e. the key ADB uses to wake/select.
KEYCODES = {
    "power": 26,
    "home": 3,
    "back": 4,
    "up": 19,
    "down": 20,
    "left": 21,
    "right": 22,
    "ok": 23,
    "enter": 66,
    "play": 126,
    "pause": 127,
    "play_pause": 85,
    "stop": 86,
    "next": 87,
    "previous": 88,
    "volume_up": 24,
    "volume_down": 25,
    "mute": 164,
    "menu": 82,
    "settings": 176,
    "wake": 224,
    "sleep": 223,
}

# Common packages, so "open netflix" works without a package name.
APPS = {
    "youtube": "com.google.android.youtube.tv",
    "netflix": "com.netflix.ninja",
    "prime video": "com.amazon.amazonvideo.livingroom",
    "amazon prime": "com.amazon.amazonvideo.livingroom",
    "disney": "com.disney.disneyplus",
    "disney plus": "com.disney.disneyplus",
    "spotify": "com.spotify.tv.android",
    "plex": "com.plexapp.android",
    "kodi": "org.xbmc.kodi",
    "vlc": "org.videolan.vlc",
    "browser": "com.android.chrome",
    "settings": "com.android.tv.settings",
}

# What the TV says it is showing, in words. Cast reports almost nothing on an
# Android TV, so the foreground package from ADB is the useful signal.
PACKAGE_NAMES = {
    "com.netflix.ninja": "Netflix",
    "com.google.android.youtube.tv": "YouTube",
    "com.google.android.apps.youtube.tv.cobalt": "YouTube",
    "com.amazon.amazonvideo.livingroom": "Prime Video",
    "com.disney.disneyplus": "Disney Plus",
    "com.spotify.tv.android": "Spotify",
    "com.plexapp.android": "Plex",
    "org.xbmc.kodi": "Kodi",
    "org.videolan.vlc": "VLC",
    "com.android.chrome": "Chrome",
    "com.google.android.tvlauncher": "the home screen",
    "com.google.android.apps.mediashell": "a cast",
}


@dataclass
class CastDevice:
    name: str
    model: str
    host: str
    port: int
    uuid: str
    cast_type: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "model": self.model, "host": self.host,
            "port": self.port, "uuid": self.uuid, "cast_type": self.cast_type,
        }


@dataclass
class TvResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class TvControl:
    """Lazy, cached TV control. All failures come back as spoken messages."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self._devices: list[CastDevice] = []
        self._discovered_at = 0.0
        self._casts: dict[str, Any] = {}
        self._browser: Any = None
        self._adb_ready: Optional[bool] = None

    # -- discovery ---------------------------------------------------
    def _enabled(self) -> bool:
        return bool(self.settings.tv_enabled)

    def _discover_sync(self) -> list[CastDevice]:
        import pychromecast

        # Keep the browser (and its Zeroconf) alive: the Chromecast objects
        # reconnect through it. Stopping it makes later waits crash.
        if self._browser is not None:
            try:
                pychromecast.discovery.stop_discovery(self._browser)
            except Exception:
                pass
            self._browser = None
        casts, browser = pychromecast.get_chromecasts(timeout=self.settings.tv_discovery_timeout)
        self._browser = browser
        devices: list[CastDevice] = []
        for cast in casts:
            info = cast.cast_info
            device = CastDevice(
                name=str(info.friendly_name or ""),
                model=str(info.model_name or ""),
                host=str(info.host or ""),
                port=int(info.port or 8009),
                uuid=str(info.uuid or ""),
                cast_type=str(getattr(info, "cast_type", "") or ""),
            )
            devices.append(device)
            # Keep the live connection: rebuilding CastInfo by hand is
            # fragile, and discovery already returned ready Chromecasts.
            self._casts[device.uuid or device.host] = cast
        return devices

    def close(self) -> None:
        """Stop mDNS discovery (called on hub shutdown)."""
        if self._browser is not None:
            try:
                import pychromecast
                pychromecast.discovery.stop_discovery(self._browser)
            except Exception:
                pass
            self._browser = None
        self._casts.clear()

    async def discover(self, force: bool = False) -> list[CastDevice]:
        if not self._enabled():
            return []
        fresh = time.monotonic() - self._discovered_at < self.settings.tv_cache_seconds
        if self._devices and fresh and not force:
            return self._devices
        try:
            self._devices = await asyncio.wait_for(
                asyncio.to_thread(self._discover_sync),
                timeout=self.settings.tv_discovery_timeout + 8,
            )
            self._discovered_at = time.monotonic()
            log.info("tv: %d cast device(s): %s", len(self._devices),
                     ", ".join(d.name for d in self._devices) or "none")
        except Exception as exc:
            log.warning("tv discovery failed: %s", exc)
            self._devices = []
            self._discovered_at = time.monotonic()
        return self._devices

    @staticmethod
    def _score(name: str, wanted: str) -> int:
        name_l, wanted_l = name.lower(), wanted.lower()
        if not wanted_l:
            return 1
        if name_l == wanted_l:
            return 100
        if wanted_l in name_l:
            return 60 + len(wanted_l)
        words = [w for w in wanted_l.split() if len(w) > 2]
        return sum(4 for w in words if w in name_l)

    async def _pick(self, wanted: str = "") -> Optional[CastDevice]:
        devices = await self.discover()
        if not devices:
            return None
        target = wanted or self.settings.tv_cast_name
        if not target:
            # Prefer a real TV over a speaker/display when nothing is named.
            devices = sorted(devices, key=lambda d: 0 if "tv" in d.name.lower() else 1)
            return devices[0]
        best = max(devices, key=lambda d: self._score(d.name, target))
        if self._score(best.name, target) <= 0:
            return None
        return best

    async def _cast(self, wanted: str = ""):
        device = await self._pick(wanted)
        if device is None:
            # Discovery may have run before mDNS answered; retry once.
            await self.discover(force=True)
            device = await self._pick(wanted)
            if device is None:
                return None, None
        cast = self._casts.get(device.uuid or device.host)
        if cast is None:
            # Cache miss (hub restarted, or the device was not up earlier).
            await self.discover(force=True)
            device = await self._pick(wanted)
            if device is None:
                return None, None
            cast = self._casts.get(device.uuid or device.host)
        if cast is None:
            return None, device
        try:
            for _ in range(10):
                if getattr(cast, "status", None) is not None:
                    break
                await asyncio.sleep(0.2)
            # ``status`` can arrive while the control socket is still opening.
            await asyncio.to_thread(cast.wait, 5.0)
        except Exception:
            pass
        return cast, device

    def _forget(self) -> None:
        self._casts.clear()
        self._devices = []
        self._discovered_at = 0.0

    # -- casting -----------------------------------------------------
    async def cast_audio(self, url: str, title: str = "",
                         content_type: str = "audio/mp3") -> TvResult:
        if not url:
            return TvResult(False, "I don't have anything to cast.")
        cast, device = await self._cast()
        if cast is None:
            return TvResult(False, self._no_device_message())
        for attempt in range(2):
            try:
                await asyncio.to_thread(
                    cast.media_controller.play_media, url, content_type,
                    title=title or None, stream_type="LIVE",
                )
                await asyncio.sleep(0.4)
                return TvResult(True, f"Playing on {device.name}.", {"device": device.name})
            except Exception as exc:
                # The control socket can still be opening right after discovery.
                if attempt == 0 and "connect" in str(exc).lower():
                    try:
                        await asyncio.to_thread(cast.wait, 8.0)
                    except Exception:
                        pass
                    continue
                log.warning("tv cast failed: %s", exc)
                self._forget()
                return TvResult(False, f"I couldn't cast to the TV: {exc}")
        return TvResult(False, "I couldn't cast to the TV.")

    async def cast_stop(self) -> TvResult:
        return await self._media("stop")

    async def cast_pause(self) -> TvResult:
        return await self._media("pause")

    async def cast_resume(self) -> TvResult:
        return await self._media("play")

    async def _media(self, action: str) -> TvResult:
        cast, device = await self._cast()
        if cast is None:
            return TvResult(False, self._no_device_message())
        verb = {"stop": "Stopped", "pause": "Paused", "play": "Resumed"}[action]
        try:
            controller = cast.media_controller
            await asyncio.to_thread(getattr(controller, action))
            return TvResult(True, f"{verb} on {device.name}.")
        except Exception as exc:
            log.warning("tv %s failed: %s", action, exc)
        # Android TV hands playback to its own player, so the Cast controller has
        # no active session. Use remote keys instead.
        host = await self._adb_host_for_cast()
        if host:
            connected, _problem = await self._adb_connect(host)
            if connected:
                keys = {"stop": ("86", "3"), "pause": ("127",), "play": ("126",)}[action]
                sent = True
                for key in keys:
                    code, _out = await self._adb("shell", "input", "keyevent", key,
                                                 host=host, timeout=8)
                    sent = sent and code == 0
                if sent:
                    return TvResult(True, f"{verb} on {device.name}.")
        return TvResult(False, f"I couldn't {action} the TV playback.")

    async def cast_volume(self, level: Optional[int] = None,
                          direction: str = "", mute: Optional[bool] = None) -> TvResult:
        cast, device = await self._cast()
        if cast is None:
            return TvResult(False, self._no_device_message())
        host = await self._adb_host_for_cast()
        adb_ok = False
        if host:
            adb_ok, _problem = await self._adb_connect(host)
        try:
            if mute is not None:
                await asyncio.to_thread(cast.set_volume_muted, mute)
                return TvResult(True, "Muted the TV." if mute else "Unmuted the TV.")
            if level is not None:
                level = max(0, min(100, level))
                # Cast volume is unreliable on Android TV; the media stream is
                # the same one the remote controls, so prefer ADB.
                if adb_ok and await self._adb_set_volume(level, host):
                    return TvResult(True, f"TV volume set to {level}.")
                await asyncio.to_thread(cast.set_volume, level / 100.0)
                return TvResult(True, f"TV volume set to {level}.")
            if direction in ("up", "down"):
                if adb_ok and await self._adb_adjust_volume(direction, host):
                    return TvResult(True, f"Turned the TV volume {direction}.")
                if direction == "up":
                    await asyncio.to_thread(cast.volume_up)
                else:
                    await asyncio.to_thread(cast.volume_down)
                return TvResult(True, f"Turned the TV volume {direction}.")
            current = getattr(cast.status, "volume_level", None)
            pct = None if current is None else round(current * 100)
            if adb_ok and not pct:
                read = await self._adb_volume(host)
                if read is not None:
                    pct = read
            if pct is None:
                return TvResult(True, "I can't read the TV volume.")
            return TvResult(True, f"TV volume is {pct}.")
        except Exception as exc:
            log.warning("tv volume failed: %s", exc)
            return TvResult(False, "I couldn't change the TV volume.")

    async def status(self) -> TvResult:
        cast, device = await self._cast()
        if cast is None:
            return TvResult(False, self._no_device_message())
        # A freshly discovered cast can answer before its status is populated.
        try:
            if getattr(cast, "status", None) is None:
                await asyncio.to_thread(cast.wait, self.settings.tv_discovery_timeout)
        except Exception as exc:
            log.debug("tv status wait failed: %s", exc)
        status = getattr(cast, "status", None)
        media = getattr(getattr(cast, "media_controller", None), "status", None)
        state = getattr(status, "status_text", "") if status is not None else ""
        display = getattr(status, "display_name", "") if status is not None else ""
        title = getattr(media, "title", "") if media is not None else ""
        player = getattr(media, "player_state", "") if media is not None else ""
        volume = getattr(status, "volume_level", None) if status is not None else None
        volume_pct = None if volume is None else round(volume * 100)

        # An Android TV answers Cast with an empty session and volume 0, so the
        # foreground app and the real music volume come from ADB instead.
        package = ""
        host = await self._adb_host_for_cast()
        if host and (not title or not volume_pct):
            connected, _problem = await self._adb_connect(host)
            if connected:
                if not title:
                    package = await self._adb_focus(host)
                    if package:
                        display = PACKAGE_NAMES.get(package) or package.rsplit(".", 1)[-1].title()
                if not volume_pct:
                    read = await self._adb_volume(host)
                    if read is not None:
                        volume_pct = read

        if title:
            head = f"{device.name} is playing {title}"
            if player:
                head += f" ({player.lower()})"
        elif display:
            head = f"{device.name} is showing {display}"
        else:
            head = f"{device.name} is {state or 'on'}"
        if volume_pct is not None:
            head += f". Volume {volume_pct}"
        return TvResult(True, head + ".", {
            "name": device.name, "state": state, "app": display,
            "package": package, "title": title or "",
            "player_state": player or "",
            "volume": volume_pct,
            "muted": getattr(status, "volume_muted", None) if status is not None else None,
        })

    async def wake(self) -> TvResult:
        """Start the default receiver: wakes the screen through HDMI-CEC."""
        cast, device = await self._cast()
        if cast is None:
            return TvResult(False, self._no_device_message())
        try:
            await asyncio.to_thread(cast.start_app, DEFAULT_RECEIVER)
            return TvResult(True, f"Turned on {device.name}.")
        except Exception as exc:
            log.warning("tv wake failed: %s", exc)
            return TvResult(False, f"I couldn't turn on {device.name}.")

    # -- ADB (Android TV) -------------------------------------------
    def adb_path(self) -> Optional[str]:
        configured = (self.settings.adb_path or "").strip()
        if configured and shutil.which(configured):
            return configured
        return shutil.which("adb")

    def adb_target(self, host: str = "") -> str:
        host = host or self.settings.tv_adb_host
        port = self.settings.tv_adb_port or 5555
        if not host:
            return ""
        return host if ":" in host else f"{host}:{port}"

    async def _adb(self, *args: str, host: str = "", timeout: float = 12.0) -> tuple[int, str]:
        binary = self.adb_path()
        target = self.adb_target(host)
        if not binary:
            return 127, "adb not installed"
        if not target:
            return 128, "no ADB host"
        command = [binary, "-s", target, *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            return proc.returncode or 0, out.decode("utf-8", "replace").strip()
        except asyncio.TimeoutError:
            return 124, "adb timed out"
        except Exception as exc:
            return 1, str(exc)

    async def _adb_connect(self, host: str = "") -> tuple[bool, str]:
        binary = self.adb_path()
        target = self.adb_target(host)
        if not binary:
            return False, "adb is not installed on this PC."
        if not target:
            return False, "I don't know the TV's IP address."
        # "adb connect" can report success while get-state briefly still says
        # "device not found". Retry a couple of times before giving up.
        for attempt in range(3):
            await asyncio.to_thread(self._adb_sync, binary, "connect", target)
            code, out = await self._adb("get-state", host=host, timeout=8)
            state = out.strip().splitlines()[-1] if out.strip() else "unknown"
            if state.startswith("device"):
                break
            await asyncio.sleep(1 + attempt)
        if state.startswith("device"):
            self._adb_ready = True
            self._learn_mac(host)
            return True, ""
        self._adb_ready = False
        if "unauthorized" in state:
            return False, ("The TV is asking to authorise this PC. Accept the "
                           "debugging prompt on the TV, then try again.")
        if "offline" in state:
            return False, "The TV's debugging connection is offline; wake it and try again."
        return False, f"I can't reach the TV's debugging port ({state})."

    @staticmethod
    def _adb_sync(binary: str, *args: str) -> str:
        import subprocess

        try:
            done = subprocess.run([binary, *args], capture_output=True, text=True,
                                  timeout=15, errors="replace")
            return (done.stdout or done.stderr or "").strip()
        except Exception as exc:
            return str(exc)

    async def _adb_host_for_cast(self) -> str:
        if self.settings.tv_adb_host:
            return self.settings.tv_adb_host
        device = await self._pick()
        if device is None:
            # First discovery can come back empty while mDNS warms up.
            await self.discover(force=True)
            device = await self._pick()
        return device.host if device else ""

    # -- ADB readers (Android TV reports little through Cast) ----------
    async def _adb_focus(self, host: str = "") -> str:
        """Foreground package name, or "" when it cannot be read."""
        host = host or await self._adb_host_for_cast()
        if not host:
            return ""
        code, out = await self._adb(
            "shell", "dumpsys window | grep -m1 mCurrentFocus", host=host, timeout=8)
        if code != 0 or not out:
            return ""
        hit = re.search(r"[ ]([A-Za-z][\w.]*)/[\w.$]+", out)
        return hit.group(1) if hit else ""

    async def _adb_volume(self, host: str = "") -> Optional[int]:
        host = host or await self._adb_host_for_cast()
        if not host:
            return None
        code, out = await self._adb(
            "shell", "media volume --stream 3 --get", host=host, timeout=8)
        if code != 0:
            return None
        hit = re.search(r"volume is (\d+)", out)
        return int(hit.group(1)) if hit else None

    async def _adb_set_volume(self, level: int, host: str = "") -> bool:
        level = max(0, min(100, level))
        host = host or await self._adb_host_for_cast()
        if not host:
            return False
        code, _ = await self._adb(
            "shell", f"media volume --stream 3 --set {level}", host=host, timeout=8)
        return code == 0

    async def _adb_adjust_volume(self, direction: str, host: str = "") -> bool:
        verb = "raise" if direction == "up" else "lower"
        host = host or await self._adb_host_for_cast()
        if not host:
            return False
        code, _ = await self._adb(
            "shell", f"media volume --stream 3 --adj {verb}", host=host, timeout=8)
        return code == 0

    async def adb_key(self, key: str) -> TvResult:
        if key not in KEYCODES:
            return TvResult(False, f"I don't know the {key} key.")
        host = await self._adb_host_for_cast()
        ok, problem = await self._adb_connect(host)
        if not ok:
            return TvResult(False, problem)
        code, out = await self._adb("shell", "input", "keyevent", str(KEYCODES[key]), host=host)
        if code != 0:
            return TvResult(False, f"The TV rejected the command: {out[:80]}")
        return TvResult(True, f"Sent {key} to the TV.")

    # -- Wake-on-LAN --------------------------------------------------
    def _mac_file(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "tv_mac.json")

    def _learn_mac(self, host: str = "") -> None:
        """Remember the TV's MAC while it is on, so WoL works when it is off."""
        if not host:
            return
        try:
            with open(self._mac_file(), encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            data = {}
        if data.get(host):
            return
        mac = ""
        binary = self.adb_path()
        for iface in ("eth0", "wlan0"):
            out = self._adb_sync(binary, "shell",
                                 f"cat /sys/class/net/{iface}/address") if binary else ""
            if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", out.strip(), re.I):
                mac = out.strip().lower()
                break
        if not mac:
            try:
                arp = subprocess.run(["arp", "-a", host], capture_output=True, text=True,
                                     timeout=8, errors="replace").stdout
                hit = re.search(r"([0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2}", arp)
                if hit:
                    mac = hit.group(0).replace("-", ":").lower()
            except Exception:
                pass
        if mac:
            data[host] = mac
            try:
                with open(self._mac_file(), "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2)
                log.info("tv: learned MAC %s for %s", mac, host)
            except Exception as exc:
                log.debug("tv MAC save failed: %s", exc)

    def _known_mac(self, host: str = "") -> str:
        configured = (getattr(self.settings, "tv_mac", "") or "").strip()
        if configured:
            return configured.lower()
        try:
            with open(self._mac_file(), encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return ""
        return str(data.get(host) or "").lower()

    async def wake_on_lan(self) -> TvResult:
        """Send a magic packet. Needs TV_MAC or a previously learned MAC."""
        host = await self._adb_host_for_cast()
        mac = self._known_mac(host)
        if not mac or not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac, re.I):
            return TvResult(False, "I don't know the TV's MAC address. Turn the TV on once "
                                   "and I can learn it, or set TV_MAC in .env.")
        packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
        sent = False
        targets = ["255.255.255.255"]
        if host:
            targets.append(host)
        for target in targets:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(packet, (target, 9))
                sock.close()
                sent = True
            except Exception as exc:
                log.debug("WoL send to %s failed: %s", target, exc)
        if not sent:
            return TvResult(False, "I couldn't send the wake packet.")
        log.info("tv: sent Wake-on-LAN for %s", mac)
        return TvResult(True, f"Sent a wake signal to the TV ({mac}).")

    async def _wait_awake(self, seconds: float = 25.0) -> bool:
        """Poll ADB until the TV has finished booting after a wake signal."""
        host = await self._adb_host_for_cast()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            code, out = await self._adb("get-state", host=host, timeout=5)
            if code == 0 and out.strip().splitlines()[-1].startswith("device"):
                return True
            await asyncio.sleep(2)
        return False

    async def ensure_awake(self) -> TvResult:
        """Make sure the TV is reachable, waking it with ADB/WoL/CEC if needed."""
        host = await self._adb_host_for_cast()
        if host:
            ok, _problem = await self._adb_connect(host)
            if ok:
                return TvResult(True, "TV is on.")
        wol = await self.wake_on_lan()
        if wol.ok and await self._wait_awake():
            return TvResult(True, "TV woke up.")
        result = await self.wake()
        if result.ok:
            return result
        if wol.ok:
            return TvResult(False, "I sent the wake signal but the TV did not come online. "
                                   "Some TVs only support Wake-on-LAN over Ethernet.")
        return TvResult(False, "I couldn't wake the TV. Turn it on once so I can learn its "
                               "MAC, or set TV_MAC in .env.")

    async def power_on(self) -> TvResult:
        host = await self._adb_host_for_cast()
        if host:
            ok, _problem = await self._adb_connect(host)
            if ok:
                code, _out = await self._adb("shell", "input", "keyevent",
                                             str(KEYCODES["wake"]), host=host)
                if code == 0:
                    return TvResult(True, "Turned on the TV.")
                # Some boxes ignore WAKE; POWER toggles.
                code, _out = await self._adb("shell", "input", "keyevent",
                                             str(KEYCODES["power"]), host=host)
                if code == 0:
                    return TvResult(True, "Turned on the TV.")
        return await self.ensure_awake()

    async def power_off(self) -> TvResult:
        host = await self._adb_host_for_cast()
        if host:
            for attempt in range(3):
                ok, problem = await self._adb_connect(host)
                if not ok:
                    if "authorise" in problem:
                        return TvResult(False, problem)
                    await asyncio.sleep(1 + attempt)
                    continue
                code, out = await self._adb("shell", "input", "keyevent",
                                            str(KEYCODES["sleep"]), host=host)
                if code == 0:
                    return TvResult(True, "Turned off the TV.")
                code, out = await self._adb("shell", "input", "keyevent",
                                            str(KEYCODES["power"]), host=host)
                if code == 0:
                    return TvResult(True, "Turned off the TV.")
                log.warning("tv power off failed: %s", out)
        return TvResult(False, "I can't turn the TV off without debugging access.")

    async def launch(self, app: str) -> TvResult:
        wanted = app.strip().lower()
        package = APPS.get(wanted, wanted if "." in wanted else "")
        if not package:
            return TvResult(False, f"I don't know the {app} app on this TV.")
        host = await self._adb_host_for_cast()
        ok, problem = await self._adb_connect(host)
        if not ok:
            return TvResult(False, problem)
        code, out = await self._adb("shell", "monkey", "-p", package, "-c",
                                    "android.intent.category.LAUNCHER", "1", host=host)
        if code != 0 or "No activities found" in out or "aborted" in out.lower():
            return TvResult(False, f"I couldn't find {app} on the TV.")
        return TvResult(True, f"Opening {app.title()}.")

    async def adb_view_url(self, url: str, package: str = "") -> TvResult:
        """Open a URL in a native Android TV app via an explicit VIEW intent.

        Wakes the TV first if ADB is unreachable, so media commands work even
        when the screen is off.
        """
        host = await self._adb_host_for_cast()
        ok, problem = await self._adb_connect(host)
        if not ok:
            awake = await self.ensure_awake()
            if not awake.ok:
                return awake
            ok, problem = await self._adb_connect(host)
            if not ok:
                return TvResult(False, problem)
        # adb's remote shell joins argv with spaces; quote the URL so '&' and
        # '?' inside it do not become shell separators on the TV.
        args = ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", "'" + url.replace("'", "'\\''") + "'"]
        if package:
            args += ["-p", package]
        code, out = await self._adb(*args, host=host, timeout=12.0)
        if code != 0 or "Error" in out or "error" in out.lower():
            return TvResult(False, f"Couldn't open that on the TV: {out[:80]}")
        return TvResult(True, "Opened on the TV.")

    async def adb_state(self) -> dict[str, Any]:
        host = await self._adb_host_for_cast()
        info: dict[str, Any] = {"host": host, "adb": bool(self.adb_path())}
        if not host:
            return info
        ok, problem = await self._adb_connect(host)
        info["connected"] = ok
        if not ok:
            info["problem"] = problem
            return info
        code, out = await self._adb("shell", "dumpsys power | grep -m1 'mWakefulness='",
                                    host=host)
        hit = re.search(r"mWakefulness=(\w+)", out)
        info["power"] = hit.group(1) if hit else out[:40]
        info["volume"] = await self._adb_volume(host)
        info["focus"] = await self._adb_focus(host)
        return info

    # -- helpers -----------------------------------------------------
    def _no_device_message(self) -> str:
        if not self._enabled():
            return "TV control is turned off."
        if not self.settings.tv_cast_name:
            return ("I can't find a TV or Chromecast. Make sure it is powered on and "
                    "on the same Wi-Fi, or set TV_CAST_NAME.")
        return f"I can't reach {self.settings.tv_cast_name}. Is it on?"

    async def describe(self) -> str:
        devices = await self.discover(force=True)
        if not devices:
            return "I can't see any Chromecast or TV on the network."
        names = ", ".join(d.name for d in devices)
        return f"I can see: {names}."
