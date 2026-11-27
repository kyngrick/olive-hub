# Lights, scheduled routines, movie/party modes and music alarms — 27 Nov 2026

## What was added
All changes are local and use the existing Tuya LAN bulbs (`iot.py`). No new
cloud service, no paid service, no firmware changes.

### 1. Scheduled routines at arbitrary times
New intent `routine.at` schedules an action for a spoken time and supports
`every day` recurrence (routines were previously sunset-only, one-shot).
`Scheduler` now re-arms recurring `routine` items like alarms/reminders.

Actions understood: `on`, `off`, `brightness:N`, `color:NAME`, `movie`.
Default target is all ready local bulbs; a named device can be given.

### 2. Sunset / 1:55 am
- "turn on the kitchen light at sunset" → scheduled for the next local sunset
  (16:45 on 27 Nov in Rome). "before sunset"/"prima del tramonto" also work.
- "turn off the kitchen light at 1:55 am every day" → daily recurring routine.
- The old "N minutes before sunset" wording still works and now also accepts
  "off"/"spegni".

### 3. Movie mode and white restore
- "movie mode" → all bulbs to warm white at 10% brightness.
- "movie mode off" → all bulbs back to full white.

### 4. Colour and party mode
- `iot.py` gained `set_colour()` using tinytuya's HSV API (0–1 range) and
  a corrected `set_white()` mapping percent → the bulb's 10–1000 scale.
- Colours: red, orange, yellow, green, teal, cyan, blue, purple, violet, pink,
  magenta, white/warm white/cool white, plus Italian names. "hue:NNN" also works.
- "set the kitchen light to blue", "make the lights red", "colore blu".
- "party mode" cycles all bulbs through seven colours every 2.5 s;
  "stop party mode" (or any "stop") ends it and restores white.

### 5. Music at alarm time
- "set an alarm for 7 am with happy music" stores `action=music`,
  `target=happy music` on the alarm.
- At delivery: alert tone → spoken message → YouTube Music mood mix (via the
  user's own logged-in account), with autoplay disabled so it does not take
  over the speaker all day.
- Any mood/genre phrase works (happy, rainy day, love, chill, workout, …).

## Verification — live, 27 Nov 2026
- Colour: kitchen light went `21=colour`, `24` hex changed; restore returned
  `21=white`, `22=1000`, `23=1000` (original values).
- Movie mode dimmed 1 light; movie mode off restored it.
- Party mode started and stopped; bulbs restored to white/full.
- "turn off the kitchen light at 1:55 am every day" fired at 01:55:04,
  bulb `20=False`, delivery trace `a8f5f194ab98`, re-armed for 28 Nov 01:55.
- "turn on the kitchen light at sunset" scheduled for 16:45 same day.
- "set an alarm for 1:58 am with happy music": tone 200 at 01:58:04, speech,
  music cached and playing at 01:58:29 (`/yt/ulkdUfItyxI`), delivery trace
  `46affa64e431` ok=True. Stopped afterwards.

## Notes / limitations
- Sunset recurrence uses the computed clock time for re-arm; it is not
  recomputed for the next day's sunset.
- Tuya cloud only supports power; colour/brightness use the local IoT bulbs.
- Home Assistant is not configured, so `ha.scene` remains unavailable.
- All scheduled state lives in gitignored `olive_schedule.json`.
