# Olive Hub — expansion & consistency audit (diagnostic phase)

Date: 24 Nov 2026. **Read-only. No firmware, model, config or deployment changes
have been made.** This document is the requested diagnostic report and
implementation plan; approval is required before any modification.

## Part A — Current device state (verified today)

- Speaker: **LibreEcho OS 0.14.5**, kernel 6.1.178+, slot A. It has **not** been
  upgraded to 0.14.6.
- Wake: Alexa, `alexa_v0.1`, sensitivity **80** (threshold 0.498), detected_count
  400, capture and inference active.
- Audio: volume **25**, mic gain **78**, not muted, digital gain 4.0×.
- Hub: `qwen/qwen3.8-27b` fast preset, **74 intents**, 9/9 services healthy at
  last check.
- Music source in recent device logs: the speaker's **own radio**, not Music
  Assistant. Hub ducking happens only *after* wake (ASR stream start).

## Part B — Phase 2: custom wake word investigation

### B1. Engine currently running
`/usr/local/sbin/libreecho-waked` on the device, started with:
```
--mic-socket /run/libreecho/mic.sock
--reference-socket /run/libreecho-audio/aec-reference.sock
--model-dir /run/libreecho/features/wakeword/root/usr/local/share/libreecho/openwakeword
--wake-threads 1 --vad-floor-rms 16
```
It is the **signed on-device wake detector** using **openWakeWord ONNX graphs**
(melspectrogram → embedding model → classifier). Logs show `model=alexa_v0.1`.
The classifier runs every 80 ms (1280 samples at 16 kHz).

### B2. Where the current model lives
- Runtime path (process argument):
  `/run/libreecho/features/wakeword/root/usr/local/share/libreecho/openwakeword/`.
- This directory is under root-only `/run/libreecho` (0700), so the `olive` SSH
  account cannot list it; `find` returned nothing for that reason, not because
  the files are absent. The stock model is also embedded in the signed wakeword
  feature payload / OTA, so it is restored on boot.
- No staged models exist: `/data/libreecho/wakeword-models/` is absent and there
  is no `/data/libreecho/config/wake-model` file. Stock model is in use.

### B3. Accepted model formats (from the project's own training guide)
A staged model folder must contain exactly three files:
| File | Contract |
|---|---|
| `melspectrogram.onnx` | input `input` float [batch, samples], output `output`; raw int16 as float, scaled x/10+2 |
| `embedding_model.onnx` | input `input_1` [1,76,32,1], output `conv2d_19` → 96 features |
| `alexa_v0.1.onnx` | classifier: input `onnx::Flatten_0` [1,16,96], output `13` [1,1], **opset 13**, Sigmoid final, operators limited to `Flatten`, `Gemm`, `Relu`, `Sigmoid` |

The classifier filename `alexa_v0.1.onnx` is mandatory regardless of the word.
`ReduceMean`/`Sqrt` (e.g. LayerNorm) are known to fail on the signed detector.
Pinned SHA-256 values exist for the two feature models and the stock classifier.
No external weight data is allowed. Host scoring uses a fresh stream and zeroes
the first five blocks.

### B4. Custom upload support
- **On 0.14.5: NO.** `GET /api/v1/wake-word/models` returns 404 today. The
  control-centre upload flow and the chunked upload/commit/select/delete API are
  documented for 0.14.6+ (`GET /api/v1/wake-word/models`,
  `PUT /api/v1/wake-word/model`, `POST /api/v1/wake-word/models/{name}/files/{file}`,
  `.../commit`, `DELETE ...`). Limits: 3 files, 8 MiB per file, 8192-byte chunks,
  base64; commit validates names/sizes, graph I/O names, operators, opset 13 and
  Sigmoid; shapes are not checked and a bad load falls back to stock.
- `set_word` on the bundled `waked` accepts only `Alexa`/`alexa`. LibreEcho,
  Computer, Echo and "Custom model" are **not** installed choices. Changing the
  saved `wake_word` string does not change the model.
- The experimental **second wake word** (0.14.6, off by default) has no control
  centre setting and is not available on 0.14.5.

