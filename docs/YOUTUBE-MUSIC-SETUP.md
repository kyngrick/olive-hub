# YouTube Music with the user's own account — setup

Status: **code ready; awaiting the local headers file** (26 Nov 2026).

## What this adds
`ytmusic_source.py` is an optional provider that uses the user's own logged-in
Google/YouTube Music session so voice requests can play real tracks from their
account. It is the first music provider tried in `music.artist` when configured;
if anything fails, the existing iTunes/Deezer 30s previews and free sources
continue to work exactly as before.

No card details, no free-trial signup, no external deployment. Credentials stay
on this PC.

## One-time setup (do not paste secrets in chat)
1. In Chrome, log in at `https://music.youtube.com/` and stay logged in.
2. Press **F12** → **Network** tab → reload the page.
3. Select a `POST` request to `music.youtube.com/youtubei/v1/browse`
   (click around the site first if needed).
4. Right-click the request → **Copy → Copy request headers**. If your Chrome
   menu has no such item, use **Copy as cURL (powershell)** (or bash/cmd) —
   the hub parses cURL commands too.
5. Paste the text into:
   `C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\ytmusic-headers.txt`
6. The pasted text must include a `cookie:`. If `x-goog-authuser:` is missing,
   the hub adds `x-goog-authuser: 0` (default account) automatically.

On the next hub start, `ytmusic_source.ensure_auth()` converts that file once
into `ytmusic-auth.json` using `ytmusicapi.setup(headers_raw=...)`. Both files
are gitignored and never leave the PC.

## Playback
`YTMusic(auth=ytmusic-auth.json)` performs personalised search. `yt-dlp`
(resolving the current YouTube n-challenge with the installed Node.js and
yt-dlp-ejs components) downloads the track once into `yt_cache/`, and the
bundled `imageio-ffmpeg` binary remuxes it to a standard MP3. The hub serves
that file at a short URL, `http://<PC-LAN-IP>:8765/yt/<videoId>`, with a proper
`Content-Length`; the Echo's native radio player then plays the full track.

Why the local file: the Echo rejects station URLs longer than a few hundred
characters (GoogleVideo URLs are ~1.2 KB), it does not play chunked responses
or DASH m4a containers reliably, and signed URLs require Range requests. A
local MP3 with a known length avoids all three problems. Cached tracks are
kept for 7 days; `yt_cache/` is gitignored.

Dependencies added: `ytmusicapi`, `yt-dlp`, `imageio-ffmpeg` (all free/local).

## Verification — 27 Nov 2026 (passed)
- `ytmusic_source.available()` → `True`; authenticated search returned 20 songs.
- `play music by buju banton champion` → "Playing Buju Banton - Champion from
  your YouTube Music account on the speaker."
- Echo fetched `/yt/nMD4GumDjxA` once, `200` with 3,797,884 bytes (no retries),
  and `/integrations/radio` reported `playing: True` 30 s later.
- Hub health `ok`; `/alert-tone.wav` still `200`; playback stopped afterwards.
