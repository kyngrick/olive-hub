# Free-music sources — integration report, 26 Nov 2026

## Files
- `freemusic.py` (project root; also `tools/freemusic.py`) — the supplied script,
  with one parser fix.
- `olive_hub.py` — `music.artist` handler now uses these sources.
- `tests/test_freemusic.py` — field/duration parser tests (pass).

## Field-name verification against live APIs
| Source | Result | Notes |
|---|---|---|
| Audius | **OK** | `data[].title`, `.user.name`, `.genre`, `.duration`, `.artwork['480x480']`, `/tracks/{id}/stream` all valid. No key needed. |
| Internet Archive | **OK after fix** | `response.docs[]`, `/metadata/{id}/files`, `.name`, `.length`, `.licenseurl`, `services/img/{id}` all valid. Duration sometimes `"04:29"`, which crashed `float()` — fixed with `_seconds()`. |
| Radio Browser | **OK** | `stationuuid`, `name`, `country`, `tags`, `url_resolved`, `favicon` valid. Requires User-Agent — supplied. |
| Jamendo | **not live-tested** | Needs a free `JAMENDO_CLIENT_ID`; none available locally. Field names from the script match Jamendo v3 docs (`name`, `artist_name`, `album_name`, `duration`, `audio`, `license_ccurl`, `image`, `musicinfo.tags.genres`). Set the env var to enable it; errors from other sources never block the rest. |

`serve` mode verified on port **8770** (not 8765, which the hub uses):
```
/health -> {"ok":true,"sources":["jamendo","audius","archive","radio"]}
/search?q=jazz&genre=jazz&source=archive&limit=2 -> 2 tracks with licence URLs
```

## Hub integration
`music.artist` order is now:
1. Music Assistant catalog search (`music/search`) → queue all matching tracks.
2. **Free sources** (`freemusic.search`: Audius, Archive, optional Jamendo) →
   queue up to 8 stream URLs on Music Assistant with a spoken source/licence note.
3. YouTube mix on the TV / PC browser as last resort.

Verified live:
```
play music by little wayne -> Playing 2 tracks for little wayne from audius on Music Assistant …
play music by rappers      -> Playing 8 tracks for rappers from audius on Music Assistant …
```
MA reported `playing=True` after both commands.

## Licence note
Each result reports a licence where the source provides one; the spoken reply
says "Check the source licence before reuse" when none is given. Jamendo's free
tier is non-commercial; Archive results include CC licences. No paid service added.

## State
Hub healthy, 112 intents, 9/9 services. The temporary `serve` process on 8770
was stopped after testing; restart with:
```powershell
.\.venv\Scripts\python.exe .\freemusic.py serve --port 8770
```
