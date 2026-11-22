#!/usr/bin/env python3
"""Helpers for YouTube lookup and flight-search links (no booking, no keys)."""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse

import aiohttp

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/124 Safari/537.36"),
    "Cookie": "CONSENT=YES+1",
}
VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')


async def youtube_url(http: aiohttp.ClientSession, query: str) -> tuple[str, bool]:
    """Return (url, resolved). Falls back to a search URL when scraping fails."""
    try:
        async with http.get(
            "https://www.youtube.com/results",
            params={"search_query": query, "hl": "en"},
            headers=UA, timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            html = await resp.text(errors="ignore")
        hit = VIDEO_ID.search(html)
        if hit:
            return f"https://www.youtube.com/watch?v={hit.group(1)}&autoplay=1", True
    except Exception:
        pass
    return ("https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(query), False)


def _dates(text: str) -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    if re.search(r"\bnext week\b", text, re.IGNORECASE):
        start = today + dt.timedelta(days=7 - today.weekday())
        return start, start + dt.timedelta(days=6)
    if re.search(r"\btomorrow\b", text, re.IGNORECASE):
        start = today + dt.timedelta(days=1)
        return start, start
    return today + dt.timedelta(days=1), today + dt.timedelta(days=8)


def flights_url(text: str) -> tuple[str, dt.date, dt.date, str]:
    """Build a Google Flights search URL; never quote prices."""
    hit = re.search(r"\b(?:to|for|into|towards|a)\s+([A-Za-z][A-Za-z ]{1,25})", text,
                    re.IGNORECASE)
    destination = hit.group(1).strip().title() if hit else "Rome"
    destination = re.sub(r"\b(?:next week|tomorrow|this week|please)$", "", destination,
                         flags=re.IGNORECASE).strip() or "Rome"
    start, end = _dates(text)
    query = (f"Flights from Pisa to {destination} from {start.isoformat()} "
             f"to {end.isoformat()}")
    url = "https://www.google.com/travel/flights?" + urllib.parse.urlencode({
        "q": query, "curr": "EUR", "hl": "en",
    })
    return url, start, end, destination
