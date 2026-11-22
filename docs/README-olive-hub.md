?# Olive Hub

Voice → intent → action for the LibreEcho speaker: radio, timers, weather and
Tuya / Home Assistant control. One file, async, no framework.

This replaces `New_pipe2.py`. It keeps the two good ideas from that script — a
Wyoming ASR proxy and an OpenAI-compatible endpoint — and fixes the parts that
did not work.

## What changed vs `New_pipe2.py`

| Old behaviour | Now |
|---|---|
| "Playing Radio Italia" announced, nothing played | Calls `/integrations/radio/play` for real |
| Same answer spoken twice (device + pipeline TTS) | One speech path: pipeline replies are text only, proactive lines use the device |
| Three blocking `urllib` calls per sentence | One async client, CSRF + token cached for 30 min |
| Credentials hardcoded in the file | Read from `.env` |
| Endpoints on `0.0.0.0`, no auth | `127.0.0.1` by default, `HUB_TOKEN` required |
| Fahrenheit hardcoded for Italy | Metric default, Fahrenheit via `OLIVE_UNITS=imperial`, real coordinates |
| `if "play" in text` | Regex intent registry with named slots, EN + IT |
| `eval()` on a transcript | AST-based arithmetic only |
| `while True` on the Whisper socket | Bounded by `ASR_TIMEOUT` |
| Timers in RAM | Device-native timers (`/timers`), they ring on the speaker |
| A fixed list of stations by exact name | Curated 30-station catalog with moods, genres, playlists, online search |
| No home automation | Direct Tuya control (no Home Assistant needed), HA still supported |

## Requirements

- Python 3.11+ with `aiohttp` (needed), `wyoming` (ASR proxy), `tinytuya` (Tuya).
  All three are already in `.venv`.

## Run

```powershell
cd "C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2"
.\.venv\Scripts\python.exe olive_hub.py                 # real run, reads .env
.\.venv\Scripts\python.exe olive_hub.py --dry-run       # no device/LLM/Tuya calls
.\.venv\Scripts\python.exe olive_hub.py --port 8791     # override the listener
```

Config comes from `.env` (template in `.env.example`). Real environment
variables win over `.env`.

Logs go to stdout/stderr. When started detached, `olive_hub.log` and
`olive_hub.err` hold them.

```powershell
.\start-hub.ps1            # restart from an interactive shell
.\start-hub-detached.ps1   # restart from automation (survives the caller)
```

`start-hub-detached.ps1` frees ports 8765/10300, rotates the old logs and
starts the hub through WMI so the calling shell can exit immediately.

### Auto-start and self-healing

The scheduled task **Olive Hub** runs `ensure-hub.ps1` at logon and every five
minutes. The watchdog only starts the hub when nothing is listening on 8765 and
the Whisper server when 10301 is free, so it never kills or races a healthy
instance.

```powershell
Get-ScheduledTask -TaskName "Olive Hub" | Format-List
Get-ScheduledTaskInfo -TaskName "Olive Hub"
Get-Content .\hub-watchdog.log -Tail 20
Unregister-ScheduledTask -TaskName "Olive Hub" -Confirm:$false   # remove
```

It runs as the current user with **RunLevel Limited** — no admin, no SYSTEM, no
stored password. This form only runs while the user is logged on. For start
before login and survival across logoff, run the registration script once from
an elevated PowerShell:

```powershell
.\register-olive-task.ps1      # creates the S4U variant (still Limited)
```

The S4U task keeps running with no interactive session; it needs one UAC
approval at registration time and still stores no password.

Both variants recover within five minutes if the hub stops, and neither touches
any task other than `Olive Hub`.

**Reloading code without the scheduler.** PowerShell's startup under S4U can
stall for minutes when the machine is busy, so day-to-day reloads use the hub
itself:

```powershell
curl.exe -s -X POST localhost:8765/restart -H "X-Olive-Token: <token>"
```

