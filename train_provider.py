#!/usr/bin/env python3
"""Train times and location lookup using the free Transitous/MOTIS API.

No API key needed. Transitous is a community-run open public-transport router
with European (and wider) coverage. Open feeds publish times but rarely ticket
prices, so a real price-check link is returned instead of a guessed cost.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from typing import Any, Optional

import aiohttp

BASE = "https://api.transitous.org"
HEADERS = {"User-Agent": "OliveHub/1.0 (local voice assistant; contact: olive@localhost)"}
TIMEOUT = aiohttp.ClientTimeout(total=30)

MONTHS = {name: i for i, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}


def _fmt_time(iso: str) -> str:
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M")
    except Exception:
        return iso[11:16] if len(iso) > 16 else iso


def _fmt_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours}h {minutes}min"
    return f"{hours}h" if hours else f"{minutes}min"


async def geocode(session: aiohttp.ClientSession, query: str) -> Optional[dict[str, Any]]:
    """Resolve a place/station name. Trust the API's ranking, but prefer an
    exact-name result in the user's usual country (Italy) when available."""
    try:
        async with session.get(BASE + "/api/v1/geocode",
                               params={"text": query, "numResults": 10},
                               headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            items = await resp.json(content_type=None)
    except Exception:
        return None
    if not isinstance(items, list) or not items:
        return None

    q = re.sub(r"\b(?:stazione|station|centrale)\b", "", query, flags=re.I).strip().lower()
    chosen = items[0]
    for item in items:
        name = re.sub(r"\b(?:stazione|station|centrale)\b", "",
                      str(item.get("name") or ""), flags=re.I).strip().lower()
        if name == q and item.get("country") == "IT":
            chosen = item
            break

    return {"name": chosen.get("name") or query,
            "id": chosen.get("id") or "",
            "lat": float(chosen.get("lat") or 0),
            "lon": float(chosen.get("lon") or 0),
            "country": chosen.get("country") or "",
            "tz": chosen.get("tz") or ""}


async def reverse(session: aiohttp.ClientSession, lat: float, lon: float) -> dict[str, Any]:
    """Describe coordinates using the nearest place/stop."""
    try:
        async with session.get(BASE + "/api/v1/reverse-geocode",
                               params={"place": f"{lat},{lon}", "numResults": 3},
                               headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return {}
            items = await resp.json(content_type=None)
            if isinstance(items, list) and items:
                hit = items[0]
                return {"name": hit.get("name") or "",
                        "country": hit.get("country") or "",
                        "type": hit.get("type") or ""}
    except Exception:
        pass
    return {}


async def plan(session: aiohttp.ClientSession, from_place: dict[str, Any],
               to_place: dict[str, Any], when: Optional[dt.datetime] = None,
               count: int = 3) -> list[dict[str, Any]]:
    """Return simple itineraries with times, duration and train names."""
    params: dict[str, Any] = {
        "fromPlace": f"{from_place['lat']},{from_place['lon']}",
        "toPlace": f"{to_place['lat']},{to_place['lon']}",
        "numItineraries": count,
        "withFares": "true",
    }
    if when is not None:
        params["time"] = when.astimezone().isoformat()
    try:
        async with session.get(BASE + "/api/v6/plan", params=params,
                               headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
    except Exception:
        return []

    results = []
    for it in (data.get("itineraries") or [])[:count]:
        trains = []
        prices = []
        rail_modes = {"HIGHSPEED_RAIL", "LONG_DISTANCE", "REGIONAL_RAIL",
                      "NIGHT_RAIL", "REGIONAL_FAST_RAIL", "INTERCITY_RAIL",
                      "SUBURBAN", "TRANSIT", "RAIL"}
        for leg in it.get("legs") or []:
            mode = str(leg.get("mode") or "")
            if mode in rail_modes or "RAIL" in mode:
                name = (leg.get("routeLongName") or leg.get("displayName")
                        or leg.get("routeShortName") or leg.get("headsign")
                        or "train")
                trains.append(str(name).strip())
            for key, value in leg.items():
                if "price" in key.lower() and isinstance(value, (int, float)):
                    prices.append(float(value))
        results.append({
            "depart": _fmt_time(it.get("startTime") or ""),
            "arrive": _fmt_time(it.get("endTime") or ""),
            "duration": _fmt_duration(it.get("duration") or 0),
            "seconds": int(it.get("duration") or 0),
            "transfers": int(it.get("transfers") or 0),
            "trains": ", ".join(dict.fromkeys(trains)) or "train",
            "prices": prices,
        })
    return results


def price_url(from_name: str, to_name: str, when: Optional[dt.datetime] = None) -> str:
    q = urllib.parse.quote_plus(f"{from_name} to {to_name} train tickets price")
    return "https://www.google.com/search?q=" + q


def parse_request(text: str, now: Optional[dt.datetime] = None) -> dict[str, Any]:
    """Extract origin, destination and travel time from a spoken request."""
    now = now or dt.datetime.now()
    clean = re.sub(r"^(?:alexa[,\s]+)?(?:(?:please|thanks)\s+)?", "", str(text or "").strip(),
                   flags=re.IGNORECASE)

    origin = destination = ""
    m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:\s+(?:on|at|for|next|tomorrow|today|this)\b|$)",
                  clean, re.IGNORECASE)
    if not m:
        m = re.search(r"\bbetween\s+(.+?)\s+and\s+(.+?)(?:\s+(?:on|at|for|next|tomorrow|today|this)\b|$)",
                      clean, re.IGNORECASE)
    if m:
        origin = m.group(1).strip(" ,.?")
        destination = m.group(2).strip(" ,.?")
        destination = re.sub(r"\b(?:tomorrow|today|tonight|next\s+\w+day)\b.*$", "",
                             destination, flags=re.I).strip(" ,.?") or destination

    low = clean.lower()
    base = now.replace(second=0, microsecond=0)
    when = None
    hour = minute = None

    # A clock time is only a departure time when introduced by "at" or a day.
    hm = re.search(r"\bat\s+(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\b", low)
    if hm:
        hour, minute = int(hm.group(1)), int(hm.group(2) or 0)
        amp = hm.group(3)
        if amp == "pm" and hour < 12:
            hour += 12
        elif amp == "am" and hour == 12:
            hour = 0

    day_set = False
    if re.search(r"\btomorrow\b", low):
        base += dt.timedelta(days=1)
        day_set = True
    elif re.search(r"\btonight\b", low):
        day_set = True
    elif re.search(r"\btoday\b", low):
        day_set = True
    else:
        md = re.search(r"\bon\s+(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)", low)
        if md and md.group(2) in MONTHS:
            day, month = int(md.group(1)), MONTHS[md.group(2)]
            target = dt.datetime(now.year, month, day)
            if target.date() < now.date():
                target = target.replace(year=now.year + 1)
            base = target
            day_set = True
        else:
            wd = re.search(r"\bnext\s+([a-z]+day)\b", low)
            if wd and wd.group(1) in WEEKDAYS:
                days = (WEEKDAYS[wd.group(1)] - now.weekday()) % 7 or 7
                base += dt.timedelta(days=days)
                day_set = True

    if hour is not None and 0 <= hour <= 23 and 0 <= minute <= 59:
        when = base.replace(hour=hour, minute=minute)
    elif day_set:
        when = base.replace(hour=8, minute=0)

    return {"origin": origin, "destination": destination, "when": when,
            "clean": clean}
