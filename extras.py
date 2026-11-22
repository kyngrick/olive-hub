#!/usr/bin/env python3
"""Small, key-free conveniences: shopping list, jokes, quotes, conversions."""
from __future__ import annotations

import os
import random
import re
import tempfile
import time

import aiohttp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SHOPPING_FILE = os.path.join(BASE_DIR, "shopping_list.json")

JOKES = [
    "Why did the tomato turn red? Because it saw the salad dressing.",
    "Why don't scientists trust atoms? Because they make up everything.",
    "Why did the scarecrow win an award? Because he was outstanding in his field.",
    "What do you call fake spaghetti? An impasta.",
    "Why can't a bicycle stand up by itself? It's two tired.",
    "I told my wife she should embrace her mistakes. She gave me a hug.",
    "Why did the coffee file a police report? It got mugged.",
    "What do you call a bear with no teeth? A gummy bear.",
    "Why don't skeletons fight each other? They don't have the guts.",
    "How do you organize a space party? You planet.",
    "Why did the music teacher go to jail? She got caught with too many notes.",
    "What's the best thing about Switzerland? I don't know, but the flag is a big plus.",
]

QUOTES = [
    "The best way to predict the future is to invent it. Alan Kay.",
    "Simplicity is the ultimate sophistication. Leonardo da Vinci.",
    "The only way to do great work is to love what you do. Steve Jobs.",
    "Life is what happens when you're busy making other plans. John Lennon.",
    "It always seems impossible until it's done. Nelson Mandela.",
    "Stay hungry, stay foolish. Steve Jobs.",
]

_UNITS = {
    "km": ("length", 1.609344), "kilometer": ("length", 1.609344),
    "kilometers": ("length", 1.609344), "kilometre": ("length", 1.609344),
    "kilometres": ("length", 1.609344), "kms": ("length", 1.609344),
    "mile": ("length", 1.0), "miles": ("length", 1.0), "mi": ("length", 1.0),
    "meter": ("length", 0.000621371), "meters": ("length", 0.000621371),
    "metre": ("length", 0.000621371), "metres": ("length", 0.000621371),
    "m": ("length", 0.000621371),
    "kg": ("mass", 0.45359237), "kilo": ("mass", 0.45359237),
    "kilos": ("mass", 0.45359237), "kilogram": ("mass", 0.45359237),
    "kilograms": ("mass", 0.45359237),
    "pound": ("mass", 1.0), "pounds": ("mass", 1.0), "lb": ("mass", 1.0),
    "lbs": ("mass", 1.0),
}

_CODES = {
    "dollar": "USD", "dollars": "USD", "usd": "USD",
    "euro": "EUR", "euros": "EUR", "eur": "EUR",
    "pound": "GBP", "pounds": "GBP", "sterling": "GBP", "gbp": "GBP",
    "yen": "JPY", "jpy": "JPY", "franc": "CHF", "francs": "CHF", "chf": "CHF",
}
_FX_CACHE: dict[tuple[str, str], tuple[float, float]] = {}


class ShoppingList:
    def __init__(self, path: str = SHOPPING_FILE) -> None:
        self.path = path
        self.items: list[str] = []
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8-sig") as handle:
                data = __import__("json").load(handle)
            if isinstance(data, list):
                self.items = [str(x) for x in data if str(x).strip()]
        except Exception:
            self.items = []

    def _save(self) -> None:
        import json
        directory = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".olive-shop-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.items, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise

    def add(self, item: str) -> str:
        item = re.sub(r"\s+", " ", str(item or "")).strip(" ,.;")
        if not item:
            return "What should I add?"
        if any(i.lower() == item.lower() for i in self.items):
            return f"{item.title()} is already on the list."
        self.items.append(item)
        self._save()
        return f"Added {item.title()}."

    def remove(self, item: str) -> str:
        item = str(item or "").strip().lower()
        before = len(self.items)
        self.items = [i for i in self.items if i.lower() != item]
        if len(self.items) == before:
            return f"{item.title()} isn't on the list."
        self._save()
        return f"Removed {item.title()}."

    def clear(self) -> str:
        self.items = []
        self._save()
        return "Shopping list cleared."

    def read(self) -> str:
        if not self.items:
            return "Your shopping list is empty."
        if len(self.items) == 1:
            return f"Your shopping list has one item: {self.items[0]}."
        return f"Your shopping list has {len(self.items)} items: " + ", ".join(self.items) + "."


def joke() -> str:
    return random.choice(JOKES)


def quote() -> str:
    return random.choice(QUOTES)


def convert_units(text: str) -> str | None:
    hit = re.search(r"\b(\d+(?:\.\d+)?)\s*([a-zA-Z°]+)\s+(?:to|in|into)\s+([a-zA-Z°]+)\b",
                    text, re.IGNORECASE)
    if not hit:
        return None
    amount, src, dst = float(hit.group(1)), hit.group(2).lower(), hit.group(3).lower()
    if src in ("c", "celsius", "centigrade") and dst in ("f", "fahrenheit"):
        value = amount * 9 / 5 + 32
        return f"{amount:g} Celsius is {value:.1f} Fahrenheit."
    if src in ("f", "fahrenheit") and dst in ("c", "celsius", "centigrade"):
        value = (amount - 32) * 5 / 9
        return f"{amount:g} Fahrenheit is {value:.1f} Celsius."
    a, b = _UNITS.get(src), _UNITS.get(dst)
    if not a or not b or a[0] != b[0]:
        return None
    value = amount * b[1] / a[1]
    return f"{amount:g} {src} is {value:.2f} {dst}."


async def convert_currency(http: aiohttp.ClientSession, amount: float,
                           src: str, dst: str) -> str | None:
    src = _CODES.get(src.lower(), src).upper()
    dst = _CODES.get(dst.lower(), dst).upper()
    cached = _FX_CACHE.get((src, dst))
    if cached and time.monotonic() - cached[0] < 6 * 3600:
        rate = cached[1]
    else:
        try:
            async with http.get(
                "https://api.frankfurter.app/latest",
                params={"from": src, "to": dst},
                timeout=aiohttp.ClientTimeout(total=6),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
            rate = float(data["rates"][dst])
            _FX_CACHE[(src, dst)] = (time.monotonic(), rate)
        except Exception:
            return None
    return f"{amount:g} {src} is {amount * rate:.2f} {dst}."
