import json
import urllib.request

TOKEN = None
for line in open(r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\.env", encoding="utf-8"):
    if line.startswith("HUB_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip()
        break

phrases = [
    "add milk to my shopping list",
    "add bread to my shopping list",
    "what's on my shopping list",
    "remove milk from my shopping list",
    "what's on my shopping list",
    "clear my shopping list",
    "tell me a joke",
    "give me a quote",
    "convert 10 miles to km",
    "convert 20 celsius to fahrenheit",
    "how much is 100 dollars in euros",
    "give me my morning briefing",
    "find restaurants near me",
]

for text in phrases:
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8765/command", data=body,
        headers={"X-Olive-Token": TOKEN, "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print(f"{text!r}\n  -> {data.get('intent')}: {data.get('text')!r}")
    except Exception as exc:
        print(f"{text!r}\n  -> ERROR {exc}")