`POST /restart` writes `restart-helper.cmd` and starts it detached. The helper
calls `start-hub-detached.ps1`, which stops whatever owns 8765/10300, waits for
the ports, and starts a fresh `olive_hub.py` with the current venv (and checks
Whisper on 10301). It replies immediately, so the caller does not wait for the
restart.

The watchdog still understands `restart.request`: create that file and run
`Start-ScheduledTask -TaskName 'Olive Hub'` (or wait for the five-minute tick).
If `Get-ScheduledTask` shows `Running` but nothing was recycled, run
`Stop-ScheduledTask -TaskName 'Olive Hub'` and trigger again — a stalled
PowerShell instance blocks the next one.

## Ports

| Port | Owner |
|---|---|
| 8765 | hub HTTP (`/command`, `/v1/chat/completions`, `/health`) |
| 10300 | hub Wyoming ASR proxy → 10301 |
| 10301 | local Whisper Wyoming server (`whisper_wyoming_server.py`) |
| 10200 | TTS language router the speaker talks to |
| 10201 | Piper English (`en_GB-alan-medium`) |
| 10202 | Piper Italian (`it_IT-serena-medium`) |

The hub owns both ports the speaker uses; `New_pipe2.py` is retired. The speaker
is configured with `tcp://192.168.1.86:10300` for STT and
`http://192.168.1.86:8765/v1` (model `olive`) for the assistant.

**Why 8765 and not 8766:** Windows Firewall on this PC has inbound allow rules
for 10300/10301 and 8765, but none for 8766, and adding one needs elevation.
The speaker could reach the ASR proxy but its chat calls to 8766 were silently
dropped (`agentd: voice response failed: ChatGPT response failed`). The hub
HTTP port was therefore moved to the already-allowed 8765. Keep
`HUB_PORT=8765` in `.env` and the device's `base_url` on `...:8765/v1`; if you
later add a firewall rule for 8766, both can be changed back together.

### Whisper ASR server

`whisper_wyoming_server.py` runs the local speech-to-text service on 10301.
It uses `openai-whisper` with a model cached in `~/.cache/whisper`. The starter
passes `--model tiny` (multilingual, ~1 s for a 5 s command); change that flag
in `start-whisper-detached.ps1` to use another model. It loads the model once
at startup and answers Wyoming `Transcribe` / `AudioStart` / `AudioChunk` /
`AudioStop` with one `Transcript`. No extra Python package is needed.

```powershell
.\start-whisper-detached.ps1   # start if 10301 is free; never kills a healthy server
```

The Olive Hub watchdog starts it automatically if it dies, and `POST /restart`
makes sure it is up after a hub restart. Logs: `whisper.log`, `whisper.err`.
If Whisper is down, the hub now logs one warning per 30 seconds and returns an
empty transcript instead of throwing an unhandled `ConnectionRefusedError`.

### WhatsApp messages

A local Node bridge (`whatsapp-bridge/`, whatsapp-web.js) exposes a token-gated
HTTP API on 127.0.0.1:3012. The hub talks to it through `whatsapp.py`.
Start it with `.\start-whatsapp-bridge.ps1`; the watchdog also keeps it alive.
**One-time setup:** scan `whatsapp-bridge\qr.png` with WhatsApp → Settings →
Linked devices → Link a device. Then:

- `whatsapp status` — connection state
- `send a WhatsApp to mum saying I'll be late` — resolves the contact by name
- `whatsapp contacts` — list matches

This is an unofficial WhatsApp client: it can be blocked by WhatsApp, needs the
PC and phone session online, and must not be exposed outside localhost.
See `INTEGRATION-PLAN-2026-11-22.md` for official alternatives.

### TTS voices and Italian

The speaker's Wyoming TTS target is a small language router, not Piper directly:

| Port | Service |
|---|---|
| 10200 | `wyoming_tts_lang_router.py` — the speaker's TTS endpoint |
| 10201 | Piper English, `en_GB-alan-medium` |
| 10202 | Piper Italian, `it_IT-serena-medium` |

