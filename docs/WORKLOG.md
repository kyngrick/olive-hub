# Olive work log

Running record of work on the Olive hub. Newest entries at the top.
Read this first when resuming: it says what is done, what is broken, and how to
restart everything.

---

## Current state (update this block every session)

| Item | Value |
|---|---|
| Hub | `olive_hub.py`, port **8765**, ASR proxy **10300**, 74 intents |
| Location / trains | `train_provider.py` + Transitous free API; `location` and `trains` intents; Travel dashboard card |
| Whisper ASR | `whisper_wyoming_server.py`, port **10301**, model `base` |
| Speaker | `http://192.168.1.228:8080/api/v1` (LibreEcho OS 0.14.5; stable 0.14.6 available, see upgrade note) |
| Dashboard | `http://127.0.0.1:8767/` — token-protected status/config/logs/intents/voice, customisable; voice picker, intent editor, wake/mic tuning, latency presets; watchdog auto-start |
| LLM | Groq `qwen/qwen3.8-27b` (fast preset, non-reasoning, 180 max tokens), fallback `openai/gpt-oss-20b` |
| Speaker mic | wake sensitivity **80** (was 67), mic gain **78** (was 65) — better far-field wake |
| Wake word | **Alexa** (firmware only ships this model; "Hey Olive" impossible) |
| LLM | Groq `openai/gpt-oss-120b`, fallback `openai/gpt-oss-20b` |
| Music | named stations via Music Assistant (`RADIO_PREFER_MA=1`), native fallback |
| Memory | `olive_memory.json` — facts + notes + topics (only `birthday` today) |
| TV | Bedroom TV `192.168.1.41`, Chromecast + ADB; MAC `ec:9c:32:72:9a:67` learned for Wake-on-LAN; full YouTube control |
| Local IoT | `iot.py` + `iot_keys.json` — **1 active**: kitchen light `192.168.1.179` |
| Key helper | `tuya-local-key` container on `127.0.0.1:8010` (QR login, no paid project) |
| Auto-start | Task **Olive Hub** S4U/Limited — logon + 5-minute watchdog; `POST /restart` for reloads |
| Tuya cloud | **blocked**: project data center suspended (code 28841107) |
| Start | `POST /restart` (daily), `.\\start-hub-detached.ps1`, or the scheduled task |
| Logs | `olive_hub.err` (live), `olive_hub.err.prev` (previous run) |

---

## 2026-11-24 03:00 — OTA outcome: 0.14.6 rejected by device, 0.14.5 restored

- With user approval, backed up speaker config and attempted 0.14.6 OTA.
- Pre-existing `update-held-after-rollback` blocked check/apply; manual signed
  upload of the official 0.14.6 OTA installed the **base image only**
  (feature payloads are separate assets downloaded by check/apply).
- Device now reports 0.14.6 with `features_state=degraded / pending-manifest`
  and **wake-word service unavailable** (`libreecho-waked` not running).
  Update state failed; boot record pending/unconfirmed.
- Repair attempts: dev-channel candidate (also 0.14.6) failed signature/
  installation verification; stable apply still held; 0.14.5 restore upload
  rejected `boot_store_not_committed`; bootctl confirm needs root (SSH user
  unprivileged). No HTTP endpoint exists to confirm/select boot slot.
- PC hub/dashboard unaffected and healthy (98 intents). Full status and
  user-action recovery steps in `OTA-RECOVERY-2026-11-24.md`.
- **Update:** after two reboots the bootloader rolled back to 0.14.5 with all
  features ready and boot confirmed. Retried the supported signed flow via the
  dev channel (also 0.14.6): check → apply succeeded with feature payloads, but
  after reboot 0.14.6 again showed `pending-manifest`, wake unavailable, and
  rolled back. Conclusion: 0.14.6 fails feature-manifest validation on this
  radar_puffin image — a firmware defect. Final state: 0.14.5, features ready,
  wake working, stable channel, all diagnostics OK. Stay on 0.14.5. Full details
  in `OTA-RECOVERY-2026-11-24.md`.

---

## 2026-11-24 01:50 — audit + authorised hub-side fixes

- Delivered read-only diagnostic report `EXPANSION-AND-AUDIT-PLAN-2026-11-24.md`
  (4 parallel audits: intents, voice/TTS, reminders/clock/AI, services).
  Key findings: device still 0.14.5; AEC reset bug is leading cause of wake
  trouble; 33+ Phase-1 intents and `intentReload` missing; LLM fallback skipped
  network errors; reminders only matched "remind me"; no timezone in hub;
  voice changes come from per-utterance language routing and persona registry.
- With user approval, implemented hub fixes (backups made first): LLM fallback
  on all failures; conversation settings applied; `Europe/Rome` timezone;
  widened reminder patterns + real recurring reminders; `alarm.snooze`.
- Added 24 functional Phase-1 intents (74 → 98), with no placeholder handlers;
  skipped shuffle/repeat/search/favourite/broadcast/intercom/light.color/scene
  because no real backend exists.
- Implemented atomic `intentReload` (`POST /intents/reload`) with duplicate/
  collision/speak/version validation and rollback on all failures; dashboard
  Reload button now really reloads.
- Created `UPGRADE-0.14.6-CHECKLIST.md` and gated read-only
  `tools/wake/ab_wake_test.py`; baseline smoke test saved `wake-ab-results.json`.
- Verified live: 98 intents, hub healthy, 9/9 services, new intents and reload
  tested. No firmware/model/device-config changes made. Test side effects
  restored.

---

## 2026-11-23 19:00 — location + train lookup

- Researched free train APIs. Chose **Transitous** (open MOTIS 2 API, no key,
  good EU/Trenitalia coverage, requires User-Agent + attribution). Rejected
  unreachable DB endpoint, UK-only National Rail, scraping and paid APIs.
