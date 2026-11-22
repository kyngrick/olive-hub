#!/usr/bin/env python3
"""Key-free place lookup via OpenStreetMap Nominatim (with a small cache)."""
from __future__ import annotations

import asyncio
import time
import urllib.parse
from typing import Optional

import aiohttp

UA = {"User-Agent": "OliveHub/1.0 (local voice assistant; contact: local)"}


class Places:
    def __init__(self, http: aiohttp.ClientSession, settings) -> None:
        self.http = http
        self.settings = settings
        self._cache: dict[str, tuple[float, list[str]]] = {}

    async def search(self, what: str, city: str = "") -> list[str]:
        key = f"{what.lower()}|{city.lower()}"
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
        params = {
            "q": f"{what} in {city}" if city else what,
            "format": "jsonv2", "limit": 8, "extratags": 1, "addressdetails": 1,
        }
        if not city and self.settings.lat is not None and self.settings.lon is not None:
            dlat, dlon = 0.012, 0.055
            params["viewbox"] = (f"{self.settings.lon - dlon},{self.settings.lat - dlat},"
                                 f"{self.settings.lon + dlon},{self.settings.lat + dlat}")
            params["bounded"] = 1
        try:
            async with self.http.get(
                "https://nominatim.openstreetmap.org/search", params=params,
                headers=UA, timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    return []
                rows = await resp.json(content_type=None)
        except Exception:
            return []
        names: list[str] = []
        for row in rows if isinstance(rows, list) else []:
            name = str(row.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
        names = names[:5]
        self._cache[key] = (time.monotonic(), names)
        # Nominatim policy: at most one request per second.
        await asyncio.sleep(1.0)
        return names

    @staticmethod
    def map_url(what: str, city: str = "") -> str:
        query = f"{what} in {city}" if city else what
        return "https://www.openstreetmap.org/search?query=" + urllib.parse.quote(query)
