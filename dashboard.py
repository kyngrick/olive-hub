#!/usr/bin/env python3
"""Olive Hub dashboard on port 8767 (configurable with DASH_PORT).

Open http://127.0.0.1:6666/ and enter the hub token (HUB_TOKEN from .env).
The token is kept in the browser session and sent as X-Olive-Token on API calls;
it is never embedded in the page or written to logs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

from aiohttp import web
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("HUB_TOKEN", "")
HUB_URL = f"http://127.0.0.1:{os.getenv('HUB_PORT', '8765')}"
HOST = os.getenv("DASH_HOST", "127.0.0.1")
PORT = int(os.getenv("DASH_PORT", "8767"))

LOG_FILES: dict[str, Path] = {
    "hub": ROOT / "olive_hub.err",
    "whisper": ROOT / "whisper.err",
    "tts-router": ROOT / "tts-router.err",
    "kokoro": ROOT / "kokoro-wyoming.err",
    "whatsapp": ROOT / "whatsapp-bridge" / "bridge.err",
    "restarts": ROOT / "restart-progress.log",
}


def _env_lines() -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    except Exception:
        pass
    return result


def config_summary() -> list[dict[str, str]]:
    secret = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD)")
    rows = []
    for key, value in sorted(_env_lines().items()):
        shown = "<set>" if secret.search(key) and value else ("<empty>" if secret.search(key) else value)
        rows.append({"key": key, "value": shown})
    return rows


def _script_value(filename: str, variable: str, default: str) -> str:
    try:
        text = (ROOT / filename).read_text(encoding="utf-8-sig", errors="ignore")
        m = re.search(rf"\${variable}\s*=\s*['\"]([^'\"]+)['\"]", text)
        return m.group(1) if m else default
    except Exception:
        return default


def voice_summary() -> dict[str, Any]:
    data: dict[str, Any] = {"active": "", "personas": [], "piper_en": "", "piper_it": ""}
    try:
        import voice_personas as vp
        registry = vp.load()
        data["active"] = registry.get("active") or ""
        data["personas"] = [
            {"name": p.get("name"), "provider": p.get("provider"),
             "voice": p.get("voice"), "ready": bool(p.get("ready")),
             "tone": p.get("tone", "")}
            for p in registry.get("personas", {}).values()
        ]
    except Exception as exc:
        data["error"] = str(exc)
    data["piper_en"] = _script_value("start-tts-detached.ps1", "enVoice", "en_GB-alan-medium")
    data["piper_it"] = _script_value("start-tts-detached.ps1", "itVoice", "it_IT-serena-medium")
    try:
        text = (ROOT / "start-whisper-detached.ps1").read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"--model\s+([^\s']+)", text)
        data["whisper_model"] = m.group(1) if m else "tiny"
    except Exception:
        data["whisper_model"] = "tiny"
    data["kokoro_host"] = os.getenv("KOKORO_HOST", "100.113.130.123")
    data["kokoro_port"] = int(os.getenv("KOKORO_PORT", "10222"))
    return data


def tail_file(path: Path, count: int = 150) -> list[str]:
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            return handle.readlines()[-count:]
    except Exception as exc:
        return [f"(cannot read {path.name}: {exc})"]


async def port_open(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=0.4)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def services_summary() -> list[dict[str, Any]]:
    env = _env_lines()
    checks = [
        ("Hub HTTP", "127.0.0.1", int(env.get("HUB_PORT", "8765"))),
        ("Wyoming ASR", "127.0.0.1", int(env.get("ASR_PORT", "10300"))),
        ("Whisper", "127.0.0.1", int(env.get("WHISPER_PORT", "10301"))),
        ("TTS router", "127.0.0.1", int(env.get("TTS_ROUTER_PORT", "10200"))),
        ("Piper EN", "127.0.0.1", 10201),
        ("Piper IT", "127.0.0.1", 10202),
        ("Kokoro (WSL)", os.getenv("KOKORO_HOST", "100.113.130.123"), 10222),
        ("WhatsApp", "127.0.0.1", int(env.get("WHATSAPP_BRIDGE_PORT", "3014"))),
        ("Music Assistant", "127.0.0.1", 8095),
    ]
    results = await asyncio.gather(*(port_open(h, p) for _, h, p in checks))
    return [{"name": n, "host": h, "port": p, "up": ok}
            for (n, h, p), ok in zip(checks, results)]


async def hub_health() -> dict[str, Any]:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(HUB_URL + "/health", headers={"X-Olive-Token": TOKEN},
                                   timeout=aiohttp.ClientTimeout(total=3)) as resp:
                return await resp.json(content_type=None)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def summary() -> dict[str, Any]:
    health = await hub_health()
    intents = health.get("intents") or []
    if not intents:
        try:
            text = (ROOT / "olive_hub.py").read_text(encoding="utf-8", errors="ignore")
            intents = sorted(set(re.findall(r'Intent\(\s*["\']([^"\']+)', text)))
        except Exception:
            intents = []
    return {
        "hub": health,
        "config": config_summary(),
        "voice": voice_summary(),
        "services": await services_summary(),
        "intents": intents,
        "time": asyncio.get_running_loop().time(),
    }


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path.startswith("/api/"):
        if request.headers.get("X-Olive-Token", "") != TOKEN or not TOKEN:
            return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def index(_request: web.Request) -> web.Response:
    return web.Response(text=HTML, content_type="text/html", charset="utf-8")


async def api_summary(_request: web.Request) -> web.Response:
    return web.json_response(await summary())


async def api_logs(request: web.Request) -> web.Response:
    name = request.query.get("name", "hub")
    path = LOG_FILES.get(name)
    if not path:
        return web.json_response({"error": "unknown log"}, status=404)
    return web.json_response({"name": name, "lines": tail_file(path)})


async def api_local_login(_request: web.Request) -> web.Response:
    # Convenience for the localhost-only dashboard: anyone who can reach this
    # server can already read .env. Disabled when bound to a LAN address.
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        return web.json_response({"error": "local login disabled"}, status=403)
    return web.json_response({"token": TOKEN})


async def api_restart(_request: web.Request) -> web.Response:
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(HUB_URL + "/restart", headers={"X-Olive-Token": TOKEN},
                                    timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return web.json_response({"ok": resp.status == 200, "status": resp.status})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)


def make_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", index)
    app.router.add_get("/api/summary", api_summary)
    app.router.add_post("/api/local-login", api_local_login)
    app.router.add_get("/api/logs", api_logs)
    app.router.add_post("/api/restart", api_restart)
    return app


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Olive Hub Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0d1117;--card:#161b22;--fg:#c9d1d9;--accent:#58a6ff;--ok:#3fb950;--bad:#f85149;--dim:#8b949e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,Roboto,monospace}
header{padding:16px 24px;background:#010409;border-bottom:1px solid #30363d;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
h1{margin:0;font-size:20px;color:var(--accent)}.wrap{padding:20px;max-width:1400px;margin:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.card{background:var(--card);border:1px solid #30363d;border-radius:8px;padding:16px}
.card h2{margin:0 0 12px;font-size:15px;color:var(--accent);text-transform:uppercase;letter-spacing:.08em}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:4px 6px;border-bottom:1px solid #21262d;text-align:left;word-break:break-word}
th{color:var(--dim);font-weight:600}.ok{color:var(--ok)}.bad{color:var(--bad)}.dim{color:var(--dim)}
pre{background:#010409;border:1px solid #30363d;border-radius:6px;padding:10px;max-height:320px;overflow:auto;white-space:pre-wrap;font-size:12px}
button,select,input{background:#21262d;color:var(--fg);border:1px solid #30363d;border-radius:6px;padding:6px 10px;font:inherit}
button{cursor:pointer}button:hover{border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:6px;max-height:260px;overflow:auto}.chip{background:#21262d;border:1px solid #30363d;border-radius:12px;padding:2px 9px;font-size:12px}
#login{position:fixed;inset:0;background:rgba(0,0,0,.88);display:flex;align-items:center;justify-content:center;z-index:10}
#login form{background:var(--card);border:1px solid #30363d;padding:28px;border-radius:10px;width:340px}
#login h2{margin-top:0;color:var(--accent)}#login input{width:100%;margin:10px 0}
.hidden{display:none}.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
</style></head><body>
<header><h1>OLIVE HUB · CONTROL / TEST</h1><div class="bar">
<span id="status" class="dim">connecting…</span>
<select id="logname"><option>hub</option><option>whisper</option><option>tts-router</option><option>kokoro</option><option>whatsapp</option><option>restarts</option></select>
<button onclick="loadAll()">Refresh</button><button onclick="restartHub()">Restart hub</button>
</div></header>
<div id="login"><form onsubmit="login(event)"><h2>Olive Hub token</h2>
<p class="dim">Enter HUB_TOKEN from .env. It stays in this browser session.</p>
<input id="token" type="password" placeholder="X-Olive-Token" autofocus><div class="bar"><button type="submit">Connect</button><button type="button" onclick="localLogin()">Use local token</button></div></form></div>
<div class="wrap">
<div class="grid">
<div class="card"><h2>Hub / services</h2><table id="services"></table></div>
<div class="card"><h2>LLM / features</h2><table id="features"></table></div>
<div class="card"><h2>Voice</h2><table id="voice"></table></div>
<div class="card"><h2>Intents (<span id="intentcount">0</span>)</h2>
<input id="ifilter" placeholder="Filter intents…" oninput="renderIntents()" style="width:100%;margin-bottom:8px">
<div id="intents" class="chips"></div></div>
<div class="card" style="grid-column:1/-1"><h2>Configuration (.env, secrets redacted)</h2>
<input id="cfilter" placeholder="Filter config…" oninput="renderConfig()" style="width:100%;margin-bottom:8px">
<div style="max-height:280px;overflow:auto"><table id="config"></table></div></div>
<div class="card" style="grid-column:1/-1"><h2>Logs · <span id="logtitle">hub</span></h2><pre id="logs"></pre></div>
<div class="card"><h2>Quick test commands</h2><pre>.\.venv\Scripts\python.exe tools\kokoro\test_kokoro.py
.\.venv\Scripts\python.exe tools\kokoro\record_and_clone.py --name Ricky

Alexa clone → sample → "Who is this?" → "Ricky"
Alexa speak like KokoroIT / KokoroEN</pre></div>
</div></div>
<script>
let T=sessionStorage.getItem('olive_token')||'', DATA=null, LOGS=[];
function login(e){e.preventDefault();T=document.getElementById('token').value.trim();sessionStorage.setItem('olive_token',T);document.getElementById('login').classList.add('hidden');loadAll();}
async function localLogin(){let r=await fetch('/api/local-login',{method:'POST'});let d=await r.json();if(d.token){document.getElementById('token').value=d.token;login(new Event('submit'));}else{alert('Local login unavailable: '+JSON.stringify(d));}}
if(T)document.getElementById('login').classList.add('hidden');
async function api(p,opt={}){opt.headers=Object.assign({},opt.headers,{'X-Olive-Token':T});let r=await fetch(p,opt);if(r.status===401){document.getElementById('login').classList.remove('hidden');throw Error('unauthorized')}return r.json();}
function rows(el,items){document.getElementById(el).innerHTML=items.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('');}
function badge(v){return v?'<span class="ok">● up</span>':'<span class="bad">● down</span>'}
function render(){
 const d=DATA,h=d.hub||{},v=d.voice||{};
 document.getElementById('status').textContent=(h.ok?'● hub online':'● hub offline')+' · '+(d.services||[]).filter(s=>s.up).length+'/'+(d.services||[]).length+' services';
 rows('services',(d.services||[]).map(s=>[s.name,s.host+':'+s.port,badge(s.up)]));
 const f=[['LLM',h.llm?'enabled':'disabled'],['Model',h.llm_model||'-'],['Provider',(d.config.find(x=>x.key==='AI_PROVIDER')||{}).value||'-'],
   ['Music Assistant',h.music_assistant?'configured':'no'],['MA reachable',h.music_assistant_reachable?'yes':'no'],
   ['WhatsApp',(h.whatsapp_bridge||{}).status||'-'],['Calendar',h.calendar?'yes':'no'],['Flights',(h.flights||{}).provider||'-'],
   ['Memory',JSON.stringify(h.memory_counts||{})],['Routines',h.scheduled_routines??0],['Conversation turns',h.conversation_turns??0]];
 rows('features',f);
 const personas=(v.personas||[]).map(p=>[p.name+' '+(p.active?'★':''),p.provider,p.voice,p.ready?'ready':'pending']);
 rows('voice',[['Active',v.active||'-','','']].concat(personas).concat([['Piper EN',v.piper_en,'',''],['Piper IT',v.piper_it,'',''],['Whisper',v.whisper_model,'',''],['Kokoro',v.kokoro_host+':'+v.kokoro_port,'','']]));
 document.getElementById('intentcount').textContent=(d.intents||[]).length;
 renderIntents();renderConfig();
}
function renderIntents(){let q=(document.getElementById('ifilter').value||'').toLowerCase();document.getElementById('intents').innerHTML=(DATA?.intents||[]).filter(x=>x.toLowerCase().includes(q)).map(x=>'<span class="chip">'+x+'</span>').join('');}
function renderConfig(){let q=(document.getElementById('cfilter').value||'').toLowerCase();rows('config',(DATA?.config||[]).filter(x=>x.key.toLowerCase().includes(q)||x.value.toLowerCase().includes(q)).map(x=>[x.key,x.value]));}
function renderLogs(){document.getElementById('logtitle').textContent=document.getElementById('logname').value;document.getElementById('logs').textContent=(LOGS||[]).join('');}
async function loadAll(){try{DATA=await api('/api/summary');render();LOGS=(await api('/api/logs?name='+encodeURIComponent(document.getElementById('logname').value))).lines;renderLogs();}catch(e){}}
async function restartHub(){if(!confirm('Restart the hub?'))return;let r=await api('/api/restart',{method:'POST'});alert(r.ok?'Restart requested':'Restart failed: '+JSON.stringify(r));}
document.getElementById('logname').onchange=loadAll;setInterval(loadAll,5000);if(T)loadAll();
</script></body></html>"""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [OLIVE-DASH] %(levelname)s: %(message)s")
    web.run_app(make_app(), host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()