- Added `train_provider.py`: geocode, reverse-geocode, journey plan with a real
  departure time, date parsing (tomorrow / on 25 November / next Friday),
  price-check link. Fixed wrong geocode hits (Rome→Paris, Milan→China).
- Added hub intents `location` ("what's my location", "where am i", "dove siamo")
  and `trains` ("check trains … from X to Y …") with spoken replies; added
  Travel / location dashboard card with a timetable table and price link.
- Verified: location replies correctly; Rome→Milan tomorrow 08:00 returns 07:01
  and 07:03 Frecciarossa options; 9/9 services, 74 intents. Full notes:
  `LOCATION-AND-TRAINS-2026-11-23.md`.
- Cost caveat: open feeds publish times, not ticket prices; Olive says so and
  opens a real price search instead of guessing.

---

## 2026-11-23 18:00 — full TV control, YouTube playback, alarm misroute

- TV: added Wake-on-LAN with auto-learned MAC (`tv_mac.json`, learned
  `ec:9c:32:72:9a:67`), `ensure_awake()` used by YouTube so media commands work
  with the screen off, ADB connect retries, reliable power on/off. Verified
  off→on, on→off, and off→YouTube (TV focus became the YouTube app).
- YouTube: `h_youtube` now handles "show me … on youtube" and "… music video"
  without the word YouTube, extracts clean search queries, wakes the TV,
  resolves a real video and opens it in the TV app. Both requested example
  phrases verified playing on the TV.
- Alarm "Timer for how long?" traced to **LibreEcho 0.14.5 firmware** (local
  timers exist; native alarms only arrived in 0.14.6), not the hub — hub
  returns `Alarm set for Tuesday 08:00` for the exact phrase. Added ASR
  correction (timer→alarm when a day/time word is present), stronger Whisper
  prompt examples, and extra alarm phrasings so "wake me at 8 tomorrow
  morning" reliably reaches the hub. Full notes:
  `TV-AND-ALARMS-2026-11-23.md`.
- `start-hub-detached.ps1` now recycles Whisper too, so prompt changes load
  after a hub restart.
- Test alarms were cancelled after verification.

---

## 2026-11-23 16:45 — voice picker, intent editor, wake tuning, latency work

- Added 4 dashboard cards: **Voice picker** (40 EN / 4 IT Piper voices,
  catalog from rhasspy/piper-voices, background download job, edits
  `start-tts-detached.ps1` and recycles TTS), **Intent editor** (add/delete
  custom regex intents, enable/disable built-ins, hot reload via new hub
  `/intents` GET/POST), **Wake word / mic** (live read/write of speaker
  sensitivity + gain), **Response time** (latency benchmark + fast/balanced/
  quality presets).
- Hub changes: `custom_intents.json` loader with hot reload, custom intents
  matched before built-ins, `/intents` endpoints, configurable
  `LLM_MAX_TOKENS` setting.
- Set speaker wake sensitivity 67→80 and mic gain 65→78 to fix "have to shout
  at Alexa from a distance". Applies via the documented 0.14 `PUT /api/v1/`
  endpoints and persists on the speaker.
- Diagnosed lag: ASR ~2.0–2.6 s, TTS first audio ~0.9–1.2 s, but the old
  `gpt-oss-120b` is a reasoning model. Compared Groq models: `qwen/qwen3.8-27b`
  0.27–0.73 s with correct Italian; `allam-2-7b` 0.3 s but broken Italian.
  Applied the **fast** preset (`qwen/qwen3.8-27b`, 180 max tokens) and
  restarted the hub.
- Fixed latent TTS router bug: unquoted `voice_personas.json` path with spaces
  broke every TTS restart. Fixed `.env` editor rejecting `LLM_MAX_TOKENS` as a
  secret. Voice switching is now a background job (no browser timeout).
- Verified: custom intent added/spoken/removed, voice downloaded+switched+
  restored, all endpoints 200, all 12 cards render. Full notes:
  `DASHBOARD-CONTROLS-2026-11-23.md`.

---

## 2026-11-23 12:45 — WhatsApp bridge stuck (observation only)

- `GET /health` reports `whatsapp_bridge.status = "starting"` with
  `lastError: "The browser is already running for ...session4\\session-olive-hub"`.
  Port 3014 is listening but the bridge client never initialised (QR png is
  from 22 Nov). No Chrome process is visibly attached to that profile now, so
  the lock error is stale.
- The bridge `node.exe` PID 27540 is owned by a protected account; both
  `taskkill /F` and `Stop-Process -Force` returned **Access denied** from the
  interactive shell, so it could not be restarted here. The watchdog will not
  restart it while port 3014 is listening.
- Manual fix when convenient: from an **elevated** PowerShell,
  `Stop-Process -Id 27540 -Force`, then run
  `.\\start-whatsapp-bridge.ps1`, then check `GET /health` for
  `whatsapp_bridge.status = "ready"` (or `qr` if a re-scan is needed).
- This is unrelated to the dashboard/LibreEcho work and was not performed.

---

## 2026-11-23 12:25 — dashboard login fix, customisation, config editor, LibreEcho research

- Fixed the login overlay: a rejected/stale token now shows a clear inline error
  ("Token rejected. Check HUB_TOKEN in .env") instead of silently reappearing.
  Valid tokens hide the overlay reliably; added a **Change token** button to
  lock the session and re-enter credentials. Background refresh no longer
  force-reopens the login box.
- Added a **Customize** panel: theme presets (Midnight/Ocean/Light/Forest),
  accent colour, show/hide cards, drag-free up/down card ordering, refresh
  interval (2–120 s) and editable quick commands. Preferences persist in
  browser `localStorage`; verified persistence across reload.
