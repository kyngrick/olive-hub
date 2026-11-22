# How to test the voice setup yourself

All commands below run from:
```powershell
cd "C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2"
```

## 1. Quick Kokoro TTS test (English + Italian)

This talks to the Kokoro Wyoming server in WSL and writes two WAV files.

```powershell
.\.venv\Scripts\python.exe tools\kokoro\test_kokoro.py
```

Expected output (timings vary):
```
en/af_bella: first=13s total=13s bytes=175104 -> ...\voice_refs\kokoro_en.wav
it/im_nicola: first=8s total=8s bytes=149504 -> ...\voice_refs\kokoro_it.wav
```

The Italian sample is also opened in the Review pane. Open the WAVs manually with:
```powershell
Start-Process ".\voice_refs\kokoro_it.wav"
```

## 2. Clone your own voice

Records 12 seconds from the Windows microphone, then runs KokoClone in WSL.

```powershell
.\.venv\Scripts\python.exe tools\kokoro\record_and_clone.py --name Ricky
```

Options:
```powershell
# use an existing recording instead of the microphone
.\.venv\Scripts\python.exe tools\kokoro\record_and_clone.py --ref ".\voice_refs\ricky.wav" --name Ricky
# Italian sentence
.\.venv\Scripts\python.exe tools\kokoro\record_and_clone.py --name Ricky --lang it --text "Buongiorno, sono Olive."
```

Recording tips: quiet room, 15–30 cm from the mic, natural voice, no TV/music,
single speaker. Longer/cleaner reference = better clone.

**Timing:** expect **3–6 minutes** on this machine (Kanade model load + CPU
conversion). The script opens the result in the default audio player when done.
If it stalls, close other heavy apps first — the clone needs a lot of free RAM.

Output: `voice_refs\<name>_cloned.wav`.

## 3. Test clone mode through the speaker

1. `Alexa clone` (aliases: `activate clone mode`, `Alexa cold` — ASR correction handles mishearings)
2. Wait for: *"Clone mode armed. Say a short sentence…"*
3. Say a 3–10 second sample.
4. Wait for **"Who is this?"**, answer e.g. `Ricky`.
5. `Alexa speak like Ricky`

The reference is saved in `voice_refs/` and a persona is created. **Live cloned
speech still needs a fast backend** (Pocket TTS / ZONOS3-Mini); right now the hub
honestly keeps the normal voice because this PC lacks the RAM. The saved
`ricky` persona is preserved.

## 4. Use Kokoro as the live voice (after next reboot)

Two ready personas are registered:

```
Alexa speak like KokoroIT      # Italian voice im_nicola
Alexa speak like KokoroEN      # English voice af_bella
Alexa use my normal voice      # back to default
```

The router change that reaches the WSL service takes effect on the next reboot.
Current CPU speed gives first audio in ~8–14 seconds, so it is not yet suitable
for quick conversational replies.

## 5. Checking the service

Windows `netstat` will show **nothing** on 10222 because WSL localhost
forwarding is off — that is normal. Check the WSL IP instead:
```powershell
Test-NetConnection -ComputerName 100.113.130.123 -Port 10222
```

If WSL address changes

The client uses the WSL Tailscale IP in `.env`:
```
KOKORO_HOST=100.113.130.123
```
If it changes after a reboot, check with:
```powershell
wsl -e hostname -I
```
and update `KOKORO_HOST` in `.env`, then restart the hub (`POST /restart`).

## Files created by the tests

| File | Purpose |
|---|---|
| `voice_refs\kokoro_en.wav` | Kokoro English sample |
| `voice_refs\kokoro_it.wav` | Kokoro Italian sample |
| `voice_refs\<name>_ref.wav` | your recorded reference |
| `voice_refs\<name>_cloned.wav` | generated cloned speech |
| `voice_refs\kokoclone_sample.wav` | earlier KokoClone test from the sample WAV |