### B5. Can a new model be trained/installed without replacing the working one?
**Yes, in principle, but not on 0.14.5 via the UI/API.** The supported pattern is:
stage a folder under `/data/libreecho/wakeword-models/<name>/`, write the name to
`/data/libreecho/config/wake-model`, restart `libreecho-waked`; the stock model
remains installed and is the automatic fallback. This is a filesystem-level
operation here (SSH as root or a maintenance route), with a backup first.
Consequences to accept:
- The device still displays/reports the wake word as "Alexa" for stock; a staged
  model reports its folder name once loaded.
- The project explicitly says **on-device load/detection of a newly trained
  word has not been verified**. Treat it as an experiment.
- Training data (positive clips of "Olive"/"Hey Olive"/"Hello Olive" plus
  negative clips including music and TV) must be supplied by you; LibreEcho does
  not train models.
- Staged models are excluded from configuration backups — keep your own copy.
- Do not overwrite `alexa_v0.1` or the feature models.

### B6. openWakeWord as an alternative
Upstream `dscripka/openWakeWord` is **Apache-2.0** and is the same family the
device detector comes from. It is a training + scoring toolkit, not a drop-in
replacement detector on this hardware: LibreEcho always runs its **signed**
detector binary and only allows the model files to change. A PC-side openWakeWord
listener would need a PC microphone and would not change the Echo's wake word;
it is not recommended. The practical route is: train with openWakeWord tooling,
host-score with LibreEcho's `tools/wakeword/wakeword_model.py`, then stage the
three-file folder — after upgrading to 0.14.6.

### B7. Answer to "which of Olive / Hey Olive / Hello Olive?"
None can be selected by configuration on 0.14.5. All three require a custom
classifier. "Olive" and "Hey Olive" are realistic single/short phrases; "Hello
Olive" is longer and generally harder for a small classifier at distance.
Recommendation: if you proceed, prototype **"Hey Olive"** first and keep Alexa
as the fallback until hardware acceptance tests pass.

## Part C — Phase 3: audio management (findings carried from the 23 Nov diagnosis)

- Threshold is already permissive (0.498 vs 0.63 default); raising sensitivity
  further risks music-triggered wakes.
- Measured wake score drops from median **0.998** (music off) to **0.757**
  (music on); several playback events sit at 0.505–0.536, just above the gate.
- Beam selection is unreliable in a diffuse music field (`selected_deg` often
  opposite `doa_deg`, contrast 0.8–3.5 dB).
- AEC is applied in `waked` **after** `micd` beamforming; the 0.14.6 release
  notes fix "wake-word echo canceller resets when music starts or reference
  stream has gaps" (#519/#526) — absent on 0.14.5. This is the leading cause.
- **Important correction:** 0.14.6's experimental `noise_suppression` runs on
  speech sent to the assistant, and the documentation explicitly says *"the wake
  word keeps hearing the unsuppressed audio"*. It will help ASR, **not** wake
  detection. Do not count it as the wake fix.
- Self-trigger test passed: TTS "Alexa" produced no wake events (349 → 349).
- No clipping at volume 30 (beam peaks −64 to −42 dBFS).
- Gain/sensitivity only scale signal and noise; they cannot improve SNR.

## Part D — Phase 4: baseline & test plan (no live changes)

Baseline already recorded: the score table above, beam snapshots, self-trigger
result, and current gain/sensitivity/volume. Not possible for me to perform:
controlled spoken volume/distance tests (I cannot speak to the device). Required
test matrix once you approve a change:

| Condition | Metric | Pass criterion |
|---|---|---|
| Quiet room, 1/3/5 m, normal voice | wake success, score, latency | 3/3 wakes per distance |
| Music low / medium / high, 1/3/5 m | wake success, score, latency | normal voice works at 3 m |
| Music only, 10 min idle | false activations | 0 |
| Device TTS says "Alexa" | self-trigger | 0 |
| Soft voice, music on | wake success | recorded, not regression vs baseline |

Instrumentation: `GET /wake-word`, `/microphones/beams`,
`/var/log/libreecho-waked.log`, and (after 0.14.6) wake near-miss clips.

## Part E — Implementation plan (ranked, approval gates)

1. **Upgrade speaker to 0.14.6** (signed OTA, A/B rollback) — fixes the AEC
   reset bug, adds model upload API, near-miss clips, mic mixing. No other
   changes until this is verified with the baseline matrix.
