"""YouTube Music provider using the user's own logged-in browser session.

Credentials stay local:
  ytmusic-headers.txt  raw request headers copied from Chrome DevTools
  ytmusic-auth.json    converted auth file (generated once, gitignored)

No card details, no trial signup, no external services. Optional module: the
hub keeps working with the free preview sources when neither file exists.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

log = logging.getLogger("OLIVE-HUB")
ROOT = os.path.dirname(os.path.abspath(__file__))
HEADERS_FILE = os.path.join(ROOT, "ytmusic-headers.txt")
AUTH_FILE = os.path.join(ROOT, "ytmusic-auth.json")

_client: Any = None
_init_tried = False


def _normalise_headers(raw: str) -> str:
    """Accept raw 'key: value' lines or a cURL command copied from DevTools."""
    if re.match(r"^\s*curl(\.exe)?\s", raw, re.IGNORECASE):
        pairs = re.findall(r"""-H\s+(?:'([^']*)'|"([^"]*)")""", raw,
                           re.IGNORECASE | re.DOTALL)
        lines = [before or after for before, after in pairs]
        if not lines:
            raise ValueError("cURL command contained no -H headers")
        raw = "\n".join(lines)
    # Newer Chrome DevTools copies headers as alternating name/value lines
    # (":authority" / "music.youtube.com", "cookie" / "SOCS=..."), without
    # the "name: value" separator. Rebuild that into the classic format.
    entries = [line.strip() for line in raw.splitlines() if line.strip()]
    bare_cookie = re.search(r"(?mi)^cookie\s*$", raw)
    if entries and bare_cookie and not re.search(r"(?mi)^cookie\s*:", raw):
        paired: list[str] = []
        for index in range(0, len(entries) - 1, 2):
            key, value = entries[index], entries[index + 1]
            if re.match(r"^[A-Za-z0-9-]+$", key):
                paired.append(f"{key}: {value}")
        if paired:
            raw = "\n".join(paired)
    if not re.search(r"(?mi)^cookie\s*:", raw):
        raise ValueError("no 'cookie:' header found in the pasted text")
    # Some logged-in requests omit this header; ytmusicapi requires it.
    # "0" selects the (single) default signed-in Google account.
    if not re.search(r"(?mi)^x-goog-authuser\s*:", raw):
        raw = raw.rstrip() + "\nx-goog-authuser: 0\n"
    return raw


def ensure_auth() -> bool:
    """Convert pasted DevTools headers into a ytmusicapi auth file once."""
    if os.path.exists(AUTH_FILE):
        return True
    if not os.path.exists(HEADERS_FILE):
        return False
    try:
        with open(HEADERS_FILE, encoding="utf-8-sig") as handle:
            raw = _normalise_headers(handle.read())
        from ytmusicapi import setup
        setup(headers_raw=raw, filepath=AUTH_FILE)
        log.info("ytmusic: generated %s from pasted headers", os.path.basename(AUTH_FILE))
    except Exception as exc:
        log.warning("ytmusic: could not convert headers (%s)", exc)
        return False
    return os.path.exists(AUTH_FILE)


def client() -> Any:
    """Return an authenticated YTMusic client, or None when not configured."""
    global _client, _init_tried
    if _client is not None:
        return _client
    if _init_tried and not os.path.exists(AUTH_FILE):
        return None
    _init_tried = True
    if not ensure_auth():
        return None
    try:
        from ytmusicapi import YTMusic
        _client = YTMusic(auth=AUTH_FILE)
        log.info("ytmusic: authenticated with the local account session")
        return _client
    except Exception as exc:
        log.warning("ytmusic: authentication failed (%s)", exc)
        return None


def available() -> bool:
    return client() is not None


def search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Search the user's own YouTube Music account; return normalised tracks."""
    yt = client()
    if yt is None:
        return []
    try:
        rows = yt.search(str(query or ""), filter="songs", limit=max(1, limit)) or []
    except Exception as exc:
        log.warning("ytmusic search failed for %r: %s", query, exc)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        video_id = str(row.get("videoId") or "")
        if not video_id:
            continue
        artists = row.get("artists") or []
        artist = ""
        if artists and isinstance(artists[0], dict):
            artist = str(artists[0].get("name") or "")
        album = row.get("album") or {}
        out.append({
            "source": "ytmusic",
            "id": video_id,
            "videoId": video_id,
            "title": str(row.get("title") or "Unknown"),
            "artist": artist or "Unknown",
            "album": str(album.get("name") or "") if isinstance(album, dict) else "",
            "duration": str(row.get("duration") or ""),
        })
    return out


def _http_headers() -> dict[str, str]:
    try:
        with open(AUTH_FILE, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    keys = ("cookie", "user-agent", "x-goog-visitor-id")
    return {k: str(data[k]) for k in keys if data.get(k)}


CACHE_DIR = os.path.join(ROOT, "yt_cache")
CACHE_TTL = 7 * 24 * 3600


def _cleanup_cache() -> None:
    try:
        now = time.time()
        for name in os.listdir(CACHE_DIR):
            path = os.path.join(CACHE_DIR, name)
            if os.path.isfile(path) and now - os.path.getmtime(path) > CACHE_TTL:
                os.remove(path)
    except Exception:
        pass


def download(video_id: str) -> str:
    """Download a track once into the local cache; return its file path.

    A local file with a known Content-Length is far more reliable on the
    Echo than a 1 KB signed GoogleVideo URL (which the speaker rejects).
    """
    vid = re.sub(r"[^A-Za-z0-9_-]", "", str(video_id or ""))
    if len(vid) != 11:
        return ""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, vid + ".mp3")
    if os.path.isfile(path) and os.path.getsize(path) > 1024:
        return path
    try:
        import yt_dlp
        import imageio_ffmpeg
        opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "quiet": True,
            "noplaylist": True,
            "outtmpl": os.path.join(CACHE_DIR, vid),
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }],
            "http_headers": _http_headers(),
            # YouTube serves SABR-only formats that need an n-challenge solver;
            # Node.js is already installed for the WhatsApp bridge.
            "js_runtimes": {"node": {}},
            "remote_components": ["ejs:github"],
        }
        with yt_dlp.YoutubeDL(opts) as downloader:
            downloader.extract_info(
                "https://www.youtube.com/watch?v=" + vid, download=True)
    except Exception as exc:
        log.warning("ytmusic: download failed for %s (%s)", vid, exc)
        if os.path.isfile(path):
            os.remove(path)
        return ""
    if not os.path.isfile(path) or os.path.getsize(path) < 1024:
        return ""
    _cleanup_cache()
    log.info("ytmusic: cached %s (%d bytes)", vid, os.path.getsize(path))
    return path
