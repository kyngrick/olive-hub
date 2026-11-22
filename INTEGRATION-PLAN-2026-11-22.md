# Flight search + WhatsApp integration plan (22 Nov 2026)

## 1. Free search APIs for flights — research result

I checked the current documentation:

| Service | What it actually is | Free access | Verdict for flights |
|---|---|---|---|
| **Brave Search API** | General web/news/image search + LLM context; own index | "$5 free credits every month", then ~$5/1,000 requests (Search plan) | Not a flight API. Returns pages/snippets, not live fares. |
| **Tavily** | General web search/extract/research for AI agents | Free API key: 1,000 credits/month; also keyless access | Not a flight API. Good for "find pages about cheap flights", not quotes. |
| **Google Programmable Search JSON API** | General Google web search | **Closed to new customers**; existing customers until 1 Jan 2027 | Not available to us. |
| **SerpApi `google_flights`** | Structured Google Flights results | Paid, but cheap | The realistic way to get real prices without airline partnerships. |
| **Duffel** | Real airline offers + orders | Test key free | Real offers and eventual booking; larger build. |
| **Skyscanner / Kiwi Tequila** | Partner APIs | Approval required | Not practical without partner status. |

**Conclusion:** Brave/Tavily are useful general web-search tools but cannot
supply trustworthy live flight prices. **SerpApi was chosen instead** and is
now wired into the hub.

### SerpApi implementation (done)

- `SERPAPI_API_KEY` stored in `.env` (never printed). The account currently
  reports **240 searches left on the Free Plan**.
- New `flights_provider.py`: `engine=google_flights`, one-way searches,
  EUR/Italy locale, 60-minute on-disk cache in `flight_search_cache.json`,
  and a conservative **5 searches/day** cap (`SERPAPI_DAILY_LIMIT`).
- Default origin `FLR` (Florence) — `PSA` returns no Google Flights results.
  `FLIGHT_ORIGIN` in `.env` can change it.
- `find flights to Rome next week` now speaks real indicative offers, e.g.
  *"From Florence to Rome for 23 November: ITA 70 euros, direct, leaving 15:30…"*
  with a "prices are estimates" caveat, and still opens the search page.
  It never claims to book. If the key is missing or the daily cap is hit, it
  falls back to the honest Google Flights link reply.
- `/health` exposes `flights: {provider, origin, daily_limit, searches_today,
  cached_results}`.
- Verified: one live search (counter 1, cache 1) and a second identical request
  served from cache (counter stayed 1). "Book/buy" phrases still refuse.

## 2. WhatsApp — built (whatsapp-web.js bridge)

Implemented and started:

- `whatsapp-bridge/` — Node 24 service using `whatsapp-web.js`, Express,
  `LocalAuth` session persistence, and system Chrome (installed).
- Local HTTP API on **127.0.0.1:3012**, token-gated (`X-Bridge-Token`).
  Port 3011 is retired: that older process is owned by another Windows session
  and cannot be stopped here. The replacement reuses a copy of the linked
  Chrome profile (`whatsapp-bridge/session2`) so **no re-scan was needed**.
  `.env` variables: `WHATSAPP_BRIDGE_URL`, `WHATSAPP_BRIDGE_PORT`,
  `WHATSAPP_BRIDGE_SESSION`, `WHATSAPP_BRIDGE_TOKEN`.
  - `GET /health` — status (`starting`, `qr`, `authenticated`, `ready`, …)
  - `GET /qr` — QR image
  - `GET /contacts?q=` — contact lookup by name/number
  - `POST /send` — `{ "to": "mum", "text": "..." }`
- `whatsapp.py` — Python client for the bridge, lazy `.env` reading.
- Hub intents (**68 total**): `whatsapp.status`, `whatsapp.send`,
  `whatsapp.contacts`. `/health` now includes `whatsapp_bridge`.
- `start-whatsapp-bridge.ps1` — detached, idempotent starter; wired into
  `ensure-hub.ps1` and `start-hub-detached.ps1`, so it comes back after a reboot.

### One thing I need from you

**Scan the QR code** shown in the Review pane with:
**WhatsApp → Settings → Linked devices → Link a device → +**.

Use `whatsapp-bridge\qr-live.html` (auto-refreshes every 5 seconds) rather than
the static `qr.png`: WhatsApp QR codes expire in ~20 seconds, so scanning an
old image produces "Could not link device". If it still fails, check that the
phone has internet, the correct WhatsApp account, automatic date/time, and
fewer than the maximum linked devices (remove an old one if needed). As a last
resort the bridge can be switched to a visible (headful) Chrome window.

Status currently: `qr` — waiting for that scan. After scanning, the bridge
becomes `ready` and I can run the live send test.

### Test plan — all passed

- Bridge starts, token auth works, QR linked successfully.
- `whatsapp status` → "WhatsApp is connected and ready."
- `whatsapp contacts` → resolved 25 contacts by name/number.
- **Send-to-self test**: `send a whatsapp to +393482763421 saying Olive test -
  ignore this` → hub replied "WhatsApp message sent", bridge counter 0 → 1.
  E.164 numbers are sent directly to `<digits>@c.us` (no contact lookup needed).
- Restarted the bridge on the replacement port 3012 with the copied session —
  no re-scan needed. Old 3011 process remains but is unused.

### Risks / rules

- Unofficial client: WhatsApp does not allow bots; accounts can be blocked.
  Use a dedicated number if possible; keep the bridge localhost-only.
- The PC and the linked phone/session must be online.
- `run.cmd` contains the bridge token — do not share it.
- No group broadcasts or unsolicited mass messages.

## 3. Files changed this round

Added: `whatsapp-bridge/{package.json,server.js,qr.png,run.cmd}`,
`whatsapp.py`, `start-whatsapp-bridge.ps1`, this plan.
Changed: `olive_hub.py`, `.env` (bridge URL + generated token), `.env.example`,
`ensure-hub.ps1`, `start-hub-detached.ps1`, `README-olive-hub.md`, `WORKLOG.md`.

After a PC reset the scheduled watchdog starts the bridge automatically; only
the one-time QR link is manual (the session is persisted in
`whatsapp-bridge/session`).
