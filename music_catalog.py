"""Radio Browser search + local catalog cache for mood/genre requests."""
from __future__ import annotations
import json, os, re, time
from typing import Any, Optional
import aiohttp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_FILE = os.environ.get("MUSIC_CATALOG_FILE") or os.path.join(BASE_DIR, "music_catalog.json")
RADIO_SEARCH_URL = "https://de1.api.radio-browser.info/json/stations/search"

# Queries made only of these words say nothing about what to play ("play some music").
GENERIC = {"some", "any", "a", "an", "the", "me", "my", "something", "anything", "please",
           "music", "musica", "radio", "for", "to", "listen", "play", "station", "stations"}


def _key(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


def _is_generic(key: str) -> bool:
    words = re.sub(r"[^a-z0-9 ]", " ", key).split()
    return not words or all(w in GENERIC for w in words)


class MusicCatalog:
    def __init__(self, http: aiohttp.ClientSession):
        self.http = http
        self.catalog: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if os.path.isfile(CATALOG_FILE):
            try:
                with open(CATALOG_FILE, encoding="utf-8-sig") as h:
                    self.catalog = json.load(h)
            except Exception:
                self.catalog = {}

    def _save(self) -> None:
        try:
            with open(CATALOG_FILE, "w", encoding="utf-8") as h:
                json.dump(self.catalog, h, indent=2)
        except Exception:
            pass

    def remember(self, query: str, station: dict) -> None:
        key = _key(query)
        if _is_generic(key):
            return
        self.catalog[key] = {
            "word": station.get("word") or station.get("name", "").lower().replace(" ", ""),
            "name": station.get("name", ""),
            "url": station.get("url_resolved") or station.get("url", ""),
            "saved_at": time.time(),
        }
        self._save()

    def lookup(self, query: str) -> Optional[dict]:
        key = _key(query)
        if _is_generic(key):
            return None
        entry = self.catalog.get(key)
        if entry and time.time() - entry.get("saved_at", 0) < 86400 * 7:  # 7-day TTL
            return entry
        return None

    async def search_radio_browser(self, query: str, limit: int = 10) -> Optional[dict]:
        """Search Radio Browser by tag/name. Returns best MP3/AAC stream."""
        if not os.environ.get("RADIO_BROWSER_ENABLED", "1") == "1":
            return None
        params = {
            "name": query,
            "limit": str(limit),
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
        }
        try:
            async with self.http.get(
                RADIO_SEARCH_URL, params=params,
                headers={"User-Agent": "OliveHub/1.0"},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                rows = await r.json(content_type=None)
        except Exception:
            return None
        if not isinstance(rows, list):
            return None

        best, best_score = None, 0
        words = [w for w in re.sub(r"[^a-z0-9 ]", " ", query.lower()).split() if len(w) > 2]
        for row in rows:
            url = str(row.get("url_resolved") or "")
            if not url.startswith("http"):
                continue
            codec = str(row.get("codec") or "").upper()
            if codec not in ("MP3", "AAC", "AAC+", "MP3+"):
                continue
            try:
                bitrate = int(row.get("bitrate") or 0)
            except (TypeError, ValueError):
                bitrate = 0
            if bitrate < 96:
                continue
            name = str(row.get("name") or "")
            tags = str(row.get("tags") or "").lower()
            score = 0
            for w in words:
                if w in name.lower():
                    score += 3
                if w in tags:
                    score += 2
            if score > best_score:
                best, best_score = row, score
        if best:
            return {
                "word": re.sub(r"[^a-z0-9]", "", best.get("name", "").lower())[:16] or "radio",
                "name": best.get("name", ""),
                "url": best.get("url_resolved") or best.get("url", ""),
            }
        return None

    async def resolve(self, query: str) -> Optional[dict]:
        """Return cached station, or search Radio Browser and cache it."""
        cached = self.lookup(query)
        if cached:
            return cached
        found = await self.search_radio_browser(query)
        if found:
            self.remember(query, found)
        return found