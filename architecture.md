# Olive Assistant Architecture (v0.3)

- Raspberry Pi 5 / desktop runs the persistent local assistant, microphone, GPIO, MQTT and Home Assistant integration.
- FastAPI provides the local API and browser dashboard.
- OpenAI-compatible `/v1/chat/completions` providers are the optional reasoning layer: Groq (`AI_PROVIDER=groq`, `GROQ_API_KEY`), Ollama or OpenAI. The live speaker hub uses the same Groq models via `olive_hub.py`.
- Default model is `openai/gpt-oss-120b` (fallback `openai/gpt-oss-20b`); output is capped and conversation history is limited to ten turns with a 120-second TTL.
- Weather is fetched directly from Open-Meteo; music requests are converted to a YouTube Music or Spotify search link. These routes do not call OpenAI.
- The browser handles speech recognition and speech synthesis; these do not use the OpenAI API key.
- Home Assistant control remains simulation-only unless explicitly configured.
- Vercel can host the dashboard and request/response API, but cannot replace the Pi's always-on microphone, GPIO, MQTT broker, or persistent local services.
- Public Vercel chat fails closed until `OLIVE_ACCESS_PASSWORD` is configured.
