# Olive hub — final audit and build log (22 Nov 2026)

## Executive summary

The speaker's "beep but no reply" and the music failure had **three unrelated
root causes**, all now fixed:

1. **Windows Firewall** silently dropped the hub's chat port 8766. The hub now
   listens on **8765**, which already has an inbound allow rule.
2. **Music Assistant's Docker container had exited** (255) and its live restart
   policy was `no`. It is running again with `unless-stopped`.
3. **Whisper `base` was too slow** on this CPU under load (up to 18 s for a
   5 s command vs the hub's 15 s timeout). It now uses multilingual **`tiny`**
   (~1 s for the same audio).

On top of that, the speaker now has **native Italian pronunciation** via a new
language-routing TTS proxy, the maths handler is correct, polluted memory was
cleaned, and `/health` distinguishes "configured" from "reachable".

---

## 1. What was broken and what was changed

### 1.1 Voice / chat path (fixed earlier in this session, re-verified)

| Problem | Fix |
|---|---|
| `agentd: voice response failed: ChatGPT response failed`, no HTTP request reached the hub | Firewall had no rule for 8766. Moved hub HTTP to 8765 (`.env`, watchdog, restart scripts, defaults, docs) and set speaker `base_url=http://192.168.1.86:8765/v1` with `api_key=HUB_TOKEN`. |
| No Wyoming ASR server existed on 10301 | Added `whisper_wyoming_server.py` + `start-whisper-detached.ps1`; watchdog starts it. |
| ASR proxy crashed with unhandled `ConnectionRefusedError` | `AsrProxyHandler` now catches upstream errors, rate-limits warnings, returns empty transcripts. |
| `POST /restart` left the hub down | Helper now calls the proven detached PowerShell starter. |
| `netstat` parser never matched `LISTENING` lines (PID column) | Fixed in all three PowerShell scripts. |
| `Olive Hub` scheduled task was Disabled | Enabled; S4U, Boot +30 s, 5-minute repeat. |

### 1.2 Music Assistant (fixed this session)

- `docker start music-assistant` + `docker update --restart unless-stopped music-assistant`.
- Container `Up`, port 8095 listening, `players/get` returns the `up31eb41d3`
  ("olive") player as `available`, `playback_state` responds.
- Live test: `POST /command {"text":"play Capital FM"}` →
  *"Playing Capital FM through Music Assistant."* Speaker `/sendspin`:
  `connected:true, streaming:true, state:playing`; MA player `playing`.
  Stopped again with "stop" afterwards.
- Added `music_assistant_reachable` to `/health` (real TCP probe) so a dead
  container is no longer reported as healthy just because config exists.
- Note: the live container was **not** created from the project
  `docker-compose.yaml` (it has HA-addon labels and bridge port mappings);
  do not recreate it from compose without intending to change networking.

### 1.3 ASR performance

- `WHISPER_MODEL=tiny` via **CLI flag** in `start-whisper-detached.ps1`
  (WMI-launched processes do not inherit `$env`). `tiny.pt` downloaded to
  `~/.cache/whisper`. Multilingual, so Italian still works.
- Measured: same 5 s test WAV went from **18 s** (base) to **~1 s** (tiny).
  Hub `ASR_TIMEOUT=15 s` no longer trips.

### 1.4 Italian voice and language behaviour (new)

New files:

- `piper-wyoming/wyoming_tts_lang_router.py` — Wyoming TTS proxy on **10200**
  (the port the speaker already uses). It inspects each `Synthesize` text and
  forwards to English Piper (**10201**, `en_GB-alan-medium`) or Italian Piper
  (**10202**, `it_IT-serena-medium`), relaying audio back verbatim.
- `start-tts-detached.ps1` — starts all three TTS services if their ports are
  free; integrated into `ensure-hub.ps1` and `start-hub-detached.ps1`.
- `it_IT-serena-medium.onnx` downloaded into `piper-wyoming/data`
  (native Italian phonemizer, `espeak_voice=it`).

Voice names are configurable at the top of `start-tts-detached.ps1`
(`$enVoice`, `$itVoice`) — swap in any installed Piper voice there.

Hub changes:

- `llm_answer()` appends an Italian instruction when the user writes Italian.
- `h_time`, `h_date`, `h_weather` now answer in Italian for Italian questions.
- Router adds Italian detection for short replies such as *"Sono le 00:13."*.
- Router swallows client TCP resets (no more unhandled task exceptions).

Verified:

- `Che ore sono?` → *"Sono le 00:13."* → router chose **it** → Italian voice.
- `Perché il cielo è blu?` → Groq answered in Italian → Italian voice.
- `why is the sky blue?` → English answer → English voice.
- `/audio/announce` with Italian text routes to `it_IT-serena-medium`.

### 1.5 Bugs from the feature audit (fixed)

- **Percentage maths**: `15 percent of 80` returned 15; now **12**. Bare
  `15 percent of 80`, `10% of 50`, `2 plus 2`, `12 times 3` all route and compute.
- **`personal_facts.json` was invalid JSON** (missing comma between `birthday`
  and `Juias birthday`) — repaired, both facts preserved.
- **Memory pollution**: junk fact/note removed, 25 hallucinated topics cleared.
  `memory.py` now rejects short/noisy ASR fragments on load and on absorb,
  caps topics at 12, and `I like ...` extraction is length-limited.
  Current counts: **1 fact, 0 notes, 0 topics**.
- Offline suites re-run: `intent_test.py`, `iot_test.py` (27/27),
  `features_test.py` (33/33) — all exit 0.

---

## 2. Feature status (live audit)

| Feature | Status | Evidence / note |
|---|---|---|
| Hub HTTP + ASR proxy | **Working** | 8765/10300 listening, `/health` 200 |
| Whisper STT | **Working** | multilingual `tiny`, ~1 s per 5 s audio |
| Wake word | **Working** | firmware "Alexa" only, `detected_count` increasing |
| Follow-up conversation | **Working** | echo guard, silent-window close, wake-only "I'm listening." |
| LLM (Groq `gpt-oss-120b`) | **Working** | replies in 0.2–0.8 s; Italian when addressed in Italian |
| Music Assistant | **Working** | container Up, Sendspin connected/streaming, live play verified |
| Native radio fallback | **Working** | curated catalog + radio-browser; MA preferred by default |
| TV (Chromecast + ADB) | **Working** | Bedroom TV 192.168.1.41, ADB :5555 + Cast :8009 |
| Local IoT (kitchen light) | **Working** | 1 device, `iot_test.py` 27/27 |
| Tuya cloud | **Effectively dead** | keys present but API returns 0 devices (data centre suspended); use local IoT |
| Home Assistant | **Not configured** | no URL/token; HA intents give truthful fallbacks |
| Telegram | **Not configured** | no bot token/chat id; clear "configure it" reply |
| Calendar | **Not configured** | `CALENDAR_ICS_URL` blank, no `calendar.ics`; set a URL or drop an ICS file named `calendar.ics` in the project |
| News | **Working** | GDELT headlines, country IT |
| Weather | **Working** | Open-Meteo, metric, San Vincenzo; now also Italian |
| Time / date | **Working** | local time only — "what time is it in Rome" still returns local time |
| Maths | **Working** (fixed) | percentages, bare expressions, AST-safe |
| Timers / alarms / reminders | **Working** | device-native timers + persisted schedule |
| Memory / facts | **Working** (cleaned) | see above |
| LED themes | **Working** | listening yellow, thinking orange |
| TTS | **Working** | English + Italian, language-routed |
| Restart / watchdog | **Working** | `/restart`, Boot +30 s, 5-min tick, 0 missed runs |
| Voice latency | **Improved** | device target 3 s; STT now ~1 s instead of 5–9 s |

---

## 3. Changing the voice / a voice "of our creation"

Right now the two voices are **Piper `en_GB-alan-medium`** and
**`it_IT-serena-medium`**. To change either, edit the two variables in
`start-tts-detached.ps1`, download the model, and restart:

```powershell
cd "C:\Users\Geggais and Rick\Documents\Codex\piper-wyoming"
.\.venv\Scripts\python.exe -m piper.download_voices it_IT-serena-high --download-dir data
# then edit $itVoice in start-tts-detached.ps1 and rerun it
```

Options for a truly custom voice, ranked:

1. **OmniVoice clone of your own voice (best, needs a recording).**
   `wyoming-piper` 2.5.3 already contains the OmniVoice backend. Install extras
   (`pip install "wyoming-piper[omnivoice-deps]==2.5.3"`), then provide a
   **3–10 s reference in Italian** (`ref.wav` + `ref.txt`) under
   `data/omnivoice_voices/it/<name>/`. Run with `--backend omnivoice` on a spare
   port (e.g. 10203) and point the router's Italian backend at it. First download
   is ~3.9 GB; CPU-heavy on this 8 GB machine. **The existing `test_input.wav`
   is English** ("…is lovely. Very lovely."), so cloning from it would make
   Italian sound English-accented — record a short Italian sample first.
2. **OmniVoice voice design** (`instruct.txt`, e.g. "female, low pitch, calm") —
   a made-up voice with no recording; English/Chinese-trained, Italian
   results unstable. Good for experimentation on a spare port.
3. **Kokoro voice blending** (`if_sara,im_nicola`) — lightweight, natural, needs
   a Windows Wyoming bridge and espeak-ng for Italian G2P.
4. XTTS/OpenVoice — not recommended here (CPU-only, 8 GB RAM, licence limits).

Ethics: only clone a voice with the owner's consent.

---

## 4. What happens when the computer resets

- The **Olive Hub** task triggers at **boot +30 s**, runs as S4U (no interactive
  login required), repeats every 5 minutes, and has `StartWhenAvailable`.
- `ensure-hub.ps1` checks **8765**, **10301** and **10200** and starts whatever
  is missing: hub, Whisper (`--model tiny`), and the full TTS stack
  (2 Piper backends + language router).
- Docker Desktop starts Music Assistant automatically now
  (`restart=unless-stopped`).
- The speaker keeps its assistant config; nothing to redo on the device.
- Voice is unavailable while the PC is off/asleep/booting. After boot it
  normally returns within 30 s–5 min. Firewall rules and `.env` persist.

---

## 5. Remaining recommendations

1. Rotate the API keys that appear in `.env` comments / old backups
   (`.env.txt`, `.env.backup-*`).
2. Fix "time in another city" by adding a small city→timezone map in `h_time`.
3. Configure `CALENDAR_ICS_URL` (Google/Outlook private ICS URL) or drop a
   `calendar.ics` in the project to enable the calendar intent.
4. Optionally enable Telegram/HA by filling their `.env` values.
5. Treat Tuya cloud as deprecated unless the data-centre issue is resolved;
   local IoT is the reliable light-control path.
6. If you want a cloned Italian voice, record the 3–10 s Italian sample and
   follow option 1 above.

---

## 6. Final verification (22 Nov 2026, ~00:22 local)

- `/health`: `ok=true`, `music_assistant=true`, `music_assistant_reachable=true`,
  memory `1/0/0`, `calendar=false` (expected).
- Listening ports: 8765, 10300, 10301, 10200, 10201, 10202, 8095.
- Docker: `music-assistant Up`, restart policy `unless-stopped`.
- Task `Olive Hub`: `Ready`, LastTaskResult 0, 0 missed runs.
- ASR end-to-end test: transcript returned, ~1 s Whisper time.
- Offline suites: intent / IoT (27/27) / features (33/33) all exit 0.
- All modified Python files compile cleanly.
