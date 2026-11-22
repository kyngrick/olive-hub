import json
import urllib.request

TOKEN = None
for line in open(r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\.env", encoding="utf-8"):
    if line.startswith("HUB_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip()
        break

phrases = [
    "turn on the kitchen light 4 minutes before sunset",
    "add call mum to my calendar today at 23:59",
    "what's on my calendar",
    "add a reminder to my calendar: buy milk tomorrow at 9",
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
        print(f"{text!r}\n  -> intent={data.get('intent')} text={data.get('text')!r}")
    except Exception as exc:
        print(f"{text!r}\n  -> ERROR {exc}")

print("--- schedule ---")
print(open(r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\olive_schedule.json", encoding="utf-8").read()[:800])
