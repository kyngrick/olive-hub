# Olive Hub — feature build log (22 Nov 2026, second session)

This log covers the new capabilities requested after the final audit. All were
researched, implemented, restarted into the live hub and tested.

## 1. Routines — "turn on the kitchen light 4 minutes before sunset"

- `Weather.sunset()` added: Open-Meteo `daily=sunset`, `timezone=auto`, uses the
  API's `utc_offset_seconds` so Rome DST is correct. Picks the next future sunset.
- `ScheduleItem` / `Scheduler` extended with optional `action` and `target`
  fields (legacy JSON still loads). A scheduled routine fires an async callback
  that reuses `home_toggle()` → local IoT → HA → Tuya fallback.
- New intent `routine.sunset` is registered **before** `ha.on` so it schedules
  instead of switching the light on immediately.
- Verified live:
  `turn on the kitchen light 4 minutes before sunset`
  → *"Okay, I'll turn on kitchen light at 16:44, 4 minutes before sunset."*
  Persisted row: `{"kind":"routine","action":"on","target":"kitchen light",
  "due_at": <sunset - 240s>}`. Stale routines are dropped on restart;
  `/health` now reports `scheduled_routines`.

## 2. Calendar write + retrieval

- `calendar_ics.py`: shared `calendar_path()`; reader now unescapes ICS text.
- New `calendar_write.py`: atomic append of `VEVENT`s to `calendar.ics`
  (CRLF, escaping, UID, DTSTART/DTEND), plus `events_for()`.
- New intents: `calendar.add` **before** the read-only `calendar` intent;
  pending clarification supported ("When should I add it to your calendar?").
- Verified live:
  - `add call mum to my calendar today at 23:59` → *"Added call mum to your calendar for 23:59."*
  - `what's on my calendar` → *"You have one event today: call mum."*
  - `add a reminder to my calendar: buy milk tomorrow at 9` → added for Monday 09:00.
  Test events were removed again afterwards.
- `/health` now has `calendar_file` and `calendar_events_today`.

## 3. Find new devices on the LAN

- New `iot_discover.py`: runs `iot_scan.py` as a subprocess (its own argparse and
  `asyncio.run()` make in-process use unsafe), pinned to `192.168.1.0/24`,
  ~17 s, off the voice turn via `background()`.
- `new_since()` compares the previous report + all IPs in `iot_keys.json` /
  `iot_pending.json` / live registry. `stage()` writes keyless placeholders to
  `iot_pending.json`; `IotRegistry` ignores rows without a local key, so this is
  safe. Only LAN-discoverable devices are reported — Tuya IDs/keys still have to
  come from the app/cloud helper.
- New intents `iot.scan` and `iot.scan.status`, plus `POST /scan` (token-gated)
  and `iot_scan` state in `/health`.
- Verified: `scan for lights` → immediate acknowledgement, scan finished in
  ~16 s, spoke *"Scan finished. No new devices found."*;
  `any new devices?` → *"The last scan was 16 seconds ago and found 0 new device(s)."*

## 4. Restaurants, flights, YouTube, Netflix

- **Restaurants** (`places.py`, Nominatim, 1 req/s, 10-min cache):
  - `find restaurants near me` → *"Autogrill, La Merenda Delle Papere and Le Cave near you. I've opened a map with the full list."*
  - `find restaurants in Florence` → real Florence POIs, map opened in the PC browser.
  - No API key needed. Overpass was measured at 26–40 s and deliberately not used
    for spoken replies. Cuisine adjectives are not reliably supported by Nominatim.
- **Flights** (`web_helpers.py`, research only — no booking, no price claims):
  - `find flights to Rome next week` → *"I can't check live fares without a flight API, but I've opened a Google Flights search to Rome from 23 November to 29 November on the computer."*
  - Dates are computed dynamically; Pisa is the default origin. "Book/buy" phrases
    get a truthful "I can't book tickets" reply.
- **YouTube** (`web_helpers.py` + `tv.py::adb_view_url`):
  - `play daft punk on youtube` → resolved a real video ID from YouTube search,
    opened it in the TV's native YouTube app via ADB → *"Playing 'daft punk' on the TV."*
  - Fixed ADB URL quoting (the `&` in watch URLs was being split by the TV shell).
  - If the TV is unreachable, falls back to opening the link on the PC.
  - Chromecast is **not** used for YouTube (proprietary signed receiver protocol).
- **Netflix**: existing `tv.app` path verified — `open netflix on the tv` → *"Opening Netflix."* (app package `com.netflix.ninja` is installed).
  `TV_ADB_HOST=192.168.1.41` added to `.env` to skip slow Cast discovery.
  Test playback was stopped afterwards.

## 5. Extra Alexa-style conveniences (all key-free)

| Phrase | Result |
|---|---|
| `add milk to my shopping list` | `shopping.add` — local `shopping_list.json`, atomic writes |
| `what's on my shopping list` | `shopping.list` — item count and names |
| `remove milk from my shopping list` / `clear my shopping list` | `shopping.remove` / `shopping.clear` |
| `tell me a joke` | local joke list |
| `give me a quote` | local quote list |
| `convert 10 miles to km` | `10 miles is 16.09 km.` (offline factor table) |
| `convert 20 celsius to fahrenheit` | `20 Celsius is 68.0 Fahrenheit.` |
| `how much is 100 dollars in euros` | `100 USD is 90.99 EUR.` (Frankfurter/ECB, 6 h cache) |
| `give me my morning briefing` | time + weather + top news + today's calendar events |

Intent ordering was chosen carefully: shopping before `facts.*`, currency and
unit conversion before `math`, YouTube before `tv.app`/radio, `calendar.add`
before `calendar`, `routine.sunset` before `ha.on`.

## 6. Files added / changed

Added: `calendar_write.py`, `iot_discover.py`, `places.py`, `web_helpers.py`,
`extras.py`, `FEATURE-BUILD-2026-11-22.md`.
Changed: `olive_hub.py` (many handlers/intents/health/scheduler/weather),
`calendar_ics.py`, `tv.py`, `.env` (`TV_ADB_HOST`).

## 7. Verification

- Live hub on **8765**; `/health` 200, **65 intents** registered.
- All command tests above returned 200 with the expected intent and reply.
- Regression suites: `intent_test.py` (exit 0), `iot_test.py` 27/27,
  `features_test.py` 33/33. All Python files compile cleanly.
- Test calendar events removed; shopping list left empty; one sunset routine kept.

## 8. Limitations and next steps

- Flight **booking/payment** is deliberately not implemented; this is research
  plus a real search link only.
- Nominatim cannot reliably filter by cuisine ("Italian restaurants") and has a
  1 req/s policy — cached, no bulk queries.
- Newly discovered devices need a **device ID and local key** pasted into
  `iot_keys.json` before they can be controlled; `iot_pending.json` holds the
  staged metadata.
- YouTube search scraping can break if YouTube changes its HTML; fallback is a
  search-results page. The TV YouTube app may require sign-in.
- `ADB_PATH` still resolves via PATH to an older project folder; pin it in
  `.env` for long-term stability.

After a PC reset the existing `Olive Hub` watchdog reloads all of this
automatically; no new scheduled tasks were added.
