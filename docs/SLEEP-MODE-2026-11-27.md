# Sleep mode — 27 Nov 2026

## What it does
One voice command chains a full bedtime routine:

1. **TV off** via the existing TvControl.
2. **Lights dim** to 15% immediately, then **off** after 8 minutes.
3. **Sleep audio at low volume** (speaker volume 5):
   - default: a Sleep-mood playlist from the user's own YouTube Music account,
     with its 12-track autoplay queue (~45 minutes);
   - with "binaural" / "8 Hz": a locally generated 8 Hz binaural-style tone.
4. **Music stops after 45 minutes**, volume restored to 10.
5. **05:13 wake window**: flutes (or "lucid dreaming music" if that phrase is
   used) for 16 minutes, then off.

Durations and wake time can be spoken:
"sleep mode with binaural beats, lights off in 1 minute, music for 1 minute,
flutes at 5:13 for 16 minutes then off".

## Implementation notes
- New `sleep.mode` intent and `h_sleep_mode` handler.
- Scheduler gained generic `music.stop` and `volume:N` actions, so the
  fade-outs survive a hub restart like any other scheduled item.
- `binaural_route` generates a stereo 200 Hz / 208 Hz tone (8 Hz difference),
  encodes it to MP3 with the bundled `imageio-ffmpeg` binary, and serves it at
  `/binaural.mp3?seconds=N`. Generation is fast (~6 s for 45 minutes).
- The Echo's radio player rejects WAV streams (it re-requests continuously);
  MP3 with a known Content-Length plays reliably, so the tone is MP3.
- A true binaural beat requires stereo headphones; a single speaker produces
  an 8 Hz amplitude pulse instead. This is a genuine limitation, not a bug.
- All scheduled items are in gitignored `olive_schedule.json`.

## Verification — live, 27 Nov 2026
- "sleep mode with binaural beats, lights off in 1 minute, music for 1 minute,
  flutes at 5:13 for 16 minutes then off" → TV off, lights dimmed, binaural
  station playing, speaker volume 5.
- Timed deliveries fired: lights off trace `b47882c158c2`, music.stop trace
  `bc7c331752a6`, volume restore trace `fa0fba4ff1c4`; speaker stopped, bulb
  `20=False` and brightness at the dimmed value.
- After switching the stream to MP3, the Echo fetched `/binaural.mp3` once,
  `200` with 481,744 bytes, no retries; `playing: True` 12 s later.

## Current persisted schedules
- Daily 01:55 — kitchen light off (re-armed automatically).
- Today 16:45 — kitchen light on (sunset).
- 05:13 — flute music; 05:29 — stop it.
