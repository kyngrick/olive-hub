# Location and train lookup — research + implementation

Added 23 Nov 2026.

## "Alexa, what's my location?"

New **location** intent (`h_location`). It reports the configured home coordinates
(`WEATHER_LAT` / `WEATHER_LON`, `OLIVE_CITY`) and reverse-geocodes them through
Transitous to name the nearest address/stop.

Verified spoken replies:
```
whats my location -> You are near Via della Principessa 99a, around 43.09 degrees
                     north, 10.54 degrees east. Your home city is set to San Vincenzo.
where am i        -> (same)
dove siamo        -> (same, Italian phrasing also accepted)
```
There is no phone/GPS on the speaker, so "location" means the configured home
location, not a live device position. Change it in `.env` with `WEATHER_LAT`,
`WEATHER_LON`, `OLIVE_CITY` (all editable from the dashboard's Configuration card).

## "Check trains and times/cost from X to Y on DATE"

New **trains** intent (`h_trains`) plus `train_provider.py`.

### Research: which API?
- **Transitous** (`https://api.transitous.org/api/`) — free, no key, community-run
  open public-transport routing (MOTIS 2 API), broad European coverage incl.
  Trenitalia. Requires a descriptive `User-Agent` (we send one) and attribution
  to `https://transitous.org/sources/` (added to the dashboard and this doc).
- Rejected: DB `v6.db.transport.rest` (unreachable from this host), National Rail
  (UK-only + token), Trainline/Omio scraping (fragile, ToS), paid APIs.
- Endpoints used: `GET /api/v1/geocode`, `/api/v1/reverse-geocode`,
  `/api/v6/plan` (with `withFares=true`).

### What works
- Geocoding resolves city/station names to coordinates. The API's own ranking is
  trusted, with a preference for an exact Italian match — this avoids earlier
  wrong hits like "Rome" → a stop near Paris and "Milan" → a Chinese railway.
- `plan()` accepts a real departure `time` (ISO with timezone), so "tomorrow at
  8", "on 25 November", "next Friday" and "today" are honoured.
- Spoken reply gives the first two options: departure, arrival, duration,
  number of changes and the main train (e.g. *Frecciarossa*).
- If no ticket price is published in the open feed (currently the case for
  Trenitalia), Olive says so honestly and opens a real Google price-check search
  on the PC rather than inventing a number.

Verified end-to-end through `/command`:
```
check trains and times cost if available from Rome to Milan tomorrow at 8
-> Tomorrow from Rome to Milan: at 07:01, arriving 10:41, 3h 40min, 2 changes,
   Frecciarossa; at 07:03, arriving 10:41, 3h 38min, 3 changes, Frecciarossa.
   The free timetable doesn't include ticket prices. I've opened a price check
   on the computer.
```
Also verified: `train times from Pisa to Florence on 25 November` and
`trains from London to Paris next Friday` parse correctly.

### Cost caveat
There is **no free, reliable, cross-border ticket-price API**. Transitous returns
empty fare products for almost all feeds. Prices therefore come from the opened
search link, not from Olive. Booking is deliberately not attempted.

## Dashboard card
New **Travel / location** card:
- shows the configured home location and nearest address;
- a text box for any train phrase, returning a five-row timetable (depart,
  arrive, duration, changes, train) plus a **Check prices** link;
- attribution link to Transitous sources.

APIs: `GET /api/location`, `GET /api/trains?text=...` (token-protected like the
rest of the dashboard).

## Note on usage limits
Transitous asks that light, non-commercial apps use the API and identify
themselves; we do both. Please don't turn this into a high-frequency poller.

## Verified state
74 intents (up from 72), 9/9 services up, hub/dashboard restarted and healthy.