The router reads each `Synthesize` text and picks the voice, so English replies
use the English voice and Italian replies use native Italian phonemes. Change
`$enVoice` / `$itVoice` at the top of `start-tts-detached.ps1` to use any other
installed Piper voice. The watchdog starts any missing TTS service. For
voice-cloning options and a full feature audit, see
`FINAL-AUDIT-2026-11-22.md`.

## HTTP API

`X-Olive-Token: <HUB_TOKEN>` is required on everything except `/health`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status, enabled features (`speaker`, `home_assistant`, `tuya`, `llm`), intent list |
| GET | `/v1/models` | OpenAI-compatible model list |
| POST | `/v1/chat/completions` | what the voice pipeline calls |
| POST | `/command` | `{"text": "..."}` — routes and **speaks** the answer |
| POST | `/restart` | reload the hub with the current code (detached helper) |
| POST | `/scan` | start a background LAN device scan (see `FEATURE-BUILD-2026-11-22.md`) |
| GET | `/memory` | permanent memory: facts, notes, recent topics |
| POST | `/memory` | `{"key": "...", "value": "..."}` or `{"note": "..."}` |
| DELETE | `/memory` | `?key=...` forgets one entry; no key clears everything |

```powershell
curl.exe -s localhost:8765/health
curl.exe -s -X POST localhost:8765/command -H "Content-Type: application/json" `
  -H "X-Olive-Token: <token>" -d '{\"text\":\"play 80s hiphop\"}'
```

Two reply modes, by design:

- **pipeline** (chat completions): return text, let the pipeline speak it — no double speech.
- **device** (timers, `/command`): announce through the speaker, ducking the music.

This speaker's agent drops assistant text from local endpoints, so the live
config sets `SUPPRESS_TTS_ON_DEVICE=1`: the hub speaks every chat reply through
`/audio/announce` and returns an empty `content`. The agent still gets a valid
SSE stream (`STREAM_CHAT=1`).

`POST /reset` clears the conversation thread, the pending clarification and the
follow-up state; `/wake-word/test` on the device is what the hub calls to reopen
the microphone.

## Intents

Time, date, weather (any city), maths, coin, dice, radio (by name, genre or
mood), playlists, next station, pause / resume / stop, now playing, volume (set,
up, down), timers (set, list, cancel), alarms, reminders, news, calendar,
Telegram messages, personal facts, TV (Chromecast media + Android TV power/apps),
and Tuya / Home Assistant on / off / state / scene. English and Italian phrasing
is matched, e.g. `accendi la luce della cucina`, `che ore sono`, `metti la radio`,
`timer for five minutes`.

Anything unmatched is forwarded to the LLM (`LLM_URL`, or Groq/Ollama from the
existing keys), except music requests, which never fall through to the LLM.

### Follow-up questions

A bare request such as `set a timer` or `remind me to buy milk` asks one
question back (`How long should the timer be?`, `When should I remind you?`) and
waits up to `PENDING_TTL` seconds for the answer. The answer can be `4 mins`,
`1 minute` or `at 6`; anything else is routed normally instead of hijacking the
next command.

`play music` without a genre asks **"Which kind of music would you like?"** and
waits for the reply (`hiphop`, `jazz`, `BBC 1Xtra`), so the next turn plays it.

When a cloud answer ends in a real question (a clarification or a research
follow-up), the hub remembers that question for `PENDING_TTL`. A short reply is
merged with the original request and sent back to the LLM; `could you clarify`,
`what do you mean` or `non ho capito` repeats the question. A new command or a
full new question always wins over an old pending question, and courtesy lines
such as "Anything else I can help with?" never open a pending state.

### Conversation and follow-up listening

After a spoken reply the hub reopens the speaker's microphone (device
`POST /wake-word/test`) so you can answer without repeating the wake word.

- The window opens only after the announcement has actually finished playing,
  plus `FOLLOW_UP_DELAY`. Opening it earlier recorded the tail of the speaker's
  own voice and the hub answered itself.
- It is skipped for media intents, while media plays, and after two wake-only
  turns in a row (`FOLLOW_UP_DEPTH`).
- A wake-only turn outside a hub-opened window answers **"I'm listening."** and
  keeps listening; inside one it means the room stayed quiet, so the hub stays
  silent and closes the chain instead of repeating itself.
- `stop`, `that's all`, `thank you`, `never mind`, `goodbye` end the thread.

