# Olive Hub

A local, voice-first assistant for a LibreEcho speaker: one Python file plus a
few small modules, async, no heavy framework. It owns the speaker's STT and
assistant endpoints, routes intents locally, uses a hosted LLM only for open
conversation, and talks to Music Assistant, local IoT, TV, calendar, WhatsApp
and more.

> **Status:** built and tested on Windows 11 with a LibreEcho OS 0.14.5 speaker,
> Python 3.11, Node 24 and Music Assistant in Docker. See `docs/WORKLOG.md` and
> the dated reports in `docs/` for the full build history.

## What it does

- **Wake word** "Alexa" (firmware) → Wyoming ASR proxy → local Whisper (multilingual `tiny`).
- **One speech path**: replies are spoken through the speaker's `/audio/announce`;
  the chat API returns empty content so nothing is said twice.
- **Local intents first**: time, date, weather, maths, timers, alarms, reminders,
  news, calendar, shopping list, jokes, quotes, unit/currency conversion,
  TV control, local lights, radio/Music Assistant, YouTube on TV, flights,
  restaurants, and WhatsApp messaging.
- **Cloud AI only as fallback** (Groq by default; Ollama/OpenAI configurable).
- **Conversation**: 2-minute follow-up thread with echo guard and mic reopen.
- **Memory**: facts/notes/topics that survive restarts, in a local JSON file.
- **Auto-start/self-healing** through the `Olive Hub` Windows scheduled task.

## Hardware / software

- Windows 11 PC on the same LAN as the speaker (tested on `192.168.1.x`).
- Python 3.11+ with `aiohttp`, `wyoming`, `openai-whisper`, `tinytuya`, etc.
  (see `requirements.txt`).
- LibreEcho speaker with OS 0.14.5 or similar, API reachable over LAN.
- Optional: Docker for Music Assistant; Node 18+ and Chrome for WhatsApp;
  a Groq/OpenAI key for open conversation.

## Quick start

```powershell
cd olive-hub-repo
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# edit .env: HUB_TOKEN, SPEAKER_URL, SPEAKER_PASSWORD, GROQ_API_KEY, ...
.\.venv\Scripts\python.exe olive_hub.py --dry-run   # no device/network side effects
.\start-hub-detached.ps1                            # real run, detached
```

Configure the speaker once:

- STT: `tcp://<PC-IP>:10300`
- Assistant endpoint: `http://<PC-IP>:8765/v1`, model `olive`,
  key = `HUB_TOKEN` (sent as `Authorization: Bearer …`)

See `docs/README-olive-hub.md` for the full setup, including the firewall note
about port **8765** (8766 has no inbound allow rule on some Windows installs).

## Ports

| Port | Service |
|---|---|
| 8765 | Hub HTTP (`/health`, `/command`, `/v1/chat/completions`, `/memory`, `/restart`, `/scan`) |
| 10300 | Wyoming ASR proxy → Whisper |
| 10301 | Local Whisper Wyoming server |
| 10200 | TTS language router (speaker's TTS target) |
| 10201 | Piper English voice |
| 10202 | Piper Italian voice |
| 8095 | Music Assistant (Docker, optional) |
| 3012 | WhatsApp bridge (local, optional) |

## Example voice commands

```
what time is it? / che ore sono?
what's the weather in Florence?
play BBC 1Xtra / play some jazz / next station
play daft punk on youtube / open netflix on the tv
find restaurants near me / find flights to Rome next week
what's on my calendar / add call mum to my calendar tomorrow at 9
turn on the kitchen light 4 minutes before sunset
set a timer for 5 minutes / remind me to call mum in 20 minutes
add milk to my shopping list / tell me a joke / give me my morning briefing
send a whatsapp msg to Julia, saying "hi baby have a good day!"
whatsapp status / whatsapp contacts / scan for lights
```

`/health` lists all registered intents (68+).

## Configuration

All configuration is via `.env` (never commit it). Key settings:

| Variable | Purpose |
|---|---|
| `HUB_HOST`, `HUB_PORT`, `HUB_TOKEN` | HTTP listener and required token |
| `SPEAKER_URL`, `SPEAKER_USER`, `SPEAKER_PASSWORD` | LibreEcho API |
| `ASR_HOST`, `ASR_PORT`, `WHISPER_HOST`, `WHISPER_PORT` | Wyoming ASR |
| `GROQ_API_KEY`, `GROQ_MODEL` (or `OPENAI_API_KEY`, Ollama) | LLM fallback |
| `AI_PROVIDER`, `LLM_MODEL`, `LLM_MODEL_FALLBACK` | Provider selection |
| `DUCK_MUSIC`, `DUCK_MODE` | Duck music while speaking/listening |
| `FOLLOW_UP`, `FOLLOW_UP_DELAY`, `CONVERSATION_TTL` | Conversation behaviour |
| `MA_ENABLED`, `MA_URL`, `MA_TOKEN`, `MA_PLAYER_ID` | Music Assistant |
| `IOT_ENABLED`, `IOT_KEYS_FILE` | Local Tuya devices with LAN keys |
| `TUYA_API_KEY`, `TUYA_API_SECRET`, `TUYA_REGION` | Optional Tuya cloud |
| `CALENDAR_ICS_URL`, `CALENDAR_ICS_FILE` | Calendar read/write |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional Telegram |
| `SERPAPI_API_KEY`, `SERPAPI_DAILY_LIMIT`, `FLIGHT_ORIGIN` | Flight prices (cached, capped) |
| `WHATSAPP_BRIDGE_URL`, `WHATSAPP_BRIDGE_PORT`, `WHATSAPP_BRIDGE_TOKEN`, `WHATSAPP_BRIDGE_SESSION` | Local WhatsApp bridge |

See `.env.example` for the full template with comments.

## Auto-start

`register-olive-task.ps1` creates the `Olive Hub` S4U scheduled task: Boot +30s
and a 5-minute watchdog. It starts the hub, Whisper, the TTS stack, and the
WhatsApp bridge if they are not listening. `POST /restart` reloads the hub
immediately without waiting for the scheduler.

## Docs

- `docs/README-olive-hub.md` — full manual (ports, intents, troubleshooting).
- `docs/FINAL-AUDIT-2026-11-22.md` — reliability audit and fixes.
- `docs/FEATURE-BUILD-2026-11-22.md` — routines, calendar, search, discovery, extras.
- `docs/INTEGRATION-PLAN-2026-11-22.md` — flight APIs and WhatsApp bridge.
- `docs/REMOTE-SPEAKER.md` — taking the speaker to another network (Tailscale).
- `docs/WORKLOG.md` — chronological build record.
- `docs/architecture.md` — high-level architecture notes.

## Privacy and security

- Everything except `/health` requires `X-Olive-Token` or `Authorization: Bearer`.
- Nothing in this repo contains real API keys; `.env`, WhatsApp sessions, QR
  images, memory and device keys are gitignored.
- The WhatsApp bridge uses an **unofficial** client (whatsapp-web.js) bound to
  localhost. WhatsApp does not allow bots; use a dedicated number and expect
  the possibility of a block.
- Transcripts are never `eval`'d; arithmetic uses an AST whitelist.
- Music requests never fall through to the LLM.

## Limitations

- Flight prices come from SerpApi/Google Flights and are **estimates**; no booking.
- Weather, time, currency and news need internet.
- The speaker's wake word cannot be changed on this firmware (Alexa only).
- Tuya cloud is currently unusable (suspended project); local IoT keys are the
  working path for lights.
- WhatsApp needs a one-time QR link and the PC/phone session online.

## License

Personal project; no license file is included. Do not publish credentials.
