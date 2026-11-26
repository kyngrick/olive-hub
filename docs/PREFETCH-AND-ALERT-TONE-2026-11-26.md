# Prefetch, personalised cache and audible alarm tone — 26 Nov 2026

## 1. Alarm / reminder tone — verified live
0.14.5 has no custom-sound API, so Olive now generates a two-tone 2-second WAV
(`olive-alert.wav`) and serves it from its own HTTP server at
`http://<PC-LAN-IP>:8765/alert-tone.wav` (no token required, device only).
For every `alarm` or `reminder` delivery, the scheduler:
1. adds the tone as a temporary radio station on the Echo and plays it;
2. then speaks the actual message through the configured Olive voice.

### Live test
```
22:56    "set an alarm for 22:59" -> alarm.set, trace 52d25ffbcfd9
22:59:03 Echo requested /alert-tone.wav -> HTTP 200 (tone)
22:59:05 delivery trace=52d25ffbcfd9 kind=alarm ok=True voice=this is ricky
         announce: "Good morning. It is 22:59. Your alarm is going off."
```
The earlier 22:48 test spoke correctly but the tone URL returned 404 (missing
`pathlib` import in the route); that was fixed and the 22:59 re-test passed.
Speaker playback stopped afterwards.

## 2. Prefetch + personalised artist cache
Research (Alexa/Google patterns): streaming ASR prefetching uses an early
hypothesis to warm caches before the final transcript; personalised predictive
ASR and entity resolution rank likely entities using past behaviour; low-latency
voice systems use semantic caches, strict timeouts and provider ranking.

Implemented:
- `music_prefs.json` records how often each artist is requested.
- `note_artist()` is called when a query is cached; repeated artists rank higher.
- `prefetch_music_cache()` refreshes the top 3 personal artists when the mic
  opens (`Transcribe` event in the Wyoming ASR proxy), at most once per 10 min,
  skipping entries newer than 1 hour. Non-blocking (`asyncio.create_task`).
- Cache remains `music_cache.json` (14-day TTL, negative caching).
- Tests: `tests/test_music_resolver.py` and `tests/test_freemusic.py` pass.

## 3. Other improvements researched, not yet implemented
- **Streaming partial ASR**: the Wyoming protocol here delivers only a final
  transcript, so true partial-hypothesis prefetch would require a different
  ASR transport. The mic-open prefetch above is the practical equivalent.
- **YouTube Music account**: `ytmusicapi` installed and unauthenticated search
  works; cookie extraction from Chrome failed under current Windows cookie
  encryption, and OAuth needs a Google Cloud client. Not wired up yet — the
  user is logged in on music.youtube.com; next step is a local headers file
  or an OAuth client.
- **Provider health circuit-breakers** and **per-user genre profiles** are
  straightforward next steps.

## State
Hub healthy, 112 intents, 9/9 services. Tone and prefetch active.
