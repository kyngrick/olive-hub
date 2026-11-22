#!/usr/bin/env python3
"""Test the Kokoro Wyoming server on 127.0.0.1:10222.

Run with the hub venv:
  .venv\Scripts\python.exe tools\kokoro\test_kokoro.py
"""
import asyncio
import os
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, ".venv", "Lib", "site-packages"))

from wyoming.tts import Synthesize, SynthesizeVoice  # noqa: E402
from wyoming.client import AsyncTcpClient  # noqa: E402
from wyoming.audio import AudioChunk, AudioStart, AudioStop  # noqa: E402

OUT_DIR = os.path.join(ROOT, "voice_refs")
HOST = os.getenv("KOKORO_HOST", "100.113.130.123")  # WSL Tailscale IP (stable)
PORT = int(os.getenv("KOKORO_PORT", "10222"))

CASES = [
    ("en", "af_bella", "Hello, this is Olive testing Kokoro text to speech."),
    ("it", "im_nicola", "Buongiorno, sono Olive. Che ore sono?"),
]


async def synth(tag, voice, text):
    t0 = time.monotonic()
    client = AsyncTcpClient(HOST, PORT, connect_timeout=5)
    await client.connect()
    await client.write_event(Synthesize(text=text, voice=SynthesizeVoice(name=voice)).event())
    pcm = bytearray()
    first = None
    rate = 24000
    try:
        for _ in range(3000):
            event = await asyncio.wait_for(client.read_event(), timeout=120)
            if event is None:
                break
            if AudioStart.is_type(event.type):
                rate = int(event.data.get("rate") or 24000)
            if AudioChunk.is_type(event.type):
                pcm.extend(event.payload or b"")
                if first is None:
                    first = time.monotonic() - t0
            if AudioStop.is_type(event.type):
                break
    finally:
        await client.disconnect()
    path = os.path.join(OUT_DIR, f"kokoro_{tag}.wav")
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(pcm))
    print(f"{tag}/{voice}: first={first:.1f}s total={time.monotonic()-t0:.1f}s "
          f"bytes={len(pcm)} -> {path}")
    return path


async def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for case in CASES:
        await synth(*case)


if __name__ == "__main__":
    asyncio.run(main())