An echo guard drops transcripts that arrive while the speaker is announcing and
transcripts that resemble anything spoken in the last 20 seconds (normalised
similarity plus token overlap), so the hub never answers its own voice.

History lives for `CONVERSATION_TTL` (default 120 s) and is capped at ten turns.
Whisper hallucinations such as *"That's the end of the sentence."* are filtered
before they reach the router.

### Permanent memory

The 2-minute thread is **temporary**: it exists so follow-up questions work.
Everything that should survive a restart goes to `olive_memory.json`
(`OLIVE_MEMORY_FILE`, `MEMORY=0` disables it):

| Kind | Written by | Example |
|---|---|---|
| facts | `my birthday is ...`, `remember my sister is Anna` | injected into the LLM prompt |
| notes | `remember that I like rainy day music`, `POST /memory` | preferences and context |
| topics | the user lines of a thread when it expires | "what's the weather in Florence" |

Facts and notes are added to the system prompt for every cloud answer, capped at
about 1200 characters, and are explicitly marked as background so the model does
not read them aloud. `personal_facts.json` is imported once on first start, so
existing memories are kept. Inspect or edit them at runtime:

```powershell
curl.exe -s localhost:8765/memory -H "X-Olive-Token: <token>"
curl.exe -s -X POST localhost:8765/memory -H "X-Olive-Token: <token>" `
  -H "Content-Type: application/json" -d '{\"key\":\"favourite radio\",\"value\":\"BBC 1Xtra\"}'
```

### Alarms, reminders, news, calendar, messages, facts

| You say | What happens |
|---|---|
| `set an alarm for 7:30 am` / `wake me up at 7` | device timer rings, hub keeps the record |
| `what alarms do i have` / `cancel my alarms` | lists or clears them |
| `remind me to call mum in 20 minutes` | hub says it out loud at the right time |
| `what are my reminders` / `cancel my reminders` | lists or clears them |
| `what's the news` / `news about italy` | Google News RSS headlines, 10-min cache |
| `what's on my calendar` | today's events from `CALENDAR_ICS_URL`/file |
| `send a telegram saying ...` | Telegram bot message (needs token + chat id) |
| `remember my sister is Anna` | stored in `olive_memory.json` and used by the LLM |
| `what is my birthday` | reads it back |
| `what do you remember about me` | lists stored facts and notes |

Reminders and alarms survive a restart (`olive_schedule.json`); only future
items are reloaded, anything missed while the hub was down is dropped.

### Stop, volume and a ringing alarm

Saying **"stop"** (also "Alexa stop", "stop it", "stop the alarm", "shut up",
"be quiet", "silence", "basta") is handled before anything else, including a
pending clarification. The hub silences, in order: a ringing device timer
(`POST /timers/dismiss`), an announcement bus, Music Assistant, then native
radio. Only when nothing is active does "stop" simply end the conversation.

`cancel my alarms` also removes the matching native device timers and dismisses
one that is already ringing — cancelling the hub's record alone would leave the
speaker beeping.

Volume accepts `louder` / `quieter`, `più forte` / `più piano`, `turn it up` /
`turn it down`, `volume up 5`, and absolute levels (`set volume to 30`). The hub
reads the current level first and clamps to 0..100.

## Music

The hub owns a curated catalog of 30 stations in `CURATED_STATIONS`
(`olive_hub.py`): pop, hip hop, 80s, electronic, chill, jazz, classical, Italian
and news. Each entry carries tags and a `pref` weight.

How a request is resolved:

