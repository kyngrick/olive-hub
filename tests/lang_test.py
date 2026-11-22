import json
import urllib.request

TOKEN = None
for line in open(r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\.env", encoding="utf-8"):
    if line.startswith("HUB_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip()
        break

phrases = [
    "Che ore sono?",
    "Che giorno è oggi?",
    "Com'è il tempo a San Vincenzo?",
    "Perché il cielo è blu?",
    "what is 15 percent of 80",
]

for text in phrases:
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8765/command", data=body,
        headers={"X-Olive-Token": TOKEN, "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print(f"{text!r} -> intent={data.get('intent')} reply={data.get('text')!r}")
    except Exception as exc:
        print(f"{text!r} -> ERROR {exc}")
