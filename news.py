"""Headlines for the hub: Google News RSS first, BBC/GDELT as fallbacks.

No API key is required. Results are cached so repeated questions do not hammer
the public feeds (GDELT in particular allows roughly one request every 5 s).
"""
from __future__ import annotations

import html
import os
import re
import time
import urllib.parse
from typing import Optional

import aiohttp

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS = "https://news.google.com/rss"
BBC_TOP = "https://feeds.bbci.co.uk/news/rss.xml"

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 600.0
_UA = {"User-Agent": "OliveHub/1.0 (+local assistant)"}


def _enabled() -> bool:
    return os.environ.get("NEWS_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def _clean_title(raw: str) -> str:
    title = html.unescape(re.sub(r"\s+", " ", raw)).strip()
    # Google News appends " - Publisher"; drop it for a cleaner spoken line.
    if " - " in title:
        head, _, tail = title.rpartition(" - ")
        if head and tail and len(tail) <= 45 and len(tail.split()) <= 5:
            title = head.strip()
    return title


def _parse_items(xml_text: str, limit: int = 3) -> list[str]:
    titles: list[str] = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text, re.S | re.I):
        hit = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.S | re.I)
        if not hit:
            continue
        title = _clean_title(hit.group(1))
        if title:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


async def _fetch(http: aiohttp.ClientSession, url: str,
                 params: Optional[dict] = None) -> Optional[str]:
    try:
        async with http.get(url, params=params, headers=_UA,
                            timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status != 200:
                return None
            raw = await response.read()
    except Exception:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


async def _google_news(http: aiohttp.ClientSession, query: str,
                       limit: int) -> Optional[str]:
    if query:
        url = f"{GOOGLE_NEWS}/search"
        params = {"q": f"{query} when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    else:
        url = GOOGLE_NEWS
        params = {"hl": "en-US", "gl": "US", "ceid": "US:en"}
    text = await _fetch(http, url, params)
    if not text:
        return None
    titles = _parse_items(text, limit)
    return _spoken(titles) if titles else None


async def _gdelt(http: aiohttp.ClientSession, query: str, country: str,
                 limit: int) -> Optional[str]:
    params = {
        "query": (query or "news") + (f" sourcecountry:{country.lower()}" if country else ""),
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(limit),
        "timespan": "24h",
    }
    try:
        async with http.get(GDELT_URL, params=params, headers=_UA,
                            timeout=aiohttp.ClientTimeout(total=12)) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
    except Exception:
        return None
    titles = [str(a.get("title") or "").strip()
              for a in (data.get("articles") or [])[:limit]]
    titles = [t for t in titles if t]
    return _spoken(titles) if titles else None


def _spoken(titles: list[str]) -> str:
    cleaned = [t.rstrip(" .!?") + "." for t in titles if t]
    return "Here's the latest. " + " ".join(cleaned)


async def search_news(http: aiohttp.ClientSession, query: str = "",
                      country: str = "", max_records: int = 3) -> Optional[str]:
    """Return a short spoken summary of today's news matching query/country."""
    if not _enabled():
        return "News is turned off."

    topic = " ".join(part for part in (query.strip(), country.strip()) if part)
    cached = _CACHE.get(topic)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    summary = await _google_news(http, topic, max_records)
    if not summary and not topic:
        text = await _fetch(http, BBC_TOP)
        titles = _parse_items(text, max_records) if text else []
        if titles:
            summary = _spoken(titles)
    if not summary:
        summary = await _gdelt(http, query, country, max_records)
    if not summary:
        return None

    _CACHE[topic] = (time.time(), summary)
    return summary