2. **Re-baseline** wake scores with music on/off; compare to Part D.
3. **Only then** consider a custom "Hey Olive" model: train + host-score first,
   stage alongside stock, never delete the stock model, keep rollback.
4. **IntentEditor `intentReload`** and the new intents (Phase 1) — implement on
   the PC side only, with validation + preserve-on-failure + duplicate checks.
   Register only handlers that actually do something functional.
5. **Consistency fixes** (identity, TTS centralisation, reminders, clock,
   capability registry, request tracing) — apply after the audit findings are
   reviewed, with a regression test plan.

Rollback for each step: OTA slot rollback for firmware; `.env` / script backups
and `custom_intents.json` backups for hub changes; stock wake model always
remains selectable.

## Part F — Architecture & services audit (completed)

### F1. Service map (live)
| Port | Process | Role | Watchdog? |
|---|---|---|---|
| 8765 + 10300 | `olive_hub.py` | Hub HTTP + Wyoming ASR proxy | yes (8765) |
| 10301 | `whisper_wyoming_server.py` (tiny) | ASR | yes |
| 10200 | TTS language router | voice routing, personas | yes (10200 only) |
| 10201 / 10202 | Piper EN `en_GB-southern_english_female-low` / IT `it_IT-serena-medium` | TTS backends | indirect via 10200 |
| 10222 | Kokoro in WSL (`100.113.130.123`) | optional personas | **no** |
| 3014 | WhatsApp bridge (session4) | messaging | yes (port only) — currently wedged, `status=starting`, stale Chrome lock |
| 8767 | `dashboard.py` | control UI, 18 routes | yes |
| 8095 | Music Assistant (Docker) | music | indirect probe |
| 8010 | tuya-local-key helper | IoT keys | no |
| 8766 | stale FastAPI `run.py serve` from older tree | **not the live voice path** | no |
| 3011–3013 | stale WhatsApp node daemons | superseded | no |

Supervision: Scheduled task `Olive Hub` (S4U, boot +30s and every 5 min) runs
`ensure-hub.ps1`, which only checks **port liveness** (10301, 10200, 8767, 3014,
8765). It does not check Kokoro 10222, Piper individually, MA, LLM health, or
functional correctness.

### F2. No central capability registry exists today
Source of truth is fragmented across `Intent` (name/patterns/handler/examples),
`custom_intents.json`, `/health` flags, `Context` module wiring, `.env`,
`voice_personas.json` and docs. `last_test` and `error_state` fields do not
exist anywhere. The proposed registry schema and grounded rows are in the audit
output; recommended fields: intent name, description, handler, patterns,
examples, enabled, availability (state/health keys/probe), dependencies,
permissions (auth/confirmation/side effects), last test, error state.

### F3. Request lifecycle logging is incomplete
Existing: aiohttp access log, `intent=… text=…`, ASR `transcript: …`, chat
lines, TTS `Synthesize` logs, watchdog lines.
Missing: **no request/correlation ID anywhere**; no per-request duration or
outcome; no shared ID across ASR → chat → intent → LLM → TTS; no audit trail for
dashboard mutations (`.env` writes, voice downloads, intent edits, restarts).
Privacy note: full transcripts and spoken text are currently written to logs.

### F4. Stale/competing artefacts and unsupported doc claims
- Many retired hub generations (`olive_hub2/3*.py`, `echo_pipe*.py`, `New_pipe*.py`)
  — only `olive_hub.py` is live, but they create edit ambiguity.
- Four WhatsApp daemons on 3011–3014; active one wedged and protected; watchdog
  cannot restart it because the port is still listening.
- Stale FastAPI process on 8766, while README still says the voice path is 8766
  (actual is 8765) and documents the wrong API key (`OLIVE_LIBREECHO_API_KEY`
  vs `HUB_TOKEN`).
- Docs disagree on WhatsApp port (3011/3012 vs live 3014), Piper EN voice,
  intent count (72 vs 74), wake sensitivity (67 vs 80), firmware model claims,
  and calendar/memory state.
- `docker-compose.yaml` defines containers that do not exist; live MA was
  created outside compose.
- Workspace `tests/` cover the FastAPI app, not the production hub; hub tests
  live in temp scripts. This is a real regression risk for Phase 1.

