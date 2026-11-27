# Taste profile, liked songs, provider health and native light effects — 27 Nov 2026

## 1. Your real taste profile
`ytmusic_source.taste_artists()` reads `YTMusic.get_tasteprofile()` from the
user's own account (1,176 artists; top entries Sizzla, Capleton, Buju Banton,
I Wayne, Richie Spice). `rank_by_taste()` moves matching artists to the front
of every YouTube Music search. Moods/genres asked by voice are also recorded
in `music_prefs.json` under `genres` and included in mic-open prefetch.

New phrases:
- "play my liked songs" → `get_liked_songs()` queue (autoplay on).
- "play my taste mix" → searches the top taste-profile artists and plays them.

## 2. Provider circuit-breakers
`_PROVIDER_HEALTH` tracks failures per free provider (itunes, deezer, audius,
jamendo, archive). One failure opens a 30 s cooldown, doubling up to 15 min;
a success clears it. Failed providers are skipped instead of delaying every
music request. `GET /music-health` (token-protected) reports the state.

## 3. Native light effects
- `iot.py` gained `set_scene()` using tinytuya's native scene mode (verified:
  mode goes to `scene`, then back to `white`). Scenes: party, romance, sleep,
  sunset, movie. "party scene", "scene off".
- "sync lights to music" pulses the bulbs through a colour palette while the
  Echo is playing, then restores white when playback stops. Any "stop" ends it.
- The bulbs also advertise native DPS music mode, but their firmware rejected
  the `set_music_colour` payload, so playback sync uses HSV pulses instead.

## Verification — live, 27 Nov 2026
- "play my liked songs" → Damian Marley - Welcome To Jamrock.
- "play my taste mix" → Sizzla - Inna Dem Face (taste-profile artist).
- "party scene" → native scene on 1 light; "scene off" → restored to white.
- "sync lights to music" → bulb mode `colour` during playback, back to `white`
  after "stop the music".
- Circuit-breaker: one simulated failure gave `available: False`,
  `cooldown_left: 30`; a success reset it to `available: True`.
- `py_compile` clean; 12/12 tests pass; hub healthy.

## Notes
- All credentials and caches stay local and gitignored.
- Taste profile is read-only; nothing is written back to the Google account.
