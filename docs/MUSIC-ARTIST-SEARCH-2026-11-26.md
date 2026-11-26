# Artist-based music search — 26 Nov 2026

## What changed
- New intent `music.artist` placed **before** `radio`/`music.mood` so
  "play music by …" is never swallowed by the generic radio handler.
- New handler `h_music_artist`:
  - extracts the artist/genre (`_artist_query`);
  - maps generic crowd phrases: `rappers` → `hip hop rap`;
  - **tries Music Assistant first** via `music/search` and queues every result
    as a multi-track MA queue on the configured player;
  - falls back to YouTube search on the TV (or PC browser) when MA has no
    matching tracks.
- `MusicAssistant` gained `search()` and `queue_uris()` helpers.
- MA container was restarted (its HTTP endpoint had stopped responding).

## Live verification
```
play music by little wayne -> [music.artist] Playing little wayne on the TV from YouTube.
play music by rappers      -> [music.artist] Playing hip hop rap on the TV from YouTube.
```
MA queue path verified with a simulated MA response (`tests/test_music_artist_queue.py`):
3 track URIs queued in order and the reply names them.

## Important limitation — why it used YouTube
Music Assistant is running and healthy, but its configured providers are only:
`builtin`, `ambient_sounds`, `bbc_sounds`, `tunein` plus metadata/player plugins.
There is **no track library provider** (no Spotify/Apple/YouTube Music/local
library). `music/search` therefore returns empty for `Lil Wayne`, `rappers`,
`jazz`, even for `BBC`. The MA queue code is implemented and tested, but it can
only queue tracks once a real music provider with tracks is configured.
No paid service was added. If you want MA to own the queue, a free/local music
source must be connected in MA first (for example a local music library folder).

Final state: hub healthy, **112 intents**, 9/9 services.
