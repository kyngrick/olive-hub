#!/usr/bin/env python3
"""Language-routing Wyoming TTS proxy for the LibreEcho speaker.

Listens on the port the speaker uses for TTS (10200) and forwards each
``Synthesize`` request to one of two local Piper servers depending on the
language of the text.  This gives the speaker correct native voices for both
English and Italian without changing its configuration per reply:

    speaker -> tcp://host:10200 (this router)
                  |-- English text -> tcp://127.0.0.1:10201 (en_GB-alan-medium)
                  `-- Italian text -> tcp://127.0.0.1:10202 (it_IT-serena-medium)

Audio events from the chosen backend are relayed verbatim back to the speaker.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import sys

from wyoming.audio import AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.event import Event
from wyoming.info import Info
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize, SynthesizeVoice

log = logging.getLogger("tts-lang-router")

# Function words and common command words are stronger signals than isolated
# vowels, so a short English reply never trips the Italian branch.
IT_WORDS = {
    "ciao", "buongiorno", "buonasera", "grazie", "prego", "perché", "perche",
    "come", "stai", "sono", "sei", "siamo", "siete", "questo", "questa",
    "quello", "quella", "oggi", "domani", "ieri", "metti", "accendi",
    "spegni", "che", "ore", "il", "la", "lo", "gli", "della", "dei", "nel",
    "nella", "con", "per", "non", "più", "piu", "musica", "radio", "tempo",
    "meteo", "parlo", "italiano", "italiana", "mi", "ti", "si", "una", "un",
    "di", "da", "al", "alla", "ho", "hai", "ha", "voglio", "puoi", "per favore",
    "le", "e", "è", "del", "degli", "agli", "sul", "sulla", "sono le", "oggi è",
}


def is_italian(text: str) -> bool:
    words = set(re.findall(r"[a-zàèìòù]+", text.lower()))
    score = len(words & IT_WORDS)
    if any(ch in text for ch in "àèìòù"):
        score += 2
    return score >= 2


class RoutingHandler(AsyncEventHandler):
    def __init__(self, reader, writer, en_host, en_port, it_host, it_port,
                 en_voice, it_voice, clone_port=10203, kokoro_port=10204,
                 persona_file="", kokoro_host="127.0.0.1") -> None:
        super().__init__(reader, writer)
        self.backends = {
            "en": (en_host, en_port),
            "it": (it_host, it_port),
            "clone": ("127.0.0.1", clone_port),
            "kokoro": (kokoro_host, kokoro_port),
        }
        self.voices = {"en": en_voice, "it": it_voice}
        self.persona_file = persona_file
        self.peername = writer.get_extra_info("peername")

    def active_persona(self) -> dict | None:
        if not self.persona_file or not os.path.isfile(self.persona_file):
            return None
        try:
            with open(self.persona_file, encoding="utf-8-sig") as handle:
                data = json.load(handle)
            key = data.get("active") or ""
            persona = (data.get("personas") or {}).get(key)
            return persona if isinstance(persona, dict) else None
        except Exception:
            return None

    async def run(self) -> None:
        # Wyoming's base run() lets a client-side TCP reset escape as an
        # unhandled task exception on Windows.  A speaker closing the socket
        # early is normal, so swallow it here.
        try:
            await super().run()
        except (OSError, ConnectionError):
            pass

    async def _relay(self, event: Event, lang: str) -> bool:
        host, port = self.backends[lang]
        client = AsyncTcpClient(host, port, connect_timeout=3.0)
        try:
            await client.connect()
            await client.write_event(event)
            while True:
                incoming = await asyncio.wait_for(client.read_event(), timeout=60)
                if incoming is None:
                    break
                try:
                    await self.write_event(incoming)
                except (OSError, ConnectionError):
                    # The speaker closed the connection early (e.g. stopped the
                    # announcement); that is not a backend failure.
                    break
                if AudioStop.is_type(incoming.type):
                    break
            return True
        except asyncio.TimeoutError:
            log.warning("backend %s:%s timed out", host, port)
            return False
        except (OSError, ConnectionError) as exc:
            log.warning("backend %s:%s failed: %s", host, port, exc)
            return False
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def handle_event(self, event: Event) -> bool:
        try:
            if Synthesize.is_type(event.type):
                request = Synthesize.from_event(event)
                lang = "it" if is_italian(request.text) else "en"
                name = self.voices[lang]
                persona = self.active_persona()
                if persona and persona.get("ready"):
                    provider = str(persona.get("provider") or "").lower()
                    voice = str(persona.get("voice") or persona.get("name") or "")
                    if provider == "piper":
                        lang = "it" if voice.startswith("it_") else "en"
                        name = voice
                    elif provider in ("kokoro", "omnivoice"):
                        lang = provider
                        name = voice
                log.info("%s: %s -> %s: %r", self.peername, lang, name, request.text[:80])
                routed = Synthesize(
                    text=request.text,
                    voice=SynthesizeVoice(name=name),
                    text_format=request.text_format,
                    context=request.context,
                ).event()
                return await self._relay(routed, lang)
            if Info.is_type(event.type):
                return await self._relay(event, "en")
            # Unknown events are harmless; keep the connection open.
            return True
        except (OSError, ConnectionError):
            return False
        except Exception:
            log.exception("failed to route event from %s", self.peername)
            return False


async def serve(args) -> None:
    server = AsyncServer.from_uri(f"tcp://{args.host}:{args.port}")
    handler_factory = (
        lambda reader, writer: RoutingHandler(
            reader, writer, args.en_host, args.en_port, args.it_host, args.it_port,
            args.en_voice, args.it_voice, args.clone_port, args.kokoro_port,
            args.persona_file, args.kokoro_host,
        )
    )
    log.info(
        "Wyoming TTS language router on tcp://%s:%d (en=%s:%d, it=%s:%d)",
        args.host, args.port, args.en_host, args.en_port, args.it_host, args.it_port,
    )
    await server.run(handler_factory)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=10200)
    parser.add_argument("--en-host", default="127.0.0.1")
    parser.add_argument("--en-port", type=int, default=10201)
    parser.add_argument("--it-host", default="127.0.0.1")
    parser.add_argument("--it-port", type=int, default=10202)
    parser.add_argument("--en-voice", default="en_GB-alan-medium")
    parser.add_argument("--it-voice", default="it_IT-serena-medium")
    parser.add_argument("--clone-port", type=int, default=10203)
    parser.add_argument("--kokoro-port", type=int, default=10204)
    parser.add_argument("--kokoro-host", default="127.0.0.1")
    parser.add_argument("--persona-file", default="")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [OLIVE-TTS-ROUTER] %(levelname)s: %(message)s",
    )
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
