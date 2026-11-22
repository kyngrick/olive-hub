#!/usr/bin/env python3
"""Cached, rate-limited SerpApi Google Flights lookup for Olive Hub.

The free SerpApi allowance is small, so every successful result is cached on
disk for 60 minutes and a daily search cap is enforced. SerpApi's own cache is
also used (no_cache is never set), which makes identical repeated searches free.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import urllib.parse

import aiohttp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "flight_search_cache.json")
CACHE_TTL = 3600  # seconds

IATA = {
    "rome": "FCO", "roma": "FCO", "milan": "MXP", "milano": "MXP",
    "florence": "FLR", "fir": "FLR", "venice": "VCE", "venezia": "VCE",
    "paris": "CDG", "london": "LHR", "london gatwick": "LGW",
    "new york": "JFK", "nyc": "JFK", "amsterdam": "AMS", "barcelona": "BCN",
    "berlin": "BER", "munich": "MUC", "monaco": "MCM", "zurich": "ZRH",
    "geneva": "GVA", "lisbon": "LIS", "lisbona": "LIS", "madrid": "MAD",
    "athens": "ATH", "prague": "PRG", "vienna": "VIE", "dublin": "DUB",
    "pisa": "PSA", "pisa international": "PSA", "bologna": "BLQ",
    "naples": "NAP", "napoli": "NAP", "palermo": "PMO", "turin": "TRN",
    "torino": "TRN", "nice": "NCE", "marseille": "MRS", "brussels": "BRU",
}


def _config() -> tuple[str, str, int]:
    # Lazy: olive_hub loads .env in main(), after imports.
    key = os.environ.get("SERPAPI_API_KEY", "").strip()
    origin = os.environ.get("FLIGHT_ORIGIN", "PSA").strip().upper()
    try:
        limit = max(1, int(os.environ.get("SERPAPI_DAILY_LIMIT", "5")))
    except ValueError:
        limit = 5
    return key, origin, limit


def city_iata(name: str) -> str:
    raw = str(name or "").strip().lower()
    raw = " ".join(raw.replace("'", "").split())
    if raw in IATA:
        return IATA[raw]
    for key, code in IATA.items():
        if key in raw or raw in key:
            return code
    return raw[:3].upper() if len(raw) >= 3 else raw.upper()


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and "entries" in data:
            return data
    except Exception:
        pass
    return {"entries": {}, "counts": {}}


def _save_cache(cache: dict) -> None:
    directory = os.path.dirname(CACHE_FILE) or "."
    fd, tmp = tempfile.mkstemp(prefix=".olive-flights-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def status() -> dict:
    key, origin, limit = _config()
    cache = _load_cache()
    today = dt.date.today().isoformat()
    return {
        "provider": "serpapi" if key else "link-only",
        "origin": origin,
        "daily_limit": limit,
        "searches_today": int(cache.get("counts", {}).get(today, 0)),
        "cached_results": len(cache.get("entries", {})),
    }


async def search(http: aiohttp.ClientSession, destination: str, outbound: str,
                 return_date: str = "", adults: int = 1) -> dict:
    key, origin, limit = _config()
    if not key:
        return {"ok": False, "reason": "not_configured"}
    dest = city_iata(destination)
    cache = _load_cache()
    cache_key = "|".join([origin, dest, outbound, return_date, str(adults)])
    entries = cache.setdefault("entries", {})
    hit = entries.get(cache_key)
    if hit and (dt.datetime.now().timestamp() - float(hit.get("at", 0))) < CACHE_TTL:
        return {**hit["data"], "cached": True, "destination": dest, "origin": origin}

    day = dt.date.today().isoformat()
    counts = cache.setdefault("counts", {})
    if int(counts.get(day, 0)) >= limit:
        return {"ok": False, "reason": "daily_limit", "searches_today": counts.get(day, 0)}

    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": dest,
        "outbound_date": outbound,
        "currency": "EUR", "gl": "it", "hl": "en",
        "type": "1" if return_date else "2",
        "adults": adults,
        "api_key": key,
    }
    if return_date:
        params["return_date"] = return_date
    try:
        async with http.get(
            "https://serpapi.com/search", params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception as exc:
        return {"ok": False, "reason": "request_failed", "error": str(exc)[:120]}

    if not isinstance(data, dict) or data.get("error"):
        return {"ok": False, "reason": "api_error",
                "error": str((data or {}).get("error", "unknown"))[:120]}

    offers = []
    for item in (data.get("best_flights") or [])[:5]:
        legs = item.get("flights") or []
        if not legs:
            continue
        first = legs[0]
        depart = str((first.get("departure_airport") or {}).get("time") or "")[-5:]
        arrive = str((first.get("arrival_airport") or {}).get("time") or "")[-5:]
        offers.append({
            "airline": first.get("airline") or "an airline",
            "price": item.get("price"),
            "depart": depart,
            "arrive": arrive,
            "duration_min": item.get("total_duration"),
            "stops": len(item.get("layovers") or []),
        })
    result = {
        "ok": True,
        "offers": offers,
        "lowest": (data.get("price_insights") or {}).get("lowest_price"),
        "destination": dest,
        "origin": origin,
        "url": "https://www.google.com/travel/flights?" + urllib.parse.urlencode({
            "q": f"Flights from {origin} to {dest} on {outbound}", "curr": "EUR", "hl": "en",
        }),
    }
    entries[cache_key] = {"at": dt.datetime.now().timestamp(), "data": result}
    counts[day] = int(counts.get(day, 0)) + 1
    cache["counts"] = counts
    _save_cache(cache)
    return {**result, "cached": False, "searches_today": counts[day]}
