import json
import time
import urllib.request

TOKEN = None
for line in open(r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\.env", encoding="utf-8"):
    if line.startswith("HUB_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip()
        break


def ask(text, timeout=60):
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8765/command", data=body,
        headers={"X-Olive-Token": TOKEN, "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print(f"{text!r}\n  -> intent={data.get('intent')} text={data.get('text')!r}")
        return data
    except Exception as exc:
        print(f"{text!r}\n  -> ERROR {exc}")
        return {}


for p in [
    "find restaurants near me",
    "find restaurants in Florence",
    "find flights to Rome next week",
    "play daft punk on youtube",
    "open netflix on the tv",
    "scan for lights",
]:
    ask(p)

time.sleep(35)
ask("any new devices?")