### F5. Ranked integration risks (from audit)
1. **High** — LLM degradation live now ("couldn't reach my reasoning core").
2. **High** — Whisper timeouts (30 s) vs hub `ASR_TIMEOUT=15`; CPU contention.
3. **High** — WhatsApp bridge wedged + four competing daemons.
4. **High** — Port-only liveness; Kokoro 10222 down with no supervisor.
5. **Medium-High** — Auth/config drift (three different tokens/passwords in docs).
6. **Medium-High** — No request correlation or dashboard audit trail.
7. **Medium** — Stale docs as source of truth.
8. **Medium** — Stale daemons/overlapping scripts.
9. **Medium** — Workspace tests do not cover the production hub.
10. **Medium** — Speaker 0.14.5 AEC bug; 0.14.6 OTA unverified; Tuya cloud blocked.

## Part G — Intent editor & routing audit (completed)

### G1. Current registry
- **74 built-in intents**, **0 active custom intents** (`custom_intents.json` has
  one disabled example). No duplicate names in `INTENTS`.
- `stop` is handled by a pre-router check, so it is absent from `/health` and the
  dashboard Intents card.
- All critical existing intents are present and functional:
  `reminder.set/list/cancel`, `alarm.set/list/cancel`, `timer.set`, `time`,
  `date`, `weather`, `knowledge`, `help`, `voice.clone`, `voice.use`.
- `announce` is **not** an intent; `LibreEcho.announce()` is an internal TTS
  transport only.
- Voice cloning is only partial: personas persist but `ready:false` and the
  OmniVoice backend is unavailable; `speak like <name>` falls back to normal voice.

### G2. Hot-reload / `intentReload`
- **No `intentReload` exists in code.** The dashboard "Reload" button only
  re-GETs `/api/intents`; it does not re-read `custom_intents.json` or invoke
  `reload_custom_intents()`. Today only `POST /intents` truly reloads, and it
  **replaces** the file with posted content.
- Validation in `apply_intents_config()` covers: dict shape, name pattern,
  non-empty patterns, regex compilation. It does **not** validate `version`,
  `speak`, duplicate names, collisions with built-ins, or regex cost.
- Preserve-on-failure is **incomplete**:
  - validation errors → old state restored ✔
  - file-write failure → old state restored ✔ (temp file leaks)
  - **missing file** → silently sets empty config, wiping customs and disables ✘
  - an unexpected exception (e.g. `"intents": 123`) can leave
    `DISABLED_INTENTS` replaced but `CUSTOM_INTENTS` old ✘
- Custom intents route before built-ins; disabling a name shared with a built-in
  mutes both. No file watcher exists.

### G3. Phase-1 intent matrix
**None of the 33+ requested Phase-1 names are registered today.** Nearest
existing capabilities:
| Requested | Nearest today |
|---|---|
| wakeword.* | dashboard speaker card / `/api/speaker` only |
| microphone.*, audio.diagnostics | none |
| music.duck | internal duck side-effect when mic opens |
| music.shuffle/repeat/search/favourite | `music.playlist`, `music.mood` adjacent |
| volume.mute/unmute/relative | `volume.set` handles relative; TV mute exists; speaker mute unregistered |
| announce | internal announce transport only |
| broadcast/intercom | none |
| note.* | none (`facts.*`/shopping are different stores) |
| alarm.snooze | unmatched phrase |
| system.* | `/health` HTTP only |
| light.toggle/color/scene | `ha.on/off`, `light.brightness`, `ha.scene` partially |
| plug.on/off | swallowed by generic `ha.on/off` |
| routine.goodnight/goodmorning | `routine.sunset` only |

### G4. Routing conflicts
- `radio` precedes `music.mood` → "play some music" becomes a clarifying question.
- `alarm.list` precedes `alarm.set`; alarm/reminder share handlers and re-derive
  kind from words in the utterance, not the matched intent.
- `ha.on/off` have catch-all target patterns that pre-empt future `plug.*` and
  `light.toggle` intents.
- Several handlers emit unregistered reply labels (`music.play`, `iot.toggle`,
  `ha.toggle`, `home.toggle`, …), so `/health` and logs do not reflect replies.

### G5. Required fixes before Phase 1
1. Implement `intentReload` as a hub-side endpoint that snapshots state,
   re-reads the file, validates, checks duplicates/collisions, and rolls back on
   **all** failures including a missing file.
