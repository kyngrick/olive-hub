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
URL_CACHE_TTL = 4 * 3600  # GoogleVideo URLs are signed and expire in hours

_client: Any = None
_init_tried = False
_url_cache: dict[str, tuple[float, str]] = {}


def _normalise_headers(raw: str) -> str:
    """Accept raw 'key: value' lines or a cURL command copied from DevTools."""
    if re.match(r"^\s*curl(\.exe)?\s", raw, re.IGNORECASE):
        pairs = re.findall(r"""-H\s+(?:'([^']*)'|"([^"]*)")""", raw,
                           re.IGNORECASE | re.DOTALL)
        lines = [before or after for before, after in pairs]
        if not lines:
            raise ValueError("cURL command contained no -H headers")
        raw = "\n".join(lines)
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


def stream_url(video_id: str) -> str:
    """Resolve a direct audio stream URL for a video, cached for a few hours."""
    video_id = str(video_id or "")
    if not video_id:
        return ""
    cached = _url_cache.get(video_id)
    if cached and time.time() - float(cached[0]) < URL_CACHE_TTL:
        return str(cached[1])
    try:
        import yt_dlp
        opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "quiet": True,
            "noplaylist": True,
            "skip_download": True,
            "http_headers": _http_headers(),
        }
        with yt_dlp.YoutubeDL(opts) as downloader:
            info = downloader.extract_info(
                "https://www.youtube.com/watch?v=" + video_id, download=False)
        url = str((info or {}).get("url") or "")
    except Exception as exc:
        log.warning("ytmusic: stream resolution failed for %s (%s)", video_id, exc)
        return ""
    if url:
        _url_cache[video_id] = (time.time(), url)
    return url