1. **Exact name/word** wins — `play bbc 1xtra`, `play groove salad`.
2. **Tags** decide genres and moods — `play pop`, `play hiphop`,
   `play rainy day music`, `play 80s hiphop`, `play classical`, `play news`.
3. **Online directory** (radio-browser.info) for anything else, if
   `RADIO_SEARCH=1`. Winners must actually match the request by name or tag, be
   MP3/AAC above 96 kbps, and get a legal device word. They are added to the
   device automatically.

Verified routing examples:

| You say | Plays |
|---|---|
| `play bbc 1xtra` | BBC Radio 1Xtra (HLS) |
| `play 80s hiphop` | Skyrock Klassics |
| `play hip hop` | Capital XTRA |
| `play rainy day music` | Drone Zone |
| `play pop` | PopTron |
| `play jazz` | FIP Jazz |
| `play classical` | Classic FM |
| `play italian radio` | Rai Radio 1 |
| `play reggae` (online) | La Grosse Radio Reggae |
| `play drum and bass` (online) | Algorythme Drum & Bass |
| `make a playlist for rainy day` | Drone Zone → Lush → Deep Space One → Ambient Sleeping Pill |
| `next station` | advances the playlist, or the next stored station |

`make a playlist ...` queues 4 matching stations and starts the first; `next
station` cycles the queue. Without a playlist, `next station` steps through the
stored list.

The device stores at most 32 stations and keeps them across reboots
(`PUT /integrations/radio` replaces the whole list). The hub seeds the catalog on
startup, keeps any extra stations you added, and when full evicts the stations it
added dynamically — never the curated ones. `word` must be lowercase letters,
digits, spaces or hyphens, and unique.

Live radio cannot pause on this firmware (`transport.pause = false`), so:

- `pause` stops the stream and remembers the station; `resume` replays it
  ("rejoins the broadcast where it is now").
- Announcements duck the volume to ~35%, then restore it — but only if you have
  not changed the volume in the meantime.

Mood requests are cached in `music_catalog.json`: the first `play rainy day
music` resolves a station through the online directory, later requests reuse it
instantly (`rainy day` → *Rainy Day (myNoise Radio)*).

### Music Assistant

Set `MA_ENABLED=1` with `MA_URL`, `MA_TOKEN` and `MA_PLAYER_ID`. Device-native
radio stays the fallback; named stations prefer MA when `RADIO_PREFER_MA=1`
(the default):

- `play bbc one extra` → MA plays BBC Radio 1Xtra
- `play groove salad on music assistant` → explicit MA request
- `play fip everywhere` / `play jazz multiroom` → MA everywhere hint

The hub says which path it used: *"Playing BBC Radio 1Xtra through Music
Assistant."* or *"Music Assistant stayed silent, so it's on the speaker now."*

Station names are matched phonetically (`station_key`), so "BBC one extra",
"BBC 1Xtra" and "bbc1xtra" all resolve to the same station. A bare station name
after another question ("and Capital FM") is replayed as a radio request too.

MA gets a URL its ffmpeg pipeline can decode: `RadioLibrary.ma_url()` prefers the
station's direct MP3/AAC stream over an HLS playlist, and looks the station up
through radio-browser when the device entry only has `.m3u8`. Before playing, the
hub clears the player queue (`player_queues/clear`) — a stale item from an older
stream otherwise makes `play_media` a silent no-op.

Before answering, the hub waits up to 10 s for the speaker's `/sendspin` to
report `streaming: true`. If MA accepted the request but no audio flows, the
hub says so and starts the same station natively, so the room is never silent.

Known firmware issue (LibreEcho OS 0.14.5): after a Music Assistant session has
been stopped and the speaker sits idle for a few minutes, its Sendspin client
sometimes stops accepting audio (`/sendspin` says `connected`, `streaming:
false`, `/playback` says `idle`, while MA says `playing`). Reloading MA's
`sendspin` provider does **not** fix it. A speaker reboot does
(`POST /system/reboot`, `X-LibreEcho-Confirm: confirm-device-action`). The hub
detects this state and falls back to native playback automatically.