2. Fix `intents_post` exception safety (`try/finally`) and clean temp files.
3. Add duplicate/collision validation and `speak`/`version` validation.
4. Replace the misleading UI "Reload" button with a real reload call and refresh
   both the editor and the Intents card.
5. Register Phase-1 intents only with working handlers; use narrower patterns
   for `plug.*`/`light.*` and add `alarm.snooze` before generic fallbacks.

## Part H — Voice / TTS consistency audit (completed)

### H1. Voice routing map (live)
```
Speaker (mode=custom, Wyoming TTS → 192.168.1.86:10200)
  ← hub Speech.say → device /audio/announce
  ← router 10200: persona? (current "this is ricky", ready=false → ignored)
        ├─ Italian detected → Piper IT 10202 (it_IT-serena-medium)
        └─ otherwise       → Piper EN 10201 (en_GB-southern_english_female-low)
     optional Kokoro 100.113.130.123:10222 (currently unreachable, no fallback)
     dormant native device voice: northern-male (only in pipeline mode=local)
```

### H2. Findings
- There is **one** hub speech path (`Speech.say` → `/audio/announce`). Reminders,
  time, weather, errors and LLM replies all use it. `Reply.speak` flags are set
  but **never read** anywhere — dead configuration.
- The voice changes **per utterance** because the Wyoming router selects EN vs IT
  from detected language (`wyoming_tts_lang_router.py:37-54,126`). Italian place
  names or accented characters in an English reply (e.g. "San Vincenzo", "oggi è")
  can flip the voice mid-conversation. The hub's LLM prompt also explicitly asks
  for Italian replies when Italian is detected.
- The persona registry is re-read on **every** `Synthesize`. An active persona
  with `ready=false` is silently ignored; making a `KokoroEN`/`KokoroIT` persona
  active switches **all** speech to Kokoro, and because Kokoro's port is
  unreachable and there is no fallback, the result is **silence**, not a
  different voice. Note a port mismatch: starter uses **10222**, router class
  default is **10204**, docs say 10204.
- The dashboard voice picker rewrites `$enVoice`/`$itVoice` in
  `start-tts-detached.ps1` and kills/relaunches ports 10200-10202. It reports the
  **script** value, not the live router — manual edits show a voice that is not
  actually in use. Script header comments and router defaults still say
  `en_GB-alan-medium`, while the live value is `en_GB-southern_english_female-low`.
- **Three independent voice knobs** exist: device `/audio.tts_voice`
  (`northern-male`, dormant in `custom` mode), device
  `voice-pipeline.tts.voice` (`en_GB-alan-medium`, ignored by the router), and
  hub `$enVoice/$itVoice` + personas. Configuration scripts disagree:
  `switch-olive-endpoint.ps1` sets `custom`, `Configure-LibreEcho-Olive.ps1`
  sets `local` — switching them flips female Piper ↔ male native voice.
- Ducking changes volume only, not voice; but its shared `_pending` state means
  a second announcement can skip ducking.
- **Identity drift:** at least six different system prompts/personalities exist
  across hub, device, config scripts and retired apps. Device prompt is English
  only; hub supports Italian. Wake word is Alexa while persona is Olive, and
  router logs show the LLM answering "I'm not Alexa, I'm Olive" / "I'm Olive, not
  Alexa" — which then changes reply language and perceived voice.
- Capability drift: hub advertises reminders while LLM replies "I can't set
  reminders" (see Part I).

### H3. Ranked causes of "voice sometimes changes"
1. Per-utterance language routing (EN female ↔ IT female).
2. Persona registry + voice commands (silent fallback; global Kokoro switch with
   no fallback when the backend is down).
3. Dashboard voice picker / startup-script mutation (stale displayed value).
4. Device pipeline mode conflict (`local` vs `custom` → native male vs Piper).
5. Multiple prompts/wake-word identity divergence.
6. Backend failure with no fallback (silence perceived as voice change).

### H4. Required fixes
1. Add structured logging of resolved `{lang, voice, persona, source}` per
   synthesis and expose "active voice" from the **live router**, not the script.
2. Decide one identity and one default voice; make all prompts/config scripts
   agree ("I am Olive; Alexa is the wake word").
