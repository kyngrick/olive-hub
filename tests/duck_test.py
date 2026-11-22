import asyncio, json, time, urllib.request
import paramiko

ROOT = r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2"
TOKEN = next(l.split("=",1)[1].strip() for l in open(ROOT+r"\.env",encoding="utf-8") if l.startswith("HUB_TOKEN="))
PW = next(l.split("=",1)[1].strip() for l in open(ROOT+r"\.env",encoding="utf-8") if l.startswith("SPEAKER_PASSWORD="))

def hub_command(text):
    b = json.dumps({"text": text}).encode()
    r = urllib.request.Request("http://127.0.0.1:8765/command", data=b,
        headers={"X-Olive-Token":TOKEN,"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read().decode())

def device_headers():
    import requests
    base = "http://192.168.1.228:8080/api/v1"
    s = requests.Session()
    csrf = s.get(base+"/config", timeout=5).json()["data"]["csrf_token"]
    tok = s.post(base+"/auth/login", json={"username":"olive","password":PW},
                 headers={"X-LibreEcho-CSRF":csrf}, timeout=5).json()["data"]["token"]
    return s, base, {"X-LibreEcho-CSRF":csrf, "Authorization":"Bearer "+tok}

def set_volume(level):
    s, base, h = device_headers()
    s.put(base+"/audio", json={"volume":level}, headers=h, timeout=5)

def device_volume():
    import requests
    s = requests.Session()
    base = "http://192.168.1.228:8080/api/v1"
    csrf = s.get(base+"/config", timeout=5).json()["data"]["csrf_token"]
    login = s.post(base+"/auth/login", json={"username":"olive","password":PW},
                   headers={"X-LibreEcho-CSRF":csrf}, timeout=5).json()
    tok = login["data"]["token"]
    r = s.get(base+"/audio", headers={"X-LibreEcho-CSRF":csrf,
              "Authorization":"Bearer "+tok}, timeout=5).json()
    return r["data"].get("volume")

async def asr_wake():
    from wyoming.asr import Transcribe
    from wyoming.audio import AudioStart, AudioStop
    from wyoming.client import AsyncTcpClient
    c = AsyncTcpClient("127.0.0.1", 10300, connect_timeout=5)
    await c.connect()
    await c.write_event(Transcribe(language="en").event())
    await c.write_event(AudioStart(rate=16000, width=2, channels=1).event())
    await c.write_event(AudioStop().event())
    try:
        await asyncio.wait_for(c.read_event(), timeout=5)
    except Exception:
        pass
    await c.disconnect()

print("start music:", hub_command("play Capital FM").get("text"))
time.sleep(16)  # let the "Playing..." announcement finish and unduck
set_volume(30)
print("volume before:", device_volume())
asyncio.run(asr_wake())
time.sleep(3)
print("volume during:", device_volume())
print("waiting 27s for auto-restore...")
time.sleep(27)
print("volume after:", device_volume())
print("stop:", hub_command("stop").get("text"))
