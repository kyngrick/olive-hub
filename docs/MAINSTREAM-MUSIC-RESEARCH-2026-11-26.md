# Mainstream music sources — research, test and implementation (26 Nov 2026)

## Research question
Where can Olive legally stream actual mainstream artists (not just Creative
Commons remixes) without a paid subscription or stored credentials?

## Findings
| Source | Auth | What it gives | Verdict |
|---|---|---|---|
| **iTunes Search API** (`itunes.apple.com/search`) | none | Full Apple Music catalog metadata + **30-second AAC preview** (`previewUrl`), artist/album/genre/art | **Implemented — primary** |
| **Deezer API** (`api.deezer.com/search`) | none | Full catalog metadata + **30-second preview** MP3 | **Implemented — backup** |
| Spotify / Apple Music full playback | OAuth paid account | full masters | Not available here; requires user subscription |
| YouTube via yt-dlp | none | full official audio, but scraping ToS grey | Not used |
| Jamendo / Audius / Archive | free/CC | remixes and indieCatalog, not mainstream masters | Kept as later fallbacks |

Both iTunes and Deezer responses are JSON (iTunes serves it as
`text/javascript`, so the client must parse without checking MIME type). The
Echo speaker accepts the iTunes AAC preview directly: verified with
`POST /integrations/radio/play` → `playing: true`, exact `playing_url`.

**Honest limitation:** these are legal 30-second previews, not full masters.
Olive says so in the spoken confirmation ("Apple Music 30s preview").

## Implementation
- `freemusic.py` gained `itunes()` and `deezer()` providers with the same
  normalized `Track` fields (source, id, title, artist, album, genre, duration,
  stream_url, license, art). Registered in `PROVIDERS`.
- Hub `_free_music_tracks()` now searches **iTunes → Deezer → Audius →
  Jamendo → Archive**, with relevance ranking and the existing 14-day cache.
- The Echo is still the first output: first result is added as a temporary
  station and played through the native stream API.
- Spoken replies include the licence/preview note.
- Tests: `tests/test_freemusic.py` (mainstream sources registered) and
  `tests/test_music_resolver.py` both pass.

## Live verification
```
play music from buju banton -> 1.4 s -> Playing Buju Banton - Champion from itunes on the speaker (Apple Music 30s preview)
play music by steven marley -> 1.3 s -> Playing Stephen Marley featuring Mos Def - Hey Baby ... (Apple Music 30s preview)
play music by buju banton  -> 1.3 s -> same (cache hit)
```
Direct provider check returned exact artists for Buju Banton, Stephen Marley
and Lil Wayne from both iTunes and Deezer.

Hub healthy, 112 intents, 9/9 services. Pushed as `541d65e` (previous) and this
change.