3. Make language routing deterministic for the assistant persona, or pin a
   single voice unless the user explicitly requests a language switch.
4. Add a real fallback when a persona backend is unreachable: log it and use the
   default Piper voice instead of silence.
5. Reconcile the Kokoro port (10222 vs 10204) and remove stale script defaults.

## Part I — Reminders / clock / AI audit (completed)

### I1. Reminder subsystem verdict
- `reminder.set`, `reminder.list`, `reminder.cancel` **are registered and work**
  (`h_reminder_set` 3031-3063, shared `h_schedule_list/cancel`). Persistence is
  `olive_schedule.json`, not a database. Scheduler is an in-process 1 s loop.
- **No source code anywhere says reminders are unavailable.** The "I can't set
  reminders" reply is generated by the **LLM**, because the request never
  matched the intent.
- Root causes of inconsistent reminder behaviour:
  1. `reminder.set` only matches `remind me` / `ricordami`. "Set a reminder to
     call mum at 8" **does not match** and instead hits `calendar.add`, which can
     answer "There's no calendar connected yet…" or fall through to the LLM.
  2. The prompt forbids claiming unperformed actions, so the LLM honestly says
     it cannot set reminders — reinforcing the false impression.
  3. **Recurring reminders are not supported at all.** `ScheduleItem` has no
     repeat field. "Remind me every morning at 8" is silently stored as a
     **one-shot** labelled "every morning" and the assistant says it is set.
  4. Items due while the hub is down and >60 s late are dropped at startup.
  5. Reminders are delivered via hub → speaker `announce`; alarms additionally
     use the device's own native timer as the real ringer.

### I2. Clock verdict
- `time` and `date` use **local `dt.datetime.now()`** (`2168-2194`) and local
  intents run **before** the LLM. No external clock API exists. Weather's
  Open-Meteo `timezone=auto` is forecast data, not the current time.
- **The hub has no timezone handling at all** — no `Europe/Rome` in code or
  `.env`; naive `now()`/`.timestamp()` throughout. Correctness depends on the
  Windows host timezone. Other (non-live) surfaces hardcode `Europe/Rome`.
- The exact string "I can't reach a real-world clock" **does not exist in the
  repository** — it is an LLM-generated claim, most likely from the retired
  FastAPI web path whose time support is only a regex quick-reply.

### I3. AI model / fallback verdict
- Current: primary `qwen/qwen3.8-27b`, fallback `openai/gpt-oss-20b`,
  `LLM_MAX_TOKENS=180`, Groq endpoint. The hub ignores `.env` `GROQ_MODEL` and
  `OLLAMA_MODEL`; it uses `LLM_MODEL`.
- Both models receive the **same** system prompt, memory block, history and
  payload — only the `model` field changes. No tool definitions are sent.
- **Fallback is weak:** it only runs on HTTP 404/400 or empty content. Network
  exceptions and 5xx return `None` immediately — so the most common failures
  skip the fallback and produce "Sorry, I couldn't reach my reasoning core
  just now." (the live symptom found by the services audit).
- `LLM_MAX_TOKENS=180` is tight for a model that may reason, increasing empty
  answers and fallback churn.
- Dead config: `CONVERSATION_TURNS`/`conversation_ttl` are parsed and reported
  by `/health` but never applied to `Conversation` (fixed 10 turns).
- Three different system prompts exist across hub, device and retired web apps;
  two different `/api/capabilities` endpoints disagree about Olive's abilities.

### I4. Central capability registry
- None in `olive_hub.py`; intent registry + hard-coded help string only.
- The retired web apps have two partial capability endpoints that omit
  reminders. `iot.py:68` is a device registry, not a capability registry.

---

## Part J — Consolidated root causes (across all audits)

