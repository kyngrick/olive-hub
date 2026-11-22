import json
import urllib.request

TOKEN = None
for line in open(r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\.env", encoding="utf-8"):
    if line.startswith("HUB_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip()
        break

for text in ["Perché il cielo è blu?", "Why is the sky blue?"]:
    body = json.dumps({
        "model": "olive",
        "messages": [{"role": "user", "content": text}],
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8765/v1/chat/completions", data=body,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    print(f"{text!r} -> content={content!r} (hub speaks the reply via /audio/announce)")
