#!/usr/bin/env python3
"""Small local ICS writer for Olive calendar reminders.

Deliberately independent of olive_hub.py (no circular import): callers pass a
datetime and get the event summary back.  Uses the same calendar.ics path as
calendar_ics.py and writes atomically.
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import uuid

from calendar_ics import _parse_ics_events, calendar_path

HEADER = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Olive//Olive Hub//EN\r\nCALSCALE:GREGORIAN\r\n"


def _esc(text: str) -> str:
    return (str(text or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n"))


def _stamp(value: dt.datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def append_event(summary: str, start: dt.datetime, duration_min: int = 60,
                 description: str = "", path: str = "") -> str:
    """Append one timed VEVENT to the local calendar.  Returns the summary."""
    target = path or calendar_path()
    existing = ""
    if os.path.isfile(target):
        try:
            with open(target, encoding="utf-8-sig", errors="ignore") as handle:
                existing = handle.read()
        except Exception:
            existing = ""
    existing = (existing.replace("\r\n", "\n").replace("\r", "\n")
                .replace("\n", "\r\n"))
    if "BEGIN:VCALENDAR" not in existing:
        existing = HEADER

    end = start + dt.timedelta(minutes=int(duration_min or 60))
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid4().hex}@olive.local",
        "DTSTAMP:" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        f"DTSTART:{_stamp(start)}",
        f"DTEND:{_stamp(end)}",
        f"SUMMARY:{_esc(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_esc(description)}")
    lines.append("END:VEVENT")
    block = "\r\n".join(lines) + "\r\n"

    marker = "END:VCALENDAR"
    index = existing.rfind(marker)
    content = (existing[:index] + block + existing[index:]
               if index >= 0 else existing + block + marker + "\r\n")

    directory = os.path.dirname(target) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".olive-calendar-", suffix=".ics", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)  # type: ignore[name-defined]
        except Exception:
            pass
        raise
    return summary


def events_for(day: dt.date, path: str = "") -> list[str]:
    target = path or calendar_path()
    if not os.path.isfile(target):
        return []
    try:
        with open(target, encoding="utf-8-sig", errors="ignore") as handle:
            return _parse_ics_events(handle.read(), day)
    except Exception:
        return []
