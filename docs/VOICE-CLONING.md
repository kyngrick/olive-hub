# Voice personas, Kokoro and cloning

## ASR robustness for the wake phrase

Whisper `tiny` sometimes hears *"Alexa clone"* as *"Alexa cold"*, *"Alex
Lacool"* or *"Alex recalled"*. Two fixes are in place:

- the Whisper server passes an `initial_prompt` containing Olive's command
  vocabulary (`clone`, `timer`, `weather`, `kitchen light`, `Giulia`, etc.);
- `correct_clone_phrase()` in `olive_hub.py` maps short misheard wake phrases back
  to `alexa clone`, and explicit aliases cover `cold`, `lacool`, `recalled`,
  `clown`, `cone`. Ordinary phrases like *"it's cold outside"* are untouched.

Verified via `/command`: all mishearings arm clone mode; the weather phrase does not.

## Current status / troubleshooting (15:50)

The speaker briefly stopped speaking after the router was moved to port 10205:
its TTS child exited with code 1 because **Windows Firewall blocks inbound
10205** (no allow rule; adding one requires admin). The speaker was restored to
the known-good Wyoming TTS endpoint **tcp://192.168.1.86:10200** with voice
`en_GB-alan-medium`, and speech is verified working again (`result=0`).

`TTS_ROUTER_PORT` is set back to **10200** in `.env`, and the watchdog checks
10200. The persona-capable router was launched by hand on 10205 to prove the
override logic (an English request was routed to `it_IT-serena-medium`), but
that port is not reachable from the speaker. After the next PC reboot, the
watchdog will start the **persona-capable router on 10200**, so `speak like
<name>` will then work automatically. Alternatively, an elevated PowerShell can
add a firewall rule for 10205 and the speaker can be pointed back there with
`switch-olive-endpoint.ps1 -Endpoint 192.168.1.86 -TtsPort 10205`.

`clone` cannot create a real cloned voice yet: OmniVoice needs ~4 GB of models
and this PC has ~0.9 GB free RAM. The capture/name/save flow works; the saved
persona stays `ready: false` and the normal voice is used until that backend is
available on a bigger machine.

## Short answer

- **Kokoro is not a voice-cloning model.** It is a fast multilingual TTS (82M
  parameters) with a fixed set of voices, including Italian `if_sara` and
  `im_nicola`, and you can blend voices to design a new one. It cannot copy your
  voice from a short recording.
- **OmniVoice is the cloning backend** already supported by the installed
  `wyoming-piper` 2.5.3. It clones from a 3–10 s reference (`ref.wav` + `ref.txt`).
  Its models are about **4 GB**; this PC currently has **~0.9 GB free RAM**, so
  it cannot run here. It needs a machine with more memory (or more RAM fitted).
- What **is** built here: the complete capture → name → save → select framework,
  with a pluggable backend, so cloning starts working as soon as an OmniVoice
  backend is available.

## What was built

### Capture flow

1. Say **"Alexa clone"** (or "clone my voice").
   Hub replies: *"Say a short sentence now, then I'll ask who it is."*
   The microphone stays open (follow-up window).
2. The next thing you say is captured in the hub's Wyoming ASR proxy as raw PCM
   and saved to `voice_refs/capture.wav`. Whisper transcribes it.
3. The hub asks **"Who is this?"**.
4. Answer with a name, e.g. **"Ricky"**. The hub saves:
   - `voice_refs/ricky.wav` — the reference recording
   - `voice_refs/ricky.txt` — the transcript
   - `voice_personas.json` — a persona with provider `omnivoice`, `ready=false`
   and sets it active.
   It honestly says the clone backend is not available here yet.

### Use flow

- **"Alexa speak like Ricky"** / **"talk like Ricky"** → selects that persona.
  The TTS router reads `voice_personas.json` on every request and overrides the
  voice. If the persona is not `ready`, the router keeps the normal voice and
  the reply explains why.
- **"Alexa use my normal voice"** → clears the active persona.
- **"Alexa list voices"** → lists saved personas and marks the active one.

Verified: with a temporary `ready` Piper persona, an English synthesis request
was routed to `it_IT-serena-medium`:
```
('127.0.0.1', 52088): it -> it_IT-serena-medium: 'Hello, this is a router test.'
```
The test persona was removed; there are no saved voices yet. **Run the clone
flow yourself to create your own.**

## Backends

| Provider | Port | Status here | Notes |
|---|---|---|---|
| `piper` | 10201 / 10202 | Working | Real installed voices; can be used as personas |
| `kokoro` | 10204 | Optional, not installed | Fixed voices/blends, not cloning; ~330 MB model |
| `omnivoice` | 10203 | Not installed | Real cloning; ~4 GB models, needs more RAM |

## Enabling OmniVoice cloning on a bigger machine

```powershell
cd C:\Users\Geggais and Rick\Documents\Codex\piper-wyoming
.\.venv\Scripts\python.exe -m pip install "wyoming-piper[omnivoice-deps]==2.5.3"
.\.venv\Scripts\python.exe -m pip install --no-deps omnivoice

# Put references where wyoming-piper expects them:
#   data\omnivoice_voices\<language>\<voice>\ref.wav
#   data\omnivoice_voices\<language>\<voice>\ref.txt
# Copy the files saved in olive_assist_v2\voice_refs\<voice>.wav/.txt there.

.\.venv\Scripts\python.exe run_piper_windows.py --backend omnivoice `
  --uri tcp://127.0.0.1:10203 --data-dir data --download-dir data `
  --omnivoice-ref-dir data\omnivoice_voices --omnivoice-language en `
  --omnivoice-steps 10
```

Then set the persona `ready` to `true` in `voice_personas.json` (or add it again
with `ready=True` via `voice_personas.add`). The router will automatically send
that voice's speech to port 10203. Use an Italian reference recording if you
want Italian speech to sound right — OmniVoice carries the reference accent
into other languages.

## Enabling Kokoro voice design (no cloning)

```powershell
cd C:\Users\Geggais and Rick\Documents\Codex\piper-wyoming
.\.venv\Scripts\python.exe -m pip install kokoro "misaki[en]" soundfile huggingface-hub
# Download hexgrad/Kokoro-82M / kokoro-v1_0.pth (~330 MB) into data/
# Run a Wyoming TTS wrapper on tcp://127.0.0.1:10204 exposing voices like
# if_sara, im_nicola or a blend "if_sara,im_nicola".
```
Then a persona with `provider: "kokoro"` and `voice: "if_sara"` will be routed
there. A blend is the closest thing to a "new voice" without cloning.

## Files

- `voice_personas.py` — registry helpers (add/set_active/active/list/remove).
- `voice_personas.json` — personas and active name (gitignored).
- `voice_refs/` — captured reference audio/transcripts (gitignored).
- `wyoming_tts_lang_router.py` — reads the registry and overrides the voice.
- `olive_hub.py` — clone/use/list intents and ASR audio capture.

## Privacy

A voice recording is biometric-adjacent data. `voice_refs/` is gitignored; keep
it local, and only clone voices with the speaker's consent.
