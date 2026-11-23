# Dashboard controls: voice, intents, wake word, latency

Added 23 Nov 2026 to the dashboard at `http://127.0.0.1:8767/`.

## 1. Voice picker (replace Alan)

**Voice picker** card → choose English or Italian → **Use** / **Download & use**.

- Catalog: 40 English and 4 Italian Piper voices, from the official
  `rhasspy/piper-voices` catalog (cached in `piper_voices_cache.json` for 24 h).
- Each row shows name, quality, model size and a speed hint. `low`/`x_low`
  models are 20–27 MB and noticeably faster on this CPU; `high` models are
  ~110 MB and slow.
- Downloading runs in a **background job**: the page polls `/api/voice/job`,
  so a slow Hugging Face download never times out the browser. The
  `.onnx` + `.onnx.json` land in `piper-wyoming/data/`.
- Selecting a voice edits `$enVoice` / `$itVoice` in
  `start-tts-detached.ps1` (a `.bak` is kept) and recycles the TTS stack
  (router 10200 + Piper 10201/10202). No hub restart needed.
- Verified end-to-end: downloaded `it_IT-riccardo-x_low` (27 MB), switched to
  it, then switched back to `it_IT-serena-medium`. Original state restored:
  EN `en_GB-alan-medium`, IT `it_IT-serena-medium`.
- To switch English away from Alan, pick e.g. `cori` (medium, natural female),
  `alba` (medium), `jenny_dioco` (medium), `northern_english_male` (medium) or
  `en_GB-alan-low` (faster, same voice).

## 2. Intent editor (add / edit / remove)

**Intent editor** card.

- Add a custom intent: name (e.g. `custom.coffee`), comma-separated regex
  patterns, and a spoken reply. `{text}` in the reply is replaced with the
  phrase that was heard.
- Tick/untick any built-in or custom intent to enable/disable it.
- Everything is saved to `custom_intents.json` and **hot-reloaded in the
  running hub** (`POST /intents`) — no restart, no downtime.
- Custom intents are matched **before** built-ins, so they can override a
  built-in phrase. Disabled names are skipped by the router and disappear
  from `/health`'s intent list.
- Verified: added `custom.pingtest` with pattern `ping test olive`, spoke the
  phrase via `/command`, got `intent=custom.pingtest` and the custom reply;
  then removed it and restored the template (72 built-ins, 0 customs).

File format:
```json
{
  "version": 1,
  "disabled": ["joke", "quote"],
  "intents": [
    {"name": "custom.coffee", "enabled": true,
     "patterns": ["make coffee", "prepare (the )?coffee"],
     "reply": "Starting the coffee machine.", "speak": "pipeline"}
  ]
}
```
Patterns are Python regular expressions (case-insensitive, `search`), so
groups like `(the )?` work. `speak` can be `pipeline` (normal TTS), `device`
or `none`.

## 3. Waking the speaker from a distance

**Wake word / mic** card.

- Wake sensitivity and microphone gain are read live from the speaker and can
  be set from the dashboard (`PUT /api/v1/wake-word` and `PUT /api/v1/audio`).
- Mapping documented by the project maintainer: detector threshold =
  `0.85 - 0.0044 × sensitivity`, i.e. 0 = strictest (0.85), 100 = most
  sensitive (0.41). Higher sensitivity = easier wake from across the room.
- **Changed on this speaker: sensitivity 67 → 80, mic gain 65 → 78.** If it
  starts false-triggering, lower sensitivity first (e.g. 72), then gain.
- Both settings persist in the speaker's `web-config.json` and survive
  reboots. Note: on 0.14.4+ mic gain is re-applied on every capture start,
  so the value now holds reliably.
- The speaker itself also supports tuning in its control centre under
  Audio → Microphones (beamformer/average/single mic) and Wake Word
  (sensitivity). 0.14.6 adds adjustable wake sensitivity in the UI plus
  custom wake-word models.

## 4. Response time

**Response time** card with **Fast / Balanced / Quality** presets and
**Measure now**.

Measured on this machine (real Wyoming connections from the dashboard):
| Stage | Time |
|---|---|
| ASR (Whisper `tiny`, 6.7 s of audio) | ~2.0–2.6 s |
| TTS first audio (Piper via router) | ~0.9–1.2 s |
| LLM, old `gpt-oss-120b` (reasoning) | 0.3–0.6 s plus long "thinking" |
| LLM, new `qwen/qwen3.8-27b` | 0.27–0.73 s, good English + Italian |

The old model was a **reasoning model**: it spent the token budget on thinking
before emitting spoken text, and often returned an empty answer (the hub then
tried the fallback). The fast preset uses a non-reasoning multilingual model.

Presets (written to `.env`; **restart the hub to apply**):
| Preset | `LLM_MODEL` | `LLM_MAX_TOKENS` |
|---|---|---|
| Fast (applied now) | `qwen/qwen3.8-27b` | 180 |
| Balanced | `openai/gpt-oss-20b` | 500 |
| Quality | `openai/gpt-oss-120b` | 900 |

`allam-2-7b` was tested and rejected: it is faster (0.3 s) but produces poor
Italian ("Ho quindici ore e mezza" for "che ore sono?"). `qwen3.8-27b` gave
correct, natural Italian.

**Applied now:** Fast preset. `LLM_MAX_TOKENS` is a new supported setting in
`olive_hub.py` (default 900).

## Bugs fixed along the way
- `start-tts-detached.ps1` passed `voice_personas.json` to the router without
  quoting the path; any path containing spaces broke the TTS router
  (`unrecognized arguments: and Rick\Documents\...`). Quoted now. This was a
  latent bug exposed when the TTS stack was first recycled.
- The `.env` editor's secret filter treated `LLM_MAX_TOKENS` as a credential
  (it matched "TOKEN"). Fixed with an explicit `MAX_TOKENS` exemption.
- Voice downloads no longer block the HTTP request (background job).
- Windows subprocess pipes from the TTS starter could hang forever because
  detached children inherit stdout; output now goes to `voice-switch.log`.
- `start-hub-detached.ps1` now recycles the dashboard on every hub start, so
  dashboard code changes take effect without manual process killing (the
  dashboard is often started elevated by the watchdog).
