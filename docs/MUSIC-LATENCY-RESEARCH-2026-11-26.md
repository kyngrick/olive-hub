# Music request latency and relevance — research + fix (26 Nov 2026)

## The symptom
- `play music by buju banton` / `play music from buju banton` were slow and
  sometimes ended in "Sorry, that didn't work."
- `play music by steven marley` produced an unrelated Internet Archive track
  ("A Tiny Window – Mama Africa").
- Whisper transcripts in the log were `budubantan`, `budubantan`,
  `a little wind`, so the artist name was often wrong before search even began.

## Measured provider latency (local)
| Provider | Time for 5 results | Relevance for the request |
|---|---|---|
| Audius | **0.7 s** | real Buju Banton remixes |
| Internet Archive | **6.3 s** | unrelated items (audiobooks/podcasts) |
| Radio Browser | 0.4 s | stations (genre fallback only) |
| Jamendo | 0.5 s | occasional zero-result responses |

Our old code searched Audius **and Archive and Jamendo** for every request,
so the slowest path decided the total latency, and Archive noise won the
ranking.

## How Alexa / Google Music do it (public sources)
- Amazon's Alexa Music skill docs describe **uploaded/pre-indexed catalogs** and
  entity types in `selectionCriteria`; a voice request is resolved to a catalog
  entity, not searched across arbitrary websites at request time.
- Voice-assistant latency work (Interspeech/arXiv) describes **streaming ASR +
  prefetching**: an early transcript hypothesis is used to prefetch/cache the
  likely response while speech is still arriving.
- Entity-resolution research describes a two-stage **relevance generation +
  ranking** step; edge-oriented work (SmallER) stresses bounded local catalog
  storage and ranking on-device to avoid cloud round-trips.
- Practical low-latency voice guides recommend **semantic caches**, prefix
  caching, tool-call prefetching and strict provider timeouts.

Translated to Olive: normalize the spoken artist, resolve against a small local
cache first, query the fastest high-quality provider first, require relevance,
and only then try slower sources — with a genre fallback rather than junk.

## Implemented
1. **ASR artist aliases** (`correct_music_query`): `budubantan/budubaton` →
   `buju banton`, `steven marley` → `stephen marley`, `a little wind` → `lil wayne`.
2. **Persistent cache** `music_cache.json`, TTL 14 days, atomic writes, keyed by
   normalized query. Caches empty results too (negative cache) so an artist with
   no free track does not re-trigger slow searches.
3. **Provider order and early exit**: Audius first; only call Archive/Jamendo if
   Audius has too few relevant matches and the query is short or partly matched.
4. **Relevance ranking**: multi-word artist queries require both tokens, so
   "Stephen Hawking" no longer beats "Stephen Marley"; single tokens need one.
5. **Genre fallback**: if a known artist has no free track, rank Radio Browser
   stations by tag/name match (not just popularity) and play a real reggae
   station on the Echo, with an honest spoken explanation.
6. **Honest failure**: YouTube fallback has a 12 s timeout and a clear spoken
   error instead of a generic "didn't work".
7. New tests: `tests/test_music_resolver.py` (aliases, relevance, cache roundtrip).

## Measured results after the fix
```
play music from buju banton  -> 1.4 s -> playing Buju Banton remix from Audius on the speaker
play music by steven marley  -> 1.8 s -> "couldn't find that artist ... playing reggae radio" (La Grosse Radio Reggae)
play music by buju banton    -> 1.4 s (cache hit, same result)
```
Second identical request for `buju banton` was **0.0 s** in the resolver test
(cache hit). The old path took roughly 7–9 s and could fail entirely.

## Remaining limitations
- Free catalogues are remix/CC catalogues; they rarely contain mainstream
  master recordings. "Playing Buju Banton" may be a remix by another artist.
- Radio fallback is genre-level, not the exact artist.
- Cache is local to this PC; it does not prefetch from partial ASR yet (the
  next possible improvement, matching the research above).
- MA still has no track provider; the Echo direct path is used first.

Final state: hub healthy, 112 intents, 9/9 services.