The local server (`http://127.0.0.1:8095`, MA 2.10.12 in Docker) pairs with the
speaker over **Sendspin**; `up31eb41d3` ("olive") is the universal player to
use. MA answers most commands with a bare JSON `null`, so the hub treats HTTP
200 as success, and keeps every MA call short (4-10 s) so a slow MA never holds
up the spoken reply.

If you re-pair: the device listens on `8928` (`/sendspin`), MA on `8927`. In MA,
save the `sendspin` provider with
`manual_discovery_ip_addresses=["192.168.1.228"]` and reload it, then use the
device's `POST /sendspin/pairing` token. When audio wedges, restart the speaker
first, then the MA container if its API stops answering.

## TV (Chromecast + Android TV)

`tv.py` controls the TV from the hub:

- **Chromecast / Google TV / Android TV with Cast** — discovery via
  `pychromecast`, then play/pause/stop, volume, mute and status.
- **Android TV with ADB debugging** — `power on/off`, app launch and state.
  The bundled platform-tools work out of the box.

```powershell
"turn on the tv"        # ADB power on (or wake the cast device)
"open netflix"          # ADB app launch
"pause the tv"          # cast media
"tv volume 20"          # cast volume
"what's on the tv"      # cast status
```

Configuration: `TV_ENABLED=1`, `TV_CAST_NAME` (fuzzy, e.g. `living room`),
`TV_DISCOVERY_TIMEOUT`, `TV_CACHE_SECONDS`, `TV_ADB_HOST` (empty = the cast
device's IP), `TV_ADB_PORT=5555`, `ADB_PATH` (empty = `adb` on PATH, then the
local `platform-tools\adb.exe`).

The TV must be powered/awake and on the same LAN segment as the hub. Discovery
is cached for `TV_CACHE_SECONDS`; if nothing is found, `/health` still reports
`tv: true` but commands answer that the TV was not found. Power-off through
Chromecast is not possible — that path needs ADB (`turn off the tv`).

Android TV reports almost nothing through Cast (empty session, volume 0), so
`status()`, volume and stop/pause fall back to ADB: the foreground package
becomes "showing Netflix / YouTube / the home screen", `media volume --stream 3`
is the real music volume, and key events (STOP=86, HOME=3, PAUSE=127, PLAY=126)
stand in for a missing Cast session. `adb_state()` reports
`power`/`volume`/`focus`. Enable *Developer options → USB debugging* (and
network debugging where offered) on the TV once; the first `adb connect` asks
for confirmation on screen.

Verified on 19 Nov 2026 with the Bedroom TV (`192.168.1.41`, `AI PONT`):
`what's on the tv` → *Bedroom TV is showing Netflix. Volume 26.* and
`open netflix on the tv` → *Opening Netflix.*

## LED feedback

The hub writes named device LED profiles at startup (`apply_led_theme`):

| State | Colour | Where |
|---|---|---|
| conversation / listening | yellow `(255, 190, 0)` | left and right |
| cloud AI thinking (Groq) | slow flashing orange `(255, 96, 0)` | back |

`LED_THEME=0` leaves the LED alone. Brightness comes from `LED_BRIGHTNESS`
(default 100). The firmware exposes one profile per state (`listening`,
`thinking`), not per-pixel control from the API, so the left/right and back
placement follows the profile the device already uses.

## Tuya (no Home Assistant)

The hub talks to the Tuya Cloud API directly through `tinytuya`; the Smart Life
app keeps working, you just add cloud access once:

1. Create a free project at <https://iot.tuya.com> → *Cloud* → *Development*.
2. In the project: *Devices* → *Link Tuya App Account* → scan the QR code with
   the Smart Life / Tuya app.
3. Note the **Access ID**, **Access Secret** and the **data centre** (`eu`,
   `us`, `cn`, `in`).
4. Run the helper, which lists your devices and writes the keys:

```powershell
.\.venv\Scripts\python.exe tuya_setup.py --write
# restart olive_hub.py afterwards
```

That adds to `.env`:

```
TUYA_REGION=eu
TUYA_API_KEY=<access id>
TUYA_API_SECRET=<access secret>
# optional, only for a "central control" device
TUYA_DEVICE_ID=
```

Then `/health` shows `"tuya": true` and these work:

- `turn on the kitchen light` / `switch off the desk lamp`
- `is the bedroom plug on`

Device names are matched fuzzily (word overlap, case-insensitive). Name devices
in the app the way you say them: *Kitchen Light*, *Desk Lamp*, *Bedroom Plug*.
Power is switched through the first `switch*` code the device reports
(`switch_1`, `switch_led`, …); state is read the same way. Offline or
unreachable devices answer with a spoken explanation instead of an error.

`tuya_setup.py` without `--write` only prints the block, useful for checking
credentials before touching `.env`.

## Local IoT (no cloud, no Tuya project)

Tuya Wi-Fi devices accept control on the LAN on port 6668, but every message is
AES-encrypted with a per-device 16-character **local key**. No key means error
`914`; that is why the cloud route exists. `iot.py` speaks the same local
protocol and needs only the key — no subscription and no cloud calls after
setup.

```powershell
# 1. find devices, IDs and protocol versions (also writes iot_devices.json)
.\.venv\Scripts\python.exe iot_scan.py

# 2. copy the template, rename each light, paste its local key
Copy-Item iot_keys.example.json iot_keys.json
notepad iot_keys.json
```

`iot.py` ignores entries without a key, so a half-filled file is safe. It also
reloads when `iot_keys.json` changes on disk, so pasting a key takes effect on
the next command — no hub restart needed. Once at least one key is present:

- `turn on the kitchen light` / `spegni la luce della cucina`
- `is the bedroom light on`
- `set the kitchen light to 40 percent`
- `dim the bedside lamp` / `brighten the desk light`

Names and `aliases` are matched fuzzily, so `kitchen`, `cucina` and
`kitchen light` all reach the same device. `kind` is `bulb` (DPS 20 power, DPS
22 brightness), `plug`, `switch` or `outlet` (DPS 1). Brightness is real
percentage: 40% maps to 10..1000 on `bright_value_v2` (≈400), or to the `v`
component of `colour_data_v2` when the bulb is in colour mode — it is **not**
tinytuya's deprecated 25..255 brightness value. `/health` reports `local_iot`
with the active device count.

Where to get the keys (once per device):

| Route | Cost | Notes |
|---|---|---|
| Smart Life QR login (`tuya-local-key`) | free | **easiest**: no developer account; the container is already installed at <http://127.0.0.1:8010> |
| Tuya IoT trial project | free | add the Europe data centre, link Smart Life, then *Device → Local key* |
| Rooted Android + Smart Life | free | key lives in the app's preferences/database |
| `tuya-cloudcutter` | free | only BK7231T/N, RTL8710BN, ESP chips; needs Linux + Wi-Fi adapter; replaces the key with a known one |
| Replace the bulbs | paid | Shelly, Tasmota, ESPHome, WiZ, Yeelight, Kasa, Hue and Matter are all cloud-free from the start |

The QR helper runs from the official upstream image and stays on localhost:

```powershell
docker run -d --name tuya-local-key --restart unless-stopped `
  -p 127.0.0.1:8010:8000 -v tuya-session:/data `
  ghcr.io/vineetchoudhary/tuya-local-key:latest
```

1. In Smart Life: *Me → Settings → Account and Security → User Code*, copy it.
2. Open <http://127.0.0.1:8010>, enter the code, scan the QR with Smart Life
   (*+ → Scan*) and confirm (the prompt may say *Home Assistant*).
3. Open a device, copy its **local key**, paste it into `iot_keys.json`.

