#!/usr/bin/env python3
"""Search free/CC music across several sources, normalise metadata, and either
serve it as a local REST API or hand a track to a Sendspin server.

Only the standard library is needed.  Untested against the live APIs from the
authoring environment, so treat field names as "verify on first run".

Env vars:
  JAMENDO_CLIENT_ID   free key from https://devportal.jamendo.com
  AUDIUS_API_KEY      optional (higher rate limits)

Usage:
  python freemusic.py search "ambient" --genre electronic --source jamendo
  python freemusic.py serve --port 8765        # GET /search?q=&genre=&source=
  python freemusic.py play  "piano" --source archive   # needs: pip install sendspin
"""
import argparse, json, os, shutil, subprocess, sys, tempfile
import urllib.parse, urllib.request
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = "freemusic-sendspin"
UA = f"{APP}/0.1 (personal use)"


@dataclass
class Track:
    source: str
    id: str
    title: str
    artist: str
    album: str = ""
    genre: str = ""
    duration: int = 0          # seconds, 0 = unknown / live
    stream_url: str = ""
    license: str = ""
    art: str = ""


def _seconds(value):
    """Parse seconds from int, float string, or mm:ss / hh:mm:ss."""
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        parts = str(value or "").split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(float(parts[1]))
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        except (TypeError, ValueError):
            pass
        return 0


def get_json(url, params=None, headers=None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


# ---------------------------------------------------------------- providers
def jamendo(query="", genre="", limit=10):
    cid = os.environ.get("JAMENDO_CLIENT_ID")
    if not cid:
        return []
    p = {"client_id": cid, "format": "json", "limit": limit,
         "include": "musicinfo", "audioformat": "mp32"}
    if query:
        p["search"] = query
    if genre:
        p["tags"] = genre
    if not query:
        p["order"] = "popularity_week"
    out = []
    for t in get_json("https://api.jamendo.com/v3.0/tracks/", p).get("results", []):
        tags = (t.get("musicinfo") or {}).get("tags") or {}
        out.append(Track("jamendo", str(t["id"]), t.get("name", ""), t.get("artist_name", ""),
                         t.get("album_name", ""), ", ".join(tags.get("genres") or []) or genre,
                         _seconds(t.get("duration")), t.get("audio", ""),
                         t.get("license_ccurl", ""), t.get("image", "")))
    return out


def audius(query="", genre="", limit=10):
    base = "https://api.audius.co/v1"
    key = os.environ.get("AUDIUS_API_KEY")
    hdr = {"Authorization": f"Bearer {key}"} if key else {}
    p = {"app_name": APP, "limit": limit}
    if genre:
        p["genre"] = genre.title()
    if query:
        p["query"] = query
        data = get_json(f"{base}/tracks/search", p, hdr)
    else:
        data = get_json(f"{base}/tracks/trending", p, hdr)
    out = []
    for t in data.get("data", []):
        art = (t.get("artwork") or {}).get("480x480", "")
        out.append(Track("audius", t["id"], t.get("title", ""), (t.get("user") or {}).get("name", ""),
                         "", t.get("genre", ""), _seconds(t.get("duration")),
                         f"{base}/tracks/{t['id']}/stream?app_name={APP}", "", art))
    return out


def archive(query="", genre="", limit=5):
    q = "mediatype:audio AND licenseurl:(*creativecommons*)"
    if query:
        q += f" AND ({query})"
    if genre:
        q += f" AND subject:({genre})"
    res = get_json("https://archive.org/advancedsearch.php",
                   {"q": q, "fl[]": ["identifier", "title", "creator", "subject", "licenseurl"],
                    "rows": limit, "sort[]": "downloads desc", "output": "json"})
    out = []
    for d in res["response"]["docs"]:
        ident = d["identifier"]
        try:
            files = get_json(f"https://archive.org/metadata/{ident}/files")["result"]
        except Exception:
            continue
        mp3 = next((f for f in files if f.get("name", "").lower().endswith(".mp3")), None)
        if not mp3:
            continue
        creator = d.get("creator", "")
        subj = d.get("subject", "")
        out.append(Track("archive", ident, mp3.get("title") or d.get("title", ""),
                         ", ".join(creator) if isinstance(creator, list) else creator,
                         d.get("title", ""), ", ".join(subj[:3]) if isinstance(subj, list) else subj,
                         _seconds(mp3.get("length")),
                         f"https://archive.org/download/{ident}/{urllib.parse.quote(mp3['name'])}",
                         d.get("licenseurl", ""), f"https://archive.org/services/img/{ident}"))
    return out


def radio(query="", genre="", limit=10):
    p = {"limit": limit, "hidebroken": "true", "order": "clickcount", "reverse": "true"}
    if genre:
        p["tag"] = genre
    if query:
        p["name"] = query
    out = []
    for s in get_json("https://all.api.radio-browser.info/json/stations/search", p):
        out.append(Track("radio", s["stationuuid"], s.get("name", "").strip(), "Internet radio",
                         s.get("country", ""), s.get("tags", ""), 0, s.get("url_resolved", ""),
                         "", s.get("favicon", "")))
    return out


PROVIDERS = {"jamendo": jamendo, "audius": audius, "archive": archive, "radio": radio}


def search(query="", genre="", source="all", limit=10):
    names = PROVIDERS if source == "all" else [source]
    results, errors = [], {}
    for n in names:
        try:
            results += [asdict(t) for t in PROVIDERS[n](query, genre, limit)]
        except Exception as e:                      # keep going if one source is down
            errors[n] = str(e)
    return {"count": len(results), "results": results, "errors": errors}


# ---------------------------------------------------------------- REST server
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/search":
            body = search(q.get("q", ""), q.get("genre", ""), q.get("source", "all"),
                          int(q.get("limit", 10)))
            code = 200
        elif u.path == "/health":
            body, code = {"ok": True, "sources": list(PROVIDERS)}, 200
        else:
            body, code = {"error": "try /search?q=jazz&genre=jazz&source=jamendo"}, 404
        data = json.dumps(body, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# ---------------------------------------------------------------- Sendspin hand-off
def play(track):
    if track["source"] == "radio":
        print("Live radio stream (no file to hand to `sendspin serve`):", track["stream_url"])
        return
    path = os.path.join(tempfile.gettempdir(), f"{track['source']}_{track['id']}.mp3")
    req = urllib.request.Request(track["stream_url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)
    print(f"Serving {track['artist']} - {track['title']}  ({track['license'] or 'check licence'})")
    subprocess.run(["sendspin", "serve", path])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("search", "play"):
        s = sub.add_parser(name)
        s.add_argument("query", nargs="?", default="")
        s.add_argument("--genre", default="")
        s.add_argument("--source", default="all", choices=["all", *PROVIDERS])
        s.add_argument("--limit", type=int, default=10)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()

    if a.cmd == "serve":
        print(f"http://127.0.0.1:{a.port}/search?q=ambient")
        ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
    res = search(a.query, a.genre, a.source, a.limit)
    if a.cmd == "search":
        print(json.dumps(res, indent=2))
    elif res["results"]:
        play(next(t for t in res["results"] if t["stream_url"]))
    else:
        sys.exit(f"No results. {res['errors']}")


if __name__ == "__main__":
    main()
