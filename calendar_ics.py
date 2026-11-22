"""Minimal ICS calendar reader for today's events."""
from __future__ import annotations
import datetime as dt, os, re
from typing import Optional
import aiohttp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def calendar_path() -> str:
    """Resolved local ICS path, shared by the reader, writer and /health."""
    path = os.environ.get("CALENDAR_ICS_FILE") or "calendar.ics"
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)

def _parse_ics_events(text: str, day: dt.date) -> list[str]:
    """Extract event summaries starting on `day` from ICS text."""
    events = []
    # Split into VEVENT blocks
    for block in re.split(r"BEGIN:VEVENT", text)[1:]:
        summary = re.search(r"SUMMARY:(.+)", block)
        dtstart = re.search(r"DTSTART(?:;[^:]*)?:(\d{8})", block)
        if summary and dtstart:
            try:
                event_date = dt.datetime.strptime(dtstart.group(1), "%Y%m%d").date()
                if event_date == day:
                    text = (summary.group(1).strip()
                            .replace("\\,", ",").replace("\\;", ";")
                            .replace("\\n", " ").replace("\\\\", "\\"))
                    events.append(text)
            except ValueError:
                pass
    return events

async def get_today_events(http: aiohttp.ClientSession) -> Optional[str]:
    """Read ICS from URL or local file, return spoken summary."""
    url = os.environ.get("CALENDAR_ICS_URL", "").strip()
    path = calendar_path()

    text = None
    if url:
        try:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    text = await r.text()
        except Exception:
            pass
    if not text and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8-sig", errors="ignore") as h:
                text = h.read()
        except Exception:
            pass
    if not text:
        return None

    events = _parse_ics_events(text, dt.date.today())
    if not events:
        return "You have no events today."
    if len(events) == 1:
        return f"You have one event today: {events[0]}."
    return f"You have {len(events)} events today: " + ", ".join(events) + "."