- Added a non-secret `.env` editor in the Configuration card (`POST /api/config`),
  with strict key validation, rejection of KEY/TOKEN/SECRET/PASSWORD keys (403)
  and a timestamped `.env.bak-*` backup before each write. Verified write +
  backup and 403 for `HUB_TOKEN`; removed the test backup afterwards.
- HTML is served with explicit `charset=utf-8`; repaired double-encoded
  characters caused by an earlier PowerShell edit.
- Researched LibreEcho: latest stable is **0.14.6** (17 Nov 2026); installed
  0.14.5. Verdict: safe in-place signed OTA via **System → System update →
  Check for updates** (do NOT hand-upload the file — broken in 0.14.6).
  Fixes Sendspin resume-after-voice and Wi-Fi pauses that affect our setup.
  Full assessment + pre/post-upgrade smoke tests in
  `LIBREECHO-UPGRADE-2026-11-23.md`. Upgrade not performed — user decision.
- Verified dashboard: 200 HTML, `text/html; charset=utf-8`, 9/9 services,
  72 intents, login/customisation flows tested in the browser.

---

## 2026-11-22 21:20 — self-test scripts and WSL host fix

- Added `tools/kokoro/test_kokoro.py`, `record_and_clone.py`, and
  `kokoclone_generate.py` so the user can test Kokoro TTS and clone a voice.
  Verified: Kokoro test writes `kokoro_en.wav` / `kokoro_it.wav` via WSL
  Tailscale IP; clone wrapper generated `wrapper_test_cloned.wav` (178 s).
- WSL localhost forwarding is off, so the router/client use the stable WSL
  Tailscale IP `100.113.130.123` (`KOKORO_HOST` in `.env`); added `--kokoro-host`.
- Wrote `HOWTO-TEST-VOICE.md` with exact commands and expected results.
- Auto-playback in the clone script was changed to non-blocking `os.startfile`
  after `sd.play/sd.wait` hung a headless session.

---

## 2026-11-22 20:20 — KokoClone notes implemented and tested

- Found the user's KokoClone project in WSL at `/home/olive/kokoclone`.
  Working interpreter is `/mnt/c/Users/Geggais and Rick/kokoclone/venv/bin/python`
  (the project-local venv is broken).
- Verified Kanade+Kokoro cloning: generated `clone_olive_test.wav` from the
  sample WAV in 153 s CPU (52 s load + 64 s conversion). Copied the result to
  `voice_refs/kokoclone_sample.wav` for listening. Too slow for live replies.
- Started the Kokoro Wyoming server on port 10222 (10220 is occupied by a
  Windows service); verified English and Italian named voices from Windows with
  valid PCM audio (7–14 s first audio, RTF 2.3–4.8 on this CPU).
- Added `start-kokoro-wyoming.ps1`, `tools/kokoro/run_kokoro_wyoming.py`,
  `--kokoro-port 10222` in the router, and registered `KokoroEN`/`KokoroIT`
  personas. User's real `ricky` clone was preserved.
- Research and numbers recorded in `TTS-RESEARCH-2026-11-22.md`.

---

## 2026-11-22 17:45 — clone wake phrase ASR robustness

- User's "Alexa clone" was heard as "Alexa cold"/"Alex Lacool" and routed to
  the LLM ("I'm not sure what you mean"). Added Whisper `initial_prompt` with
  Olive command vocabulary and `correct_clone_phrase()` fuzzy aliases in the
  router. Verified `Alexa cold`, `Alex Lacool`, `Alex recalled`, `Alexa clone`
  and `activate clone mode` all arm clone mode; `it's cold outside` does not.