| # | Root cause | Evidence | Impact |
|---|---|---|---|
| 1 | 0.14.5 AEC reset bug; music contaminates post-beam audio | score 0.998→0.757; 0.14.6 notes #519/#526 | must shout over music |
| 2 | LLM fallback skips network/5xx failures; 180-token cap | `olive_hub.py:4961,4973-4975` | "can't reach reasoning core" → "can't do X" |
| 3 | Narrow reminder regex + competing `calendar.add`; no recurrence | `:3959`, `:4162`, `ScheduleItem` | inconsistent reminders |
| 4 | Per-utterance TTS language routing + persona registry | router `:37-54,72-82,126` | voice changes mid-conversation |
| 5 | No request correlation ID / capability registry / audit trail | all services | cannot trace or report errors accurately |
| 6 | Phase-1 intents and `intentReload` do not exist; reload rollback gaps | intent audit | requested expansion is not implemented |
| 7 | Three identities/prompts; Alexa wake word vs Olive persona | voice audit | identity answers vary |
| 8 | Port-only watchdog; Kokoro/WhatsApp unmonitored; stale daemons/docs | services audit | silent degradations |
| 9 | No timezone in hub; naive datetimes | clock audit | reminder times depend on host TZ |
| 10 | No physical wake-word acceptance tests | Part D | cannot prove hands-free goal |

## Part K — Ranked implementation plan (approval required for each step)

**Step 0 — Baseline & approval gate (done for diagnostics; physical tests pending).**
Keep current gain 78 / sensitivity 80 / volume 25 until the 0.14.6 OTA is
verified. Run the Part D matrix with you in the room.

**Step 1 — Speaker OTA to LibreEcho 0.14.6.** Signed, A/B rollback. Fixes the
AEC reset bug and enables model upload/near-miss clips. Highest impact on the
primary complaint. No hub change.

**Step 2 — Hub reliability (small, isolated patches):**
- make LLM fallback run on **all** failures, not just 404/400/empty;
- apply `CONVERSATION_TURNS`/TTL or remove the dead settings;
- widen `reminder.set` patterns ("set/create/add a reminder…", "remind me…");
- add real recurrence support (repeat field + next-occurrence calculation) or
  explicitly refuse recurring requests instead of silently making one-shots;
- add `Europe/Rome` timezone handling to all scheduling and clock calls;
- add `alarm.snooze` and the Phase-1 deterministic intents with **real**
  handlers only (dashboard/API-backed where possible), with narrower patterns
  than `ha.on/off` so `plug.*`/`light.*` are not swallowed.

**Step 3 — Intent editor `intentReload`:** snapshot → re-read file → validate
(including duplicates, `speak`, `version`) → apply atomically → return
`{errors, active, disabled}`; roll back on **all** failures including missing
file; replace the misleading UI Reload button; add a file watcher only if you
want automatic reload.

**Step 4 — One Olive:** consolidate to one system prompt and one identity
statement ("I am Olive; Alexa is the wake word"), remove/retire the competing
prompts and capability endpoints, generate the help text from the capability
registry so it cannot claim unavailable features.

**Step 5 — One voice:** log resolved `{lang, voice, persona, source}` per
synthesis; expose the **live** voice; add fallback when a persona backend is
unavailable; reconcile Kokoro 10222 vs 10204; stop config scripts from flipping
device pipeline mode; pin one default voice unless a language switch is asked.

**Step 6 — Observability:** add a request ID propagated from ASR → chat →
intent → LLM → TTS; structured per-stage timing/outcome; dashboard mutation
audit log; capability registry with availability, dependencies, permissions,
last test and error state; stop logging full transcripts/spoken text by default.

**Step 7 — Housekeeping:** remove stale hub generations and competing daemons
after verifying nothing depends on them; fix stale docs (ports, token names,
voices, intent counts, firmware claims); add production-hub regression tests.

**Step 8 — Custom wake word (only if you still want it after Step 1):** train
and host-score "Hey Olive" with openWakeWord tooling, stage the three ONNX files
alongside stock, test on-device, keep Alexa as fallback until acceptance passes.

### Rollback plan
- Firmware: A/B slot rollback via the speaker's control centre.
- Hub: `.env`, scripts and `custom_intents.json` backups already made before
  each write; keep a known-good copy of `olive_hub.py`/`dashboard.py`.
- Wake model: stock `alexa_v0.1` remains installed; removing `wake-model`
  restores it.
- No database deletion, no paid services, no firmware changes beyond the signed
  0.14.6 OTA, and no live deployment without your explicit approval.

## Part L — Acceptance tests (after approval)
As listed in the original request: identity, capabilities, clock/date, reminders
(all five forms), alarm, timer, music, general question, model fallback,
consecutive commands, spoken notification — plus the wake-word matrix in Part D
and the false-activation/self-trigger checks.

---

