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
4. Right-click the request → **Copy → Copy request headers**.
5. Paste the text into:
   `C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\ytmusic-headers.txt`
6. The pasted text must include lines beginning `cookie:` and
   `x-goog-authuser:`. If `x-goog-authuser` is missing, copy a different
   `/browse` request from a logged-in page.

On the next hub start, `ytmusic_source.ensure_auth()` converts that file once
into `ytmusic-auth.json` using `ytmusicapi.setup(headers_raw=...)`. Both files
are gitignored and never leave the PC.

## Playback
`YTMusic(auth=ytmusic-auth.json)` performs personalised search; `yt-dlp`
resolves a direct audio URL using the same headers (cached ~4 h). The URL is
handed to the Echo's native radio player, so the speaker plays full tracks
without the PC acting as an audio proxy.

## Verification checklist (once the file exists)
- `ytmusic_source.available()` → `True`
- `ytmusic_source.search('buju banton', 2)` → tracks with `videoId`
- `play music by buju banton` → reply says "from your YouTube Music account"
- Hub health still `ok`; alarm tone route still `200`