- Tested `base` Whisper against the same synthetic audio: no better ("Alex
  recalled"), so the fast `tiny` model remains default.

---

## 2026-11-22 17:30 — clone mode built and tested; health KeyError fixed

- Added `activate clone mode` / `clone mode` / `start cloning` phrases and a
  `clone mode status` command. Clone mode now says *"Clone mode armed..."*,
  captures the next Wyoming utterance, asks *"Who is this?"*, saves the persona
  and can select it with `speak like <name>`.
- Verified end-to-end with the test WAV: armed → capture → name → saved persona
  → selected → cleaned up. Realistic pauses are needed because the echo guard
  ignores speech while the device is still announcing.
- Fixed a `/health` 500 caused by popping `_VOICE_CAPTURE["path"]`; now uses
  `.get()` and resets the key instead of deleting it.
- HUB_TOKEN in `.env` is still the original long token, not `echo play`; user
  clarification pending.

---

## 2026-11-22 16:45 — alarm time parsing + WSL HF CLI path

- Fixed alarm/reminder time parsing: new `parse_when()` honours tomorrow,
  tonight, morning, night, `8am`/`8pm`/`8 pm` and bare times. Verified:
  `tomorrow morning 8 am` → Mon 08:00, `tomorrow night 8 pm` → Mon 20:00,
  `8pm` → today 20:00, `tomorrow night at 8` → Mon 20:00. Test alarms cancelled.
- HF login is done from the **hub venv** using the `hf` CLI (installed
  `huggingface_hub 2.4.0` there). In WSL bash use forward slashes:
  `./.venv/Scripts/hf.exe auth login` (Windows backslashes are not a command).
  The Pocket binary was copied to `tools/pocket-tts/pocket-tts.exe` so it no
  longer lives in Temp.

---

## 2026-11-22 16:30 — HF terms guide + ZONOS3-Mini research

- Documented exact steps to accept the Hugging Face terms for Pocket TTS
  cloning (agree on `kyutai/pocket-tts`, create a Read token, `hf auth login`).
- Researched `Zyphra/ZONOS3-Mini`: public/ungated, Apache-2.0 weights, 43
  languages incl. Italian/Japanese, ~100M active params, raw-clip or 192-d
  embedding cloning, 27 built-in voices, emotion/accent steering, C++/ggml CPU
  backend. GGUF ~663 MB backbone + codec; PyTorch ~9 GB; HF Space ~10.5 GB.
- Verdict: more capable than Pocket TTS but **not lighter**; needs a ggml build
  (Rust present, CMake not on PATH) and ~1 GB download. Only ~0.6 GB free RAM
  here, so not installed. Speech config unchanged and verified on 10200.

---

## 2026-11-22 16:10 — TTS candidate research (Pocket/Sopro/OpenVoice/etc.)

- Researched Pocket TTS, Sopro, OpenVoice V2, KittenTTS 2, Hojo-TTS-Light and
  VoiceForge. Full comparison in `TTS-RESEARCH-2026-11-22.md`.
- Downloaded the Pocket TTS standalone Windows binary (7.9 MB) and verified:
  tiny EN model 16× real-time; Italian 100M model 1.29× real-time with correct
  WAV output. Cloning is gated behind Hugging Face terms/login; built-in voices
  work. `export-voice` to safetensors is available for reusable embeddings.
- Recommendation: Pocket TTS first; Sopro only for English cloning; KittenTTS 2
  Ternary best multilingual but needs ~6 GB RAM (unavailable now).
- No live speaker/TTS config changes were made; speech stays on 10200.

---

## 2026-11-22 15:50 — speech restored after 10205 firewall block

- Speaker stopped speaking after TTS router moved to 10205: `ttsd` child exited
  code 1; no requests reached either router. Cause: Windows Firewall has no
  inbound allow rule for 10205 and adding one needs admin.
- Restored speaker to `tcp://192.168.1.86:10200` + `en_GB-alan-medium`.
  Verified announcement and "what time is it?" both speak again (`result=0`).
- `.env TTS_ROUTER_PORT=10200` and watchdog check 10200 restored. The
  persona-capable router will take over 10200 after the next reboot, so
  `speak like` will then route correctly. Documented in `VOICE-CLONING.md`.

---

## 2026-11-22 15:40 — voice persona/clone framework (Kokoro research)

- Researched Kokoro vs OmniVoice: Kokoro has fixed voices/blends (Italian
  `if_sara`/`im_nicola`) but **cannot clone**; OmniVoice clones from 3–10 s
  audio but needs ~4 GB models while this PC has ~0.9 GB free, so it cannot run
  here. Documented both in `VOICE-CLONING.md`.
- Built the full framework: ASR-proxy audio capture (`voice_refs/capture.wav`),
  `voice_personas.py` registry, `clone` → record → "Who is this?" → save
  persona, `speak like <name>` / `talk like <name>`, `list voices`, and
  `use my normal voice`. TTS router reads the registry on every request and
  overrides the voice (Piper now, Kokoro/OmniVoice slots ready on 10204/10203).
- New TTS router on **10205** (old 10200 process is locked to another session);
  `start-tts-detached.ps1` now uses `Start-Process` so args with spaces work;
  watchdog and speaker TTS URI updated to 10205.
- Verified: clone flow saved a persona from the test WAV; router override sent
  an English request to `it_IT-serena-medium`. Test persona removed (it was not
  the user's voice). 71 intents registered.
- Docs: `VOICE-CLONING.md`; repo updated with new module/scripts.

---

## 2026-11-22 15:10 — portable speaker, WhatsApp Giulia 2, repo push

- **Portable speaker research**: speaker is ARMv7 minimal Linux with no
  package manager/root, so Tailscale cannot be installed on it. PC already has
  Tailscale `100.108.79.37`; hub binds 0.0.0.0. Verified the speaker cannot
  reach the tailnet from home WiFi, so a Tailscale **travel router** is required.
  Added `switch-olive-endpoint.ps1` (updates assistant base_url + STT URI, keeps
  voice settings) and `REMOTE-SPEAKER.md` with setup, firewall checklist and
  what works/doesn't away from home. Tested the script with `-WhatIfOnly` and a
  real LAN re-apply; config remains `192.168.1.86`.
- **WhatsApp bridge moved to port 3013 with session3** after old processes on
  3011/3012 became unkillable (other Windows session) and copied Chrome
  profiles failed to reach web.whatsapp.com. Fresh headless session linked via
  QR; watchdog and hub updated. Added contact dedup (duplicate rows share one
  chat id).
- **Test message sent**: `send a whatsapp msg to Giulia 2, saying tst amo love
  ignore` → *"WhatsApp message sent to Giulia 2. It said: tst amo love ignore."*
  Bridge counter 0 → 1.
- GitHub repo `kyngrick/olive-hub` pushed again (`efbca65`) with the portable
  guide and dedup fix.

---

## 2026-11-22 14:00 — music ducking, WhatsApp polish, GitHub staging

- **Music ducking for commands**: when the speaker opens the mic (ASR
  `Transcribe`), music is dropped from the current level to the minimum (~4 at
  volume 30) before the user speaks, and restored after the reply/follow-up or
  by a 25 s watchdog. Verified live: 30 → 4 → 30.
- **WhatsApp polish**: robust message extraction (`msg`, quotes, commas),
  contact lookup with disambiguation via pending clarification, short-message
  playback in the confirmation, and TTS-friendly phone numbers (spoken digit
  groups, contact name preferred). Bridge currently cannot reach
  web.whatsapp.com from its Chrome client despite PC connectivity; linked
  session restored to `session2` for automatic reconnect.
- **GitHub repo staged** in `%TEMP%\opencode\olive-hub-repo`: sanitized 48 files,
  new README with quick start, commands, config table, ports, privacy notes,
  and all dated reports in `docs/`. Local `main` commit `899c298` created.
  Publishing needs a GitHub token (`gh` 2.105.0 downloaded to temp, not logged in).
- **Other improvement ideas** documented in the final answer: commute times,
  air quality/UV, sports scores, crypto/stocks, translation, offline Ollama
  fallback, web dashboard, geofenced reminders, multi-room audio, custom voice.

---

## 2026-11-22 02:15 — WhatsApp linked; SerpApi flights live

- WhatsApp QR linking succeeded after switching the bridge to a visible Chrome
  window and an auto-refreshing embedded QR page (`qr-live.html`). Bridge and
  hub both report `ready`; contact lookup now caches contacts (it timed out on
  the first uncached `getContacts()`).
- Added `flights_provider.py` with the supplied `SERPAPI_API_KEY`: cached
  (60 min), capped at 5 searches/day, `FLR` origin (PSA returned no results).
  Live test spoke ITA €70 Florence→Rome; repeated call used cache (count stayed
  1). Account reports 240 searches left on the Free Plan. `/health` exposes
  `flights` status.
- After a server restart the old bridge (port 3011, another session, cannot be
  killed here) got stuck reporting `authenticated` and refusing sends. Started
  a replacement on **3012** using a copy of the linked Chrome profile
  (`session2`), switched `.env` URL/port/session, made the starter use
  `Start-Process` so env vars inherit, and updated the watchdog. No re-scan
  needed. Hub and bridge both report `ready`; `whatsapp status` and
  `whatsapp contacts` both work.
- **Live send test passed:** `send a whatsapp to +393482763421 saying Olive
  test - ignore this` → `whatsapp.send` → *"WhatsApp message sent to
  +393482763421."*; bridge counter went 0 → 1. Added E.164 direct-to-`@c.us`
  handling in the bridge. User to confirm receipt on the phone.

---

## 2026-11-22 01:55 — WhatsApp bridge built; flight-API research

- Researched Brave/Tavily/Google PSE: none provide live flight fares; Google
  Custom Search is closed to new customers. Kept the honest Google Flights
  link fallback. SerpApi is the fastest paid route to real prices; Duffel is
  the route to booking. Full comparison in `INTEGRATION-PLAN-2026-11-22.md`.
- Built local WhatsApp bridge (`whatsapp-bridge/`, whatsapp-web.js + Express +
  system Chrome) on 127.0.0.1:3011 with token auth, contact lookup and send.
  Added `whatsapp.py`, `start-whatsapp-bridge.ps1`, 3 hub intents (68 total),
  `/health whatsapp_bridge`, and wired it into watchdog/start scripts.
- Verified bridge starts, writes QR, and hub handles `whatsapp status` and
  `send a whatsapp to mum saying hello` truthfully while unscanned.
- **Pending:** user must scan `whatsapp-bridge\qr.png` with WhatsApp →
  Linked devices, then I can do the live send-to-self test.
- Detailed plan/risks: `INTEGRATION-PLAN-2026-11-22.md`.

---

## 2026-11-22 01:00 — routines, calendar write, search, YouTube, discovery, extras

- Built and live-tested: sunset routine (`turn on the kitchen light 4 minutes
  before sunset` → scheduled device action with DST-correct Open-Meteo sunset),
  calendar write/read (`calendar_write.py`, `calendar.add` intent, `/health`
  calendar fields), LAN device discovery (`iot_discover.py`, `iot.scan`,
  `POST /scan`, keyless staging in `iot_pending.json`).
- Restaurants via Nominatim (`places.py`), flight-search links only
  (`web_helpers.py`, no booking/prices), YouTube via native TV app
  (`tv.adb_view_url`, ADB URL quoting fixed), Netflix verified.
- Extras: shopping list, jokes, quotes, unit + currency conversion, morning
  briefing. Hub now registers **65 intents**.
- Tests: all live phrases returned the expected intent/reply; offline suites
  intent/iot(27/27)/features(33/33) pass; all files compile. Test calendar
  entries removed, shopping list cleared.
- Full report: `FEATURE-BUILD-2026-11-22.md`.

---

## 2026-11-22 00:22 — music restored, Italian voice built, final audit

- **Music Assistant was down**: Docker Desktop crashed at 23:10, MA container
  exited 255 with restart policy `no`. Started it and set
  `docker update --restart unless-stopped music-assistant`. Verified Sendspin
  `connected/streaming`, live `play Capital FM` through MA, then stopped.
  `/health` now has `music_assistant_reachable` (real TCP probe).
- **ASR latency fixed**: Whisper `base` took up to 18 s for 5 s audio (hub
  timeout 15 s). Downloaded multilingual `tiny` and pass `--model tiny` from
  `start-whisper-detached.ps1`; same test now ~1 s.
- **Italian voice built**: `piper-wyoming/wyoming_tts_lang_router.py` routes
  Wyoming TTS on 10200 to English Piper (10201) or Italian Piper (10202,
  `it_IT-serena-medium`, downloaded). `start-tts-detached.ps1` starts all three
  and is wired into the watchdog and `start-hub-detached.ps1`. Hub prompts and
  time/date/weather intents now answer in Italian when spoken to in Italian.
- **Audit fixes**: percentage maths (`15 percent of 80` → 12, was 15), bare
  expressions, invalid `personal_facts.json` repaired, memory junk/noise
  filtered and cleared (now 1 fact, 0 notes, 0 topics).
- **Tests**: intent/iot(27/27)/features(33/33) pass; all files compile;
  `/health` 200 with `music_assistant_reachable=true`; task Ready.
- **Full report**: `FINAL-AUDIT-2026-11-22.md`. Custom/cloned voice options
  are documented there (needs a 3–10 s Italian sample for a true clone).

---

## 2026-11-21 23:47 — voice restored: firewall blocked the chat port

- **Symptom**: "Alexa" beeped, STT worked (`agentd: transcript ... text_chars=22`)
  but there was no spoken reply and `agentd: voice response failed: ChatGPT
  response failed`. Hub log showed the ASR connection and transcript, but **no
  HTTP request** from the speaker.
- **Research**: `/assistant` on the device pointed at `http://192.168.1.86:8766/v1`
  with a key configured. TCP tracing showed connections to 10300/8080 only —
  none to 8766. SSH into the speaker and `curl` from the device proved
  `192.168.1.86:8766` **timed out**. Windows Firewall has inbound allow rules
  for 10300, 10301 and 8765, but none for 8766; the generic Python rules are
  Public-only and WiFi is Private. Adding a rule needs admin, which this
  session does not have.
- **Fix**: moved the hub HTTP listener to the already-allowed **8765**
  (`HUB_PORT=8765`, watchdog/restart scripts, defaults, docs), re-applied the
  speaker's assistant config with `base_url=http://192.168.1.86:8765/v1` and
  `api_key=HUB_TOKEN`, and restarted its voice pipeline.
- **Verified**: device now logs `POST /v1/chat/completions ...
  "libreecho-agent/0.1" 200`; hub routed `"Alex, what's the time?"` →
  `intent=time` → *"It's 23:47."* and opened the follow-up window.
  `/assistant/respond` returns 200 `queued:true`.
- **After a PC reset**: the `Olive Hub` task has a Boot trigger (+30 s), S4U
  principal, and a 5-minute repetition. It starts 8765/10300 and Whisper on
  10301 automatically. The speaker keeps its config; voice returns once the
  PC has booted (no user login normally required).

---

## 2026-11-21 23:30 — ASR outage fixed, watchdog hardened, `/restart` repaired

- **Root cause**: no Wyoming ASR server was running on 10301. Every speaker
  request reached the hub's proxy (10300), which then threw an unhandled
  `ConnectionRefusedError` (WinError 1225) — the "Task exception was never
  retrieved" lines in `olive_hub.err`. Voice had effectively been dead since
  the old pipeline was retired.
- **Fix**: added `whisper_wyoming_server.py` — a small Wyoming server using the
  already-installed `openai-whisper` and cached `base` model. It loads the model
  once at startup, converts PCM to 16 kHz mono, and returns one `Transcript`.
  Started with `start-whisper-detached.ps1`; the Olive Hub watchdog now checks
  10301 and restarts it, and `POST /restart` calls the same starter.
- **Hardening**: `AsrProxyHandler` in `olive_hub.py` (and `AsrProxy` in the
  attached `olive_hub3.py`) now catch connect/write/read failures, rate-limit
  warnings to one per 30 s, and return an empty transcript instead of killing
  the handler. Device disconnects no longer produce unhandled task exceptions.
- **Second real bug**: `start-hub-detached.ps1` used the regex
  `LISTENING\s*$` on `netstat -ano`, but `-ano` appends a PID, so it never
  matched. The script therefore never saw (or killed) a running hub; log
  rotation failed with "file in use" and restarts were unreliable. Fixed in
  all three PowerShell scripts.
- **Third bug**: `POST /restart`'s CMD helper launched Python with
  `start /b cmd /c`, which died with the helper — the hub stayed down. The
  helper now calls the proven detached PowerShell starter. Verified: `/restart`
  returns 200 and `/health` is back within ~12 s.
- **Also**: scheduled task `Olive Hub` was **Disabled**; re-enabled (Ready).
  Fixed the syntax error in `test_play.py` and stale IP/doc references.
- **Verified**: `asr_e2e_test.py` sends `test_input.wav` through 10300 and gets
  a transcript back; watchdog restart of a killed Whisper works; both hub and
  Whisper listening after a full `/restart`. No new errors in `olive_hub.err`.

---

## 2026-11-21 01:30 — stop/volume, station matching, MA queue fix, self-restart

- **Bare "stop" now silences the room.** `STOP_COMMAND` is checked at the top of
  `Router.route`, before pending clarifications and radio, and again in the
  voice `answer()` path. `stop_everything()` dismisses a ringing device timer,
  stops an announcement bus, Music Assistant, then native radio; it returns
  `None` only when nothing is active, so a bare "stop" can still close the
  thread. `cancel my alarms` also cancels/dismisses the matching native timers.
- **Volume**: `louder` / `quieter` / `più forte` / `più piano` / `turn it up` /
  `turn it down` added to `volume.set`; live check moved 5 → 15 → 5 and restored.
- **Station names match phonetically.** `station_key`/`station_keys` normalise
  "BBC one extra", "BBC 1Xtra" and "bbc1xtra" to one key; `station_in_text`
  catches a bare station after a question ("and Capital FM") and ignores real
  questions. Clarification replay tries the answer first, then the original
  sentence on "yes", and only accepts actionable intents.
- **Music Assistant reliability.** `play_url` clears the player queue before
  `play_media` (a stale item made it a no-op), `ma_url` prefers a direct MP3/AAC
  stream and falls back to radio-browser for HLS-only entries, and
  `verify_streaming` waits 10 s. After rebooting the speaker, both
  `CapitalMP3` and the BBC 1Xtra HLS stream reported `streaming: true` for 30+ s.
- **Self-restart added.** The S4U task's PowerShell can take minutes to start in
  session 0 (or stall), so `POST /restart` writes `restart-helper.cmd` and runs it
  detached. The helper shares the hub's token, kills the process holding
  8765/10300, waits for the ports and starts the venv Python. The watchdog still
  handles `restart.request`; `Stop-ScheduledTask` clears a stalled instance.
- **Tests**: `features_test.py` 33 checks pass (stop phrases, ringing alarm,
  alarm cancel, volume, MA routing, bare station, clarify replay); `iot_test.py`
  and `intent_test.py` exit 0. Live `/command` test before the reboot showed the
  native fallback working (MA was wedged); after the reboot MA streamed.
- **TODO next**: reload via `POST /restart`, then live-verify "play bbc one
  extra" through `/v1/chat/completions` (expect "through Music Assistant") and a
  3-second timer + "stop" (expect `ringing: 0`). Then the voice work: device
  `PUT /audio {"tts_voice":"southern-female"}` or a custom Wyoming TTS
  (`PUT /voice-pipeline`), and evaluate Kokoro/XTTS/OpenVoice for cloning.

---

## 2026-11-19 11:00 — kitchen light keyed, brightness fixed, auto-start task

- **Kitchen light is live locally.** The QR helper's `/api/devices` returned the
  Smart Life device `kitchen light` (`bfbaa8008d33d20242v6ne`, 16-char key).
  Written into `iot_keys.json` with LAN IP `192.168.1.179` (the cloud row only
  knew the public IP) and version `3.3`. `/health` shows `devices: 1`.
- **Local tests pass**: status read (`switch_led=true`, `bright=1000`),
  turn off → verify off, turn on → verify on. Voice checks:
  `is the kitchen light on` → *kitchen light is on.*;
  `set the kitchen light to 40 percent` → applied.
- **Brightness bug fixed**: tinytuya's `set_brightness()` is not percent-based
  (25..255); 40% was becoming 4%. `iot.py` now maps percent to
  `bright_value_v2` 10..1000 (40% ≈ 406) and to the `v` component of
  `colour_data_v2` in colour mode. Verified 100 → 1000, 40 → 406, restored 100.
- **Only 1 of the 4 scanned Tuya devices is in this Smart Life account.** The
  `.71` / `.168` / `.175` bulbs (v3.4) remain keyless; `.179` is the kitchen
  light.
- **Scheduled task `Olive Hub` installed** (Interactive, Limited): runs
  `ensure-hub.ps1` at logon and every 5 minutes; starts only when 8765 is free.
  Tested: while up it exits 0 and leaves the hub alone; after killing the hub it
  restarted it and `/health` returned 200. Logs `hub-watchdog.log`.
- **UAC for the S4U variant was canceled**, so this version starts only while
  the user is logged on. `register-olive-task.ps1` is ready for one elevated
  run to get pre-login/logout survival (still Limited, no password).
- `ensure-hub.ps1` now captures the start script output as UTF-8 instead of
  PowerShell's default UTF-16 append.

---

## 2026-11-19 10:39 — hub outage found, false "Done." fixed

- **Hub was down**: last log line was 05:04:55; `/health` refused connections
  at 10:39. Cause not logged (clean stop, no traceback — likely the detached
  process was closed or the machine slept). Restarted with
  `start-hub-detached.ps1`; health OK, memory still only `birthday`.
- **Real bug found in the 05:04 transcript** `Turn it kitchen light off`: it
  missed every home intent and fell through to the LLM, which answered
  *"Sure, turning off the kitchen light. Done."* while switching nothing.
- **Fixes** (all tested):
  - `ha.off` also matches `turn/switch/shut <target> off` (incl. "turn it … off").
  - `ha.on` also matches `turn/switch <target> on`.
  - New `HOME_CONTROL` guard in `answer()`: if a home-control phrase matches no
    backend, the reply is deterministic and truthful ("Add its local key to
    iot_keys.json…"), never the LLM.
  - System prompt now forbids claiming a device was switched unless it was.
- **Verified**: exact phrase → `intent=tuya.toggle`, reply *"I couldn't find
  kitchen light. Add its local key to iot_keys.json to control it offline."*
  `iot_test.py` all pass; `intent_test.py` exit 0.
- **Open**: no keys yet (0 local devices); QR login at
  <http://127.0.0.1:8010> still pending. Consider a Task Scheduler entry so the
  hub restarts after a logout/reboot.

---

## 2026-11-19 (later) — local IoT driver wired in

### Done

- **`iot_scan.py`**: full-LAN discovery (ARP + TCP + SSDP + mDNS + WiZ/LIFX/
  Kasa probes + HTTP fingerprints). Found **4 Tuya Wi-Fi devices**:
  `.71` / `.168` / `.175` (v3.4, product `evzoxk3oozkxsuad`) and `.179`
  (v3.3, product `keytg5kq8gvkv9dh`), all AES-encrypted.
- **`iot.py`**: registry + Tuya LAN driver. Loads `iot_keys.json`, ignores
  entries without a key, fuzzy-matches names/aliases, runs blocking socket
  work in threads. Supports `status`, `is_on`, `turn_on/off` (tinytuya picks
  the DPS: 20 for bulbs, 1 for plugs) and `set_brightness` (DPS 22).
- **Hub wiring**: `Settings.iot_enabled` / `iot_keys_file` (`IOT_ENABLED`,
  `IOT_KEYS_FILE`), `Context.iot`, construction in `run()`. `home_toggle`
  tries local IoT before Tuya; `h_ha_state` reads local state; new
  `light.brightness` intent (`set the kitchen light to 40 percent`,
  `dim the bedside lamp`, `brighten …`). `/health` now has `local_iot`
  counts + `light.brightness` in the intent list.
- **`iot_keys.example.json`**: pre-filled with the four discovered devices and
  their IDs/IPs/versions, keys empty.
- **Offline tests** (`%TEMP%\opencode\iot_test.py`, exit 0): load/skip rules,
  alias/partial matching, no false match, missing file, intent routing vs
  `ha.on`/`ha.off`, example file has 0 active devices, handler message.
- **Regression**: `intent_test.py` updated for the new `Context.iot` field;
  full suite passes (exit 0). Hub restarted; `/health` shows
  `"local_iot": {"enabled": true, "devices": 0, …}`.
- **Docs**: README gained "Local IoT (no cloud, no Tuya project)" and the TV
  ADB fallback notes; `.env.example` gained `IOT_ENABLED` / `IOT_KEYS_FILE`.

### Live checks + key helper (same day)

- **Alarms**: live `/command` set 23:59 → *Alarm set for 23:59.*, list →
  *Alarms: 23:59.*, cancel → *Cancelled 1 alarm.*; `olive_schedule.json`
  back to `[]`.
- **Conversations**: `/reset`, then two chat turns. `history` grew 0 → 2 → 4
  → 6 and the LLM answered *"You just came back from Lisbon."* from context.
  A turn sent while the reply was still being announced was correctly ignored
  by the echo guard; re-sent 5 s later it worked. TTL 120 s, 10-turn cap.
- **Memory**: the live test re-added `sister: Anna` and `favourite colour:
  teal`; both removed again via `DELETE /memory?key=…`. Only `birthday`
  remains.
- **QR key helper installed** (official upstream image
  `ghcr.io/vineetchoudhary/tuya-local-key:latest`) as container
  `tuya-local-key`, bound to `127.0.0.1:8010`, session volume
  `tuya-session`, restart policy `unless-stopped`. Web UI returns HTTP 200.
  User still has to do the Smart Life QR scan (needs their phone/app).
- **Pairing mode**: confirmed it does not expose the local key. It only helps
  `tuya-convert` (ESP8266-only, obsolete) or `tuya-cloudcutter` (specific
  chips, Linux + Wi-Fi adapter). The QR helper is the safe free route.
- **Hot reload**: `iot.py` now watches `iot_keys.json` mtime and reloads on
  access. Verified live: adding a fake device showed `devices: 1` in `/health`
  and routed `turn on the kitchen light` to `iot.toggle` without a restart;
  removing it went back to `devices: 0`. `iot_keys.json` exists (copied from
  the example) and waits for the real keys.

### Still open

- **No keys yet** → 0 devices active. User must finish the Smart Life QR login
  at <http://127.0.0.1:8010>, then paste the keys into `iot_keys.json`
  (or hand them over and they will be written in).
- Wrong-key attempts can temporarily lock a device; do not brute force.

---

## 2026-11-19 — TV verified, memory cleaned, IoT fallback research started

### Done

- **Memory cleaned**: removed the test fact `sister: Anna` and all test topics;
  `olive_memory.json` now holds only `birthday: october 29`. Hub restarted so
  its in-process copy matches the file.
- **TV fully verified** after the user enabled ADB debugging on the Bedroom TV:
  - discovery → `Bedroom TV` at `192.168.1.41` (model `AI PONT`)
  - ADB connects, `power=Awake`, `volume=26`, focus readable
  - `power_on` works, `launch netflix` puts `com.netflix.ninja` in the foreground
  - `cast_audio` plays a live MP3 stream (`PLAYING` for 20 s), `cast_stop` works
- **`tv.py` improvements** (all verified):
  - `status()` now falls back to ADB for the foreground app and volume, because
    an Android TV reports an empty Cast session and volume 0.
  - `_media()` stop/pause/play fall back to ADB key events (STOP=86, HOME=3,
    PAUSE=127, PLAY=126) when the Cast controller has no active session.
  - `cast_volume()` reads/sets via `media volume --stream 3 --get/--set/--adj`,
    which is the same stream the remote controls.
  - `_cast()` waits for the control socket (`cast.wait`) and `cast_audio()`
    retries once on `NotConnected`.
  - `_adb_host_for_cast()` forces a discovery retry when mDNS answers late.
  - `adb_state()` parses `mWakefulness=` correctly (was matching
    `mWakefulnessChanging=false`) and reports volume + focus.
- **Hub-level checks**: "what's on the tv" → *Bedroom TV is showing a cast.
  Volume 26.*; "open netflix on the tv" → *Opening Netflix.*

### Open / next

- **Tuya**: still `28841107` (data center suspended). Free fix: enable the
  Europe Data Center and link the Smart Life account. Fallback plan in
  `README-olive-hub.md` → local IoT discovery.
- **IoT without Tuya cloud**: `iot_scan.py` (new) finds lights on the LAN and
  reports which local protocol they speak.
- **Music Assistant**: device Sendspin client still wedges after idle; the hub
  falls back to native radio. "reboot the speaker" fixes it.

---

## How to resume

```powershell
cd "C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2"
.\start-hub-detached.ps1                 # restart, rotates logs
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/health | Select -Expand Content
.\venv\Scripts\python.exe -X utf8 C:\Users\Geggais\Rick\AppData\Local\Temp\opencode\intent_test.py
```

Test scripts in `%TEMP%\opencode` (not part of the app):

| Script | Purpose |
|---|---|
| `intent_test.py` | offline intents / pending / memory / TV (exit 0 = pass) |
| `chat_live4.py` | live timer, LLM, memory regression |
| `ma_wedge_test.py` | Music Assistant playback + wedge detection |
| `dev_reboot.py` | reboot speaker and wait for reconnect |
| `dev_state.py` | wake word / playback / audio snapshot |
| `tv_live_test.py` | TV discovery, ADB, cast, app launch |
| `iot_test.py` | local IoT registry, matching, light intents |
| `iot_scan.py` (project) | LAN discovery of lights and IoT protocols |

---

## Earlier history (condensed)

- Hub built as the replacement for `New_pipe2.py`: radio, Music Assistant,
  Tuya, TV, permanent + temporary memory, wake-word pipeline with 2-minute
  follow-up conversations, LED feedback, alarms/timers/reminders, news,
  calendar, Telegram, personal facts.
- Cutover done: device STT → hub `10300`; device `/assistant` → hub `/v1`,
  model `olive`; `SUPPRESS_TTS_ON_DEVICE=1` (hub speaks via `/audio/announce`).
- Permanent memory (`memory.py`) with `/memory` GET/POST/DELETE and health counts.
- Music Assistant integration with explicit hint routing and automatic native
  fallback; documented Sendspin wedge and the reboot remedy.
- Device firmware cannot change the wake word (Alexa model only, no upload).
- Tuya: region `eu` correct; all calls fail with the suspended-data-center error
  until the Tuya IoT project is enabled and the app account linked.