## Part M — Authorised hub-side implementation (completed 24 Nov 2026)

After the report was delivered, the user authorised hub-side fixes. **Firmware,
wake model and device configuration were not changed.** Backups were made first
(`olive_hub.py.bak-20261124-012834`, `custom_intents.json.bak-...`).

### M1. Reliability fixes applied to `olive_hub.py`
- **LLM fallback now runs on every failure** (any non-200, network exception or
  timeout), not only 404/400/empty. Final failure is logged explicitly.
- **`CONVERSATION_TURNS` / `CONVERSATION_TTL` are now applied** to the live
  `Conversation` object instead of being dead settings.
- **Timezone:** `LOCAL_TZ` (`OLIVE_TIMEZONE`, default `Europe/Rome`, with
  host fallback) is used for clock targets, reminders, alarms, calendar parsing
  and snooze. All generated targets are timezone-aware.
- **Reminder patterns widened** to include `set/create/add/make a reminder…`,
  `promemoria`, and `remember to…`.
- **Recurring reminders implemented:** `ScheduleItem` now has
  `repeat/repeat_hour/repeat_minute`, persisted in `olive_schedule.json`; the
  scheduler reschedules recurring items instead of firing once; `_parse_recurrence`
  handles every morning/evening/day/weekday/weekend/week and keeps the actual
  task text. The spoken confirmation says ` (daily)`, ` (weekdays)`, etc.
- **Snooze:** `alarm.snooze` reschedules the next alarm 9 minutes later.

### M2. Phase-1 intents (functional handlers only)
**24 new intents registered — 74 → 98 total, no duplicate names.** Registered:
`wakeword.status/set/list/test/sensitivity`, `microphone.status/test`,
`audio.diagnostics`, `music.duck`, `volume.mute/unmute/relative`, `announce`,
`note.add/list/read`, `alarm.snooze`, `system.status/diagnostics`,
`light.toggle`, `plug.on/off`, `routine.goodnight/goodmorning`.
All use real backends (device HTTP API, memory store, scheduler, local IoT).
**Deliberately not registered** (no real backend): `music.shuffle/repeat/search/
favourite`, `broadcast`, `intercom`, `light.color`, `light.scene` — registering
them would mean placeholder handlers, which the requirement forbids.

### M3. `intentReload` implemented
- Hub: `POST /intents/reload` snapshots state, re-reads `custom_intents.json`,
  validates, applies atomically and rolls back on **all** failures including a
  missing file.
- Validation added for duplicate names, collisions with built-ins, `speak`
  mode, `version` and `intents` type.
- `intents_post` now guards validation exceptions and cleans temp files.
- Dashboard: real `Reload` button calls `/api/intents/reload` and refreshes both
  editor and Intents card.

### M4. Deliverables created
- `UPGRADE-0.14.6-CHECKLIST.md` — pre-flight, baseline, manual OTA steps,
  post-upgrade checks, rollback and acceptance criteria.
- `tools/wake/ab_wake_test.py` — read-only A/B script with explicit
  `--approve-baseline`, `--approve-post`, `--approve-false MINUTES` gates;
  refuses to run without them, records device state and wake-log score stats to
  `wake-ab-results.json`. Physical voice/distance testing still requires you.

### M5. Verification
- `py_compile` clean; hub restarted; `/health` reports **98 intents**, `ok=true`.
- Live `/command` tests passed for wakeword.status, microphone.status,
  audio.diagnostics, system.status, note.add, note.list, routine.goodnight;
  dashboard `intentReload` returned `ok=true`.
- IntentReload unit checks passed for valid config, duplicate names, missing
  file and malformed `intents` type — old state preserved in each failure case.
- Side effects of the goodnight test (kitchen light, volume, test note) were
  restored. Dashboard shows 9/9 services after restarting Kokoro.
- No firmware, model, device-gain or sensitivity changes were made.

### M6. Remaining user actions
1. Run the baseline A/B script with your physical voice/distance tests.
2. Perform the 0.14.6 OTA from the control centre after backing up.
3. Run post-upgrade A/B and compare. Only then consider a custom wake model.
4. Approve Steps 4–7 from Part K (identity, voice, observability, housekeeping).

---
*Hub fixes implemented and verified. Firmware/model/device configuration remain
unchanged pending user action and approval.*
