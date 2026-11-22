#!/usr/bin/env python3
"""Client for the local whatsapp-web.js bridge (localhost only)."""
from __future__ import annotations

import os
from typing import Any, Optional

import aiohttp

def _config() -> tuple[str, str]:
    # Read lazily: the hub loads .env in main(), after this module is imported.
    base = os.environ.get("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3011").rstrip("/")
    token = os.environ.get("WHATSAPP_BRIDGE_TOKEN", "")
    return base, token


def _headers(token: str) -> dict[str, str]:
    return {"X-Bridge-Token": token, "Content-Type": "application/json"}


async def _request(http: aiohttp.ClientSession, method: str, path: str,
                   payload: Optional[dict] = None, timeout: float = 6.0) -> dict[str, Any]:
    base, token = _config()
    if not token:
        return {"ok": False, "error": "WHATSAPP_BRIDGE_TOKEN is not configured"}
    try:
        async with http.request(
            method, base + path, headers=_headers(token), json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            data = await resp.json(content_type=None)
            return data if isinstance(data, dict) else {"ok": False, "error": "bad bridge response"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def status(http: aiohttp.ClientSession) -> dict[str, Any]:
    return await _request(http, "GET", "/health")


async def contacts(http: aiohttp.ClientSession, query: str = "") -> dict[str, Any]:
    from urllib.parse import quote
    return await _request(http, "GET", "/contacts?q=" + quote(query), timeout=45.0)


async def send(http: aiohttp.ClientSession, to: str, text: str) -> dict[str, Any]:
    return await _request(http, "POST", "/send", {"to": to, "text": text[:4000]}, timeout=30.0)
