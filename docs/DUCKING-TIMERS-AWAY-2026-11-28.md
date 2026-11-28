# Ducking, timer/alarm tones and leaving-home mode — 28 Nov 2026

## 1. Ducking research and fix
The speaker reports `state=idle` while its own radio stream plays, so the old
duck check (`/playback` state == `playing`) almost never fired for music. Both
duck paths now use a new `LibreEcho.is_media_playing()` that checks cast playback
**or** `/integrations/radio.playing`.

- Listening duck: volume × 0.30, clamped 8–28 (was × 0.15, clamped 1–10).
- Speaking duck: volume × 0.40, minimum 8 (was × 0.35, minimum 5).
- `duck_music=False` now disables both paths (previously ignored for listening).
- Auto-restore watchdog remains at 25 s; the normal restore happens after the
  answer as before.

Verified live: radio playing, volume 40 → 12 on mic-open duck → restored to 40.

## 2. Timer / alarm ringing
- `Scheduler._deliver()` now fires the alert callback for `timer` items too,
  passing the kind.
- Two distinct locally generated 30-second tones:
  - `olive-alert.wav` → `/alert-tone.wav` (880/660 Hz chime, alarms/reminders)
  - `olive-timer.wav` → `/timer-tone.wav` (triple 1046 Hz beep, timers)
- `play_alert_tone(kind)` raises the speaker volume before playing (72 for
  timers, 62 for alarms) and schedules a restore 14 s later, only if the user
  has not changed it.
- "stop timer" / "stop the timer" is intercepted by `STOP_COMMAND`, so
  `h_stop_everything()` now cancels scheduled timers and pending device timers
  before the generic stop path. `stop_everything()` also stops native radio
  using `is_media_playing()`, so a ringing tone actually stops.

Verified live: 8-second timer fired, `/timer-tone.wav` fetched by the Echo,
delivery trace ok, radio showed the tone; "stop timer" → "Timer stopped." and
`playing: False`.

## 3. Leaving-home mode
New `leaving.mode` intent:
- "leaving home" / "I'm leaving" / "away mode" → stops YouTube autoplay, party
  and music-sync, stops radio, turns all local lights off and the TV off.
- "I'm back" / "welcome home" / "back home" → lights to full white.

Verified live: "leaving home" → lights off and TV off; "I am back" → lights on.

## Notes
- No new paid service or firmware change. Tones are generated locally and
  served by the hub; the speaker plays them through its native radio API.
- The 30-second tone length gives time to say "stop" while it rings.