Discovery/pairing mode (blinking) is **not** needed and does not reveal the key:
the key is issued by the cloud during pairing and stored encrypted in the app
and the device. `iot_scan.py` already reads the ID, IP, product key and
protocol version without pairing mode.

`iot_scan.py` also reports those cloud-free protocols when it sees them
(`yeelight`, `wiz`, `shelly`, `tasmota`, `esphome`, `hue`, `lifx`, `kasa`,
`wled`, `matter`, `homekit`, `chromecast`) and writes a JSON report. It uses the
ARP table, a TCP scan, SSDP, mDNS and the WiZ/LIFX/Kasa discovery broadcasts —
all read-only probes.

Configuration: `IOT_ENABLED=1` (default) and `IOT_KEYS_FILE` (empty = the
`iot_keys.json` next to the hub).

## Home Assistant (optional)

Set `HA_URL` (e.g. `http://homeassistant.local:8123`) and a long-lived
`HA_TOKEN`. When both HA and Tuya are configured, **Home Assistant is used
first**; Tuya is the fallback.

The speaker itself also has `GET/PUT /home-assistant` if you ever want it to talk
to HA without the hub.

## Wake word

The device ships exactly one wake-word model: **"Alexa"** (`GET /wake-word`
reports `"wake_word": "Alexa"`, `"model_status": "loaded"`). There is no custom
model upload and no phrase selection:

- `PUT /wake-word {"wake_word": "..."}` → `HTTP 501 io_error`
  ("Wake-word setting could not be applied")
- other keys (`phrase`, `model`) are accepted with `200` but silently ignored,
  and `wake_word` stays `Alexa`

So "Hey Olive" is not available on this firmware. The hub does not touch the
wake word — the speaker listens for "Alexa", then sends the rest of the sentence
to the hub's Wyoming ASR endpoint. Tunable fields that *do* work:
`enabled`, `sensitivity` (currently 67), `cooldown_ms` (1750).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `LibreEcho login failed (HTTP 401)` | `SPEAKER_PASSWORD` missing or wrong in `.env` |
| `I couldn't reach the speaker` | Speaker offline / IP changed (`SPEAKER_URL`) |
| Occasional silent reply, radio restarts | Device TTS refused the line (HTTP 501). The hub retries once and resumes the station; the JSON reply is always complete |
| `I couldn't find <thing> in Tuya` | Device name mismatch — rename it in the app or say the name it has |
| `/health` shows `"tuya": false` | `TUYA_API_KEY` / `TUYA_API_SECRET` missing — run `tuya_setup.py --write` |
| Port already in use | Old pipeline still running — see Ports |
| Voice stops working, `ConnectionRefusedError` in `olive_hub.err` | Whisper on 10301 is down — run `.\start-whisper-detached.ps1` (the watchdog also restarts it) |
| Weather in the wrong city | Set `WEATHER_LAT` / `WEATHER_LON`, or name the city in the sentence |
| "Hey Olive" doesn't wake it | Firmware only has the Alexa model — see Wake word |
| `TV not found` | TV off, asleep, on another VLAN, or discovery blocked; set `TV_CAST_NAME` / `TV_ADB_HOST` |
| Assistant answers the TV/radio | Echo guard plus Whisper hallucination filter; keep the speaker away from the TV, or say `stop` |
| Memory not updating | `MEMORY=0` or a read-only `OLIVE_MEMORY_FILE`; check `GET /health` → `memory_counts` |
| An old question swallows a command | Fixed: new commands/intents always outrank a pending clarification |

## Safety notes

- Defaults to `127.0.0.1`; set `HUB_HOST=0.0.0.0` (and `ASR_HOST=0.0.0.0`) when the
  speaker is on the LAN. Every route except `/health` needs `HUB_TOKEN`.
- Transcripts are never `eval`'d; arithmetic goes through an AST whitelist.
- The hub only writes to the speaker what you ask it to: announcements, volume,
  transport, station list, and timers.
- Tuya credentials stay in `.env`; `tinytuya` is imported lazily, so the hub
  runs fine without it.
