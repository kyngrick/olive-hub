"""End-to-end test: talk to the hub ASR proxy (10300) like the speaker does."""
import asyncio
import sys
import wave

sys.path.insert(0, r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\.venv\Lib\site-packages")
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient

WAV = r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\test_input.wav"


async def main():
    with wave.open(WAV, "rb") as wf:
        rate, width, channels = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    print(f"audio: {rate} Hz, {width} bytes, {channels} channel(s), {len(pcm)} bytes")
    client = AsyncTcpClient("127.0.0.1", 10300, connect_timeout=5)
    await client.connect()
    await client.write_event(Transcribe(language="en").event())
    await client.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
    for i in range(0, len(pcm), 1024):
        await client.write_event(AudioChunk(rate=rate, width=width, channels=channels,
                                            audio=pcm[i:i + 1024]).event())
    await client.write_event(AudioStop().event())
    text = ""
    for _ in range(5):
        event = await asyncio.wait_for(client.read_event(), timeout=25)
        if event is None:
            break
        if Transcript.is_type(event.type):
            text = Transcript.from_event(event).text
            break
    await client.disconnect()
    print(f"TRANSCRIPT: {text!r}")
    return 0 if text.strip() else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
