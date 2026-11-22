"""Telegram messenger sender (free, no SDK needed)."""
from __future__ import annotations
import os
from typing import Optional
import aiohttp

async def send_telegram(http: aiohttp.ClientSession, text: str) -> Optional[str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return "Telegram isn't configured. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with http.post(
            url,
            json={"chat_id": chat_id, "text": text[:4000]},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            data = await r.json(content_type=None)
    except Exception as e:
        return f"I couldn't send the message: {e}"

    if data.get("ok"):
        return "Message sent."
    return f"Telegram said: {data.get('description', 'unknown error')}"