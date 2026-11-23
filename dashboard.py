#!/usr/bin/env python3
"""Olive Hub dashboard on port 8767 (configurable with DASH_PORT).

Open http://127.0.0.1:8767/ and enter the hub token (HUB_TOKEN from .env).
The token is kept in the browser session and sent as X-Olive-Token on API calls;
it is never embedded in the page or written to logs.

The UI is customisable (theme, visible cards, card order, refresh rate, quick
commands) with preferences saved in the browser. Non-secret .env values can be
edited from the Configuration card; edits are backed up before being written.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime
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
ENV_FILE = ROOT / ".env"

SECRET_RE = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD)")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

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
        for line in ENV_FILE.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    except Exception:
        pass
    return result


def config_summary() -> list[dict[str, str]]:
    rows = []
    for key, value in sorted(_env_lines().items()):
        shown = "<set>" if SECRET_RE.search(key) and value else ("<empty>" if SECRET_RE.search(key) else value)
        rows.append({"key": key, "value": shown, "secret": bool(SECRET_RE.search(key))})
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
    if request.path.startswith("/api/") and request.path != "/api/local-login":
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


async def api_config_set(request: web.Request) -> web.Response:
    """Write one non-secret .env value, keeping a timestamped backup."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    key = str(data.get("key", "")).strip()
    value = str(data.get("value", ""))
    if not ENV_KEY_RE.match(key):
        return web.json_response({"ok": False, "error": "key must look like UPPER_CASE_NAME"}, status=400)
    if SECRET_RE.search(key):
        return web.json_response({"ok": False, "error": "secret keys cannot be edited here"}, status=403)

    try:
        original = ENV_FILE.read_text(encoding="utf-8-sig", errors="ignore")
        lines = original.splitlines()
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith("#") or "=" not in line:
                continue
            if line.split("=", 1)[0].strip() == key:
                lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"{key}={value}")

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = ENV_FILE.with_name(f".env.bak-{stamp}")
        backup.write_text(original, encoding="utf-8")

        fd, tmp_name = tempfile.mkstemp(prefix=".env-", dir=str(ROOT))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(tmp_name, ENV_FILE)
        return web.json_response({"ok": True, "key": key, "value": value,
                                  "backup": backup.name,
                                  "note": "Restart the hub (and dashboard if DASH_* changed) to apply."})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


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
    app.router.add_post("/api/config", api_config_set)
    app.router.add_post("/api/restart", api_restart)
    return app


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Olive Hub Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0d1117;--card:#161b22;--fg:#c9d1d9;--accent:#58a6ff;--ok:#3fb950;--bad:#f85149;--dim:#8b949e;--line:#30363d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,Roboto,monospace}
header{padding:16px 24px;background:#010409;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px;flex-wrap:wrap}
h1{margin:0;font-size:20px;color:var(--accent)}.wrap{padding:20px;max-width:1400px;margin:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px}
.card h2{margin:0 0 12px;font-size:15px;color:var(--accent);text-transform:uppercase;letter-spacing:.08em}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left;word-break:break-word}
th{color:var(--dim);font-weight:600}.ok{color:var(--ok)}.bad{color:var(--bad)}.dim{color:var(--dim)}
pre{background:#010409;border:1px solid var(--line);border-radius:6px;padding:10px;max-height:320px;overflow:auto;white-space:pre-wrap;font-size:12px}
button,select,input,textarea{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font:inherit}
button{cursor:pointer}button:hover{border-color:var(--accent)}button.mini{padding:2px 8px;font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;max-height:260px;overflow:auto}.chip{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:2px 9px;font-size:12px}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;z-index:10}
.modal form{background:var(--card);border:1px solid var(--line);padding:24px;border-radius:10px;width:min(560px,92vw);max-height:90vh;overflow:auto}
.modal h2{margin-top:0;color:var(--accent)}#customize form{width:min(680px,94vw)}
#login form{width:min(380px,92vw)}.modal input{width:100%;margin:6px 0}.modal textarea{width:100%;height:110px;margin:6px 0}
.hidden{display:none}.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.rowline{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0}
.rowline input{flex:1;min-width:120px}.err{color:var(--bad);min-height:20px;font-size:13px}
.cfgsave{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.cfgsave input{flex:1;min-width:120px}
.orderlist label{display:flex;justify-content:space-between;align-items:center;padding:3px 0}
.note{font-size:12px;color:var(--dim)}
</style></head><body>
<header><h1>OLIVE HUB · CONTROL / TEST</h1><div class="bar">
<span id="status" class="dim">connecting…</span>
<select id="logname"><option>hub</option><option>whisper</option><option>tts-router</option><option>kokoro</option><option>whatsapp</option><option>restarts</option></select>
<button onclick="loadAll()">Refresh</button><button onclick="restartHub()">Restart hub</button>
<button onclick="openCustomize()">Customize</button><button onclick="lock()">Change token</button>
</div></header>

<div id="login" class="modal"><form onsubmit="login(event)"><h2>Olive Hub token</h2>
<p class="dim">Enter HUB_TOKEN from .env. It stays in this browser session.</p>
<input id="token" type="password" placeholder="X-Olive-Token" autofocus>
<div id="loginerr" class="err"></div>
<div class="bar"><button type="submit">Connect</button><button type="button" onclick="localLogin()">Use local token</button></div></form></div>

<div id="customize" class="modal hidden"><form onsubmit="savePrefs(event)">
<h2>Customize dashboard</h2>
<div class="rowline"><label>Theme
<select id="theme"><option value="midnight">Midnight</option><option value="ocean">Ocean</option><option value="light">Light</option><option value="forest">Forest</option></select></label></div>
<div class="rowline"><label>Accent <input id="accent" type="color" value="#58a6ff"></label></div>
<p class="dim">Visible cards and their order:</p>
<div id="orderlist" class="orderlist"></div>
<div class="rowline"><label>Refresh every <input id="interval" type="number" min="2" max="120" value="5" style="width:80px"> seconds</label></div>
<p class="dim">Quick test commands (shown in the Quick commands card):</p>
<textarea id="commands"></textarea>
<div id="preferr" class="err"></div>
<div class="bar"><button type="submit">Save</button><button type="button" onclick="resetPrefs()">Reset defaults</button><button type="button" onclick="closeCustomize()">Cancel</button></div>
</form></div>

<div class="wrap"><div class="grid" id="grid">
<div class="card" id="card-services"><h2>Hub / services</h2><table id="services"></table></div>
<div class="card" id="card-features"><h2>LLM / features</h2><table id="features"></table></div>
<div class="card" id="card-voice"><h2>Voice</h2><table id="voice"></table></div>
<div class="card" id="card-intents"><h2>Intents (<span id="intentcount">0</span>)</h2>
<input id="ifilter" placeholder="Filter intents…" oninput="renderIntents()" style="width:100%;margin-bottom:8px">
<div id="intents" class="chips"></div></div>
<div class="card" id="card-config" style="grid-column:1/-1"><h2>Configuration (.env, secrets redacted)</h2>
<div class="cfgsave"><input id="cfgkey" placeholder="KEY"><input id="cfgval" placeholder="new value"><button class="mini" onclick="saveConfig()">Save value</button></div>
<div id="cfgerr" class="err"></div>
<input id="cfilter" placeholder="Filter config…" oninput="renderConfig()" style="width:100%;margin-bottom:8px">
<div style="max-height:280px;overflow:auto"><table id="config"></table></div>
<p class="note">Non-secret values only. A timestamped .env backup is made before each change; restart the hub to apply. DASH_* changes need a dashboard restart.</p></div>
<div class="card" id="card-logs" style="grid-column:1/-1"><h2>Logs · <span id="logtitle">hub</span></h2><pre id="logs"></pre></div>
<div class="card" id="card-quick"><h2>Quick test commands</h2><pre id="quicktext"></pre></div>
<div class="card" id="card-libreecho"><h2>LibreEcho upgrade</h2><div id="leinfo" class="dim">checking…</div></div>
</div></div>

<script>
const DEFAULT_COMMANDS = ".\\.venv\\Scripts\\python.exe tools\\kokoro\\test_kokoro.py\n.\\.venv\\Scripts\\python.exe tools\\kokoro\\record_and_clone.py --name Ricky\n\nAlexa clone → sample → \"Who is this?\" → \"Ricky\"\nAlexa speak like KokoroIT / KokoroEN";
const CARD_NAMES = {services:'Hub / services',features:'LLM / features',voice:'Voice',intents:'Intents',config:'Configuration',logs:'Logs',quick:'Quick commands',libreecho:'LibreEcho upgrade'};
const DEFAULTS = {theme:'midnight',accent:'#58a6ff',interval:5,commands:DEFAULT_COMMANDS,
  show:{services:true,features:true,voice:true,intents:true,config:true,logs:true,quick:true,libreecho:true},
  order:['services','features','voice','intents','config','logs','quick','libreecho']};
const THEMES = {midnight:{bg:'#0d1117',card:'#161b22',fg:'#c9d1d9'},ocean:{bg:'#071a2b',card:'#0f2942',fg:'#d6e9ff'},light:{bg:'#f6f8fa',card:'#ffffff',fg:'#1f2328'},forest:{bg:'#0b1712',card:'#13251c',fg:'#d8f3e2'}};
let T=sessionStorage.getItem('olive_token')||'', DATA=null, LOGS=[], TIMER=null;
let PREFS=loadPrefs();

function loadPrefs(){
  try{let p=JSON.parse(localStorage.getItem('olive_prefs')||'null');
    if(p&&p.order&&p.show){return Object.assign({},DEFAULTS,p,{show:Object.assign({},DEFAULTS.show,p.show)});}
  }catch(e){}
  return JSON.parse(JSON.stringify(DEFAULTS));
}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function rows(el,items){document.getElementById(el).innerHTML=items.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('');}
function badge(v){return v?'<span class="ok">● up</span>':'<span class="bad">● down</span>'}

function applyPrefs(){
  const th=THEMES[PREFS.theme]||THEMES.midnight, root=document.documentElement.style;
  root.setProperty('--bg',th.bg);root.setProperty('--card',th.card);root.setProperty('--fg',th.fg);root.setProperty('--accent',PREFS.accent);
  const grid=document.getElementById('grid');
  PREFS.order.forEach(id=>{const el=document.getElementById('card-'+id);if(el){el.classList.toggle('hidden',!PREFS.show[id]);grid.appendChild(el);}});
  document.getElementById('quicktext').textContent=PREFS.commands;
  document.getElementById('interval').value=PREFS.interval;
  if(TIMER)clearInterval(TIMER);
  TIMER=setInterval(loadAll,Math.max(2,Number(PREFS.interval)||5)*1000);
}
function openCustomize(){
  document.getElementById('theme').value=PREFS.theme;document.getElementById('accent').value=PREFS.accent;
  document.getElementById('interval').value=PREFS.interval;document.getElementById('commands').value=PREFS.commands;
  const list=document.getElementById('orderlist');list.innerHTML='';
  PREFS.order.forEach((id,i)=>{
    const row=document.createElement('div');row.className='rowline';
    row.innerHTML='<label><input type="checkbox" data-show="'+id+'" '+(PREFS.show[id]?'checked':'')+'> '+esc(CARD_NAMES[id])+'</label>'+
      '<span><button class="mini" onclick="moveCard(\''+id+'\',-1)">↑</button> <button class="mini" onclick="moveCard(\''+id+'\',1)">↓</button></span>';
    list.appendChild(row);
  });
  document.getElementById('customize').classList.remove('hidden');
}
function closeCustomize(){document.getElementById('customize').classList.add('hidden');}
function moveCard(id,dir){
  const i=PREFS.order.indexOf(id), j=i+dir;
  if(i<0||j<0||j>=PREFS.order.length)return;
  [PREFS.order[i],PREFS.order[j]]=[PREFS.order[j],PREFS.order[i]];openCustomize();
}
function savePrefs(e){
  if(e)e.preventDefault();
  PREFS.theme=document.getElementById('theme').value;PREFS.accent=document.getElementById('accent').value;
  PREFS.interval=Math.max(2,Number(document.getElementById('interval').value)||5);
  PREFS.commands=document.getElementById('commands').value;
  document.querySelectorAll('[data-show]').forEach(cb=>PREFS.show[cb.dataset.show]=cb.checked);
  localStorage.setItem('olive_prefs',JSON.stringify(PREFS));applyPrefs();closeCustomize();
}
function resetPrefs(){
  PREFS=JSON.parse(JSON.stringify(DEFAULTS));localStorage.removeItem('olive_prefs');applyPrefs();openCustomize();
}

function showLogin(msg){document.getElementById('loginerr').textContent=msg||'';document.getElementById('login').classList.remove('hidden');}
function hideLogin(){document.getElementById('login').classList.add('hidden');}
async function api(p,opt={}){
  opt.headers=Object.assign({},opt.headers,{'X-Olive-Token':T});
  const r=await fetch(p,opt);
  if(r.status===401)throw new Error('unauthorized');
  if(!r.ok)throw new Error('HTTP '+r.status);
  return r.json();
}
async function login(e){
  if(e)e.preventDefault();
  const tok=document.getElementById('token').value.trim();
  if(!tok){showLogin('Enter the token first.');return;}
  T=tok;showLogin('Checking token…');
  try{DATA=await api('/api/summary');sessionStorage.setItem('olive_token',T);hideLogin();render();await loadLogs();}
  catch(err){T='';sessionStorage.removeItem('olive_token');showLogin('Token rejected. Check HUB_TOKEN in .env ('+err.message+').');}
}
async function localLogin(){
  try{
    const r=await fetch('/api/local-login',{method:'POST'}),d=await r.json();
    if(d.token){document.getElementById('token').value=d.token;await login(null);}
    else showLogin('Local login unavailable: '+JSON.stringify(d));
  }catch(err){showLogin('Local login failed: '+err.message);}
}
function lock(){sessionStorage.removeItem('olive_token');T='';DATA=null;document.getElementById('token').value='';showLogin('Session locked. Enter the token again.');}

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
  renderIntents();renderConfig();renderLibreEcho();
}
function renderIntents(){let q=(document.getElementById('ifilter').value||'').toLowerCase();document.getElementById('intents').innerHTML=(DATA?.intents||[]).filter(x=>x.toLowerCase().includes(q)).map(x=>'<span class="chip">'+esc(x)+'</span>').join('');}
function renderConfig(){
  let q=(document.getElementById('cfilter').value||'').toLowerCase();
  rows('config',(DATA?.config||[]).filter(x=>x.key.toLowerCase().includes(q)||x.value.toLowerCase().includes(q))
    .map(x=>[esc(x.key),esc(x.value),x.secret?'':'<button class="mini" onclick="fillConfig(\''+esc(x.key)+'\',\''+esc(x.value)+'\')">Edit</button>']));
}
function fillConfig(k,v){document.getElementById('cfgkey').value=k;document.getElementById('cfgval').value=v==='&lt;empty&gt;'?'':v;}
async function saveConfig(){
  const key=document.getElementById('cfgkey').value.trim(), value=document.getElementById('cfgval').value;
  const err=document.getElementById('cfgerr');
  if(!key){err.textContent='Enter a KEY.';return;}
  err.textContent='Saving…';
  try{
    const r=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,value})});
    err.textContent=r.ok?'Saved '+key+' (backup: '+r.backup+'). Restart the hub to apply.':('Failed: '+r.error);
    if(r.ok){document.getElementById('cfgval').value='';loadAll();}
  }catch(e){err.textContent='Failed: '+e.message;}
}
function renderLogs(){document.getElementById('logtitle').textContent=document.getElementById('logname').value;document.getElementById('logs').textContent=(LOGS||[]).join('');}
function renderLibreEcho(){
  document.getElementById('leinfo').innerHTML='<b>Stable: 0.14.6</b> (17 Nov 2026) · installed: 0.14.5<br>'+
    '<span class="ok">● In-place OTA is supported</span> — no reinstall needed.<br>'+
    'Update from the speaker control centre: <b>System › System update › Check for updates</b>.<br>'+
    '<span class="bad">Do not</span> upload the update file by hand — broken in 0.14.6.<br>'+
    'Relevant fixes: Sendspin resumes after voice replies/mute, Wi-Fi pauses fixed, '+
    'repeatable alarms & spoken reminders, AirPlay skip/seek fixes.<br>'+
    'Known issue: AirPlay from Music Assistant still needs Sendspin, AirPlay 2-compatibility '+
    'or AirPlay 1 (RAOP). Keep the signed A/B rollback slot.';
}
async function loadLogs(){
  LOGS=(await api('/api/logs?name='+encodeURIComponent(document.getElementById('logname').value))).lines;renderLogs();
}
async function loadAll(){
  if(!T)return;
  try{DATA=await api('/api/summary');render();await loadLogs();}
  catch(e){if(e.message==='unauthorized')showLogin('Session expired. Enter the token again.');}
}
async function restartHub(){
  if(!confirm('Restart the hub?'))return;
  try{const r=await api('/api/restart',{method:'POST'});alert(r.ok?'Restart requested':'Restart failed: '+JSON.stringify(r));}
  catch(e){alert('Restart failed: '+e.message);}
}
document.getElementById('logname').onchange=loadLogs;
applyPrefs();
if(T){
  showLogin('Checking saved token…');
  api('/api/summary').then(d=>{DATA=d;hideLogin();render();loadLogs();})
    .catch(()=>{T='';sessionStorage.removeItem('olive_token');showLogin('Saved token was rejected. Check HUB_TOKEN in .env.');});
}else showLogin();
</script></body></html>"""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [OLIVE-DASH] %(levelname)s: %(message)s")
    web.run_app(make_app(), host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
