#!/usr/bin/env python3
"""Local Wyoming ASR server backed by openai-whisper.

The Olive hub's ASR proxy (port 10300) forwards Wyoming audio events to this
server (port 10301).  Nothing in the project actually ran a server on 10301,
so every voice request ended in ConnectionRefusedError and no transcript ever
reached the hub.  This file fills that gap using the Whisper models that are
already downloaded in ~/.cache/whisper (base.pt, tiny.en.pt, ...).

Configuration (environment variables):
    WHISPER_HOST        listen address, default 127.0.0.1
    WHISPER_PORT        listen port, default 10301
    WHISPER_MODEL       model name, default base (multilingual, fast on CPU)
    WHISPER_LANGUAGE    force a language code (en, it, ...), default auto
    WHISPER_DOWNLOAD_ROOT   model cache directory, default ~/.cache/whisper
    WHISPER_TIMEOUT     max seconds per transcription, default 30
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from typing import Optional

import numpy as np
import whisper
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.server import AsyncEventHandler, AsyncServer

log = logging.getLogger("whisper-wyoming")

HOST = os.getenv("WHISPER_HOST", "127.0.0.1")
PORT = int(os.getenv("WHISPER_PORT", "10301"))
MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
if "--model" in sys.argv:
    MODEL_NAME = sys.argv[sys.argv.index("--model") + 1]
LANGUAGE = os.getenv("WHISPER_LANGUAGE", "") or None
DOWNLOAD_ROOT = os.getenv("WHISPER_DOWNLOAD_ROOT", "")
TIMEOUT = float(os.getenv("WHISPER_TIMEOUT", "30"))


def load_model():
    kwargs = {"device": "cpu", "in_memory": True}
    if DOWNLOAD_ROOT:
        kwargs["download_root"] = DOWNLOAD_ROOT
    log.info("loading Whisper model %r (this happens once at startup)", MODEL_NAME)
    model = whisper.load_model(MODEL_NAME, **kwargs)
    log.info("Whisper model %r ready", MODEL_NAME)
    return model


def pcm_to_audio(data: bytes, rate: int, width: int, channels: int) -> np.ndarray:
    """Turn raw Wyoming PCM into mono float32 at Whisper's 16 kHz."""
    if width == 2:
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(data, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        samples = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported audio width: {width}")

    channels = max(1, int(channels))
    if samples.size % channels:
        samples = samples[: samples.size - (samples.size % channels)]
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)

    rate = int(rate) if rate and rate > 0 else 16000
    if rate != 16000 and samples.size > 1:
        count = max(1, int(round(samples.size * 16000.0 / rate)))
        samples = np.interp(
            np.linspace(0, samples.size - 1, count),
            np.arange(samples.size),
            samples,
        ).astype(np.float32)

    return samples


class WhisperEventHandler(AsyncEventHandler):
    """One Wyoming connection: Transcribe, AudioStart+, AudioStop -> Transcript."""

    def __init__(self, reader, writer, model) -> None:
        super().__init__(reader, writer)
        self.model = model
        self.rate = 16000
        self.width = 2
        self.channels = 1
        self.language: Optional[str] = LANGUAGE
        self.chunks: list[bytes] = []

    async def handle_event(self, event: Event) -> bool:
        try:
            if Transcribe.is_type(event.type):
                request = Transcribe.from_event(event)
                if request.language:
                    self.language = request.language
                self.chunks = []
                return True

            if AudioStart.is_type(event.type):
                fmt = AudioStart.from_event(event)
                self.rate, self.width, self.channels = fmt.rate, fmt.width, fmt.channels
                self.chunks = []
                return True

            if AudioChunk.is_type(event.type):
                chunk = AudioChunk.from_event(event)
                self.chunks.append(chunk.audio)
                return True

            if AudioStop.is_type(event.type):
                text = await self._transcribe()
                await self.write_event(Transcript(text=text).event())
                self.chunks = []
                return True

            return True
        except (ConnectionError, OSError):
            return False
        except Exception:
            log.exception("failed to handle Wyoming event %s", event.type)
            return False

    async def _transcribe(self) -> str:
        if not self.chunks:
            log.warning("audio-stop without audio chunks")
            return ""
        audio = pcm_to_audio(
            b"".join(self.chunks), self.rate, self.width, self.channels
        )
        if audio.size < 400:  # less than ~25 ms: almost certainly silence
            log.info("ignoring very short audio (%d samples)", audio.size)
            return ""

        def work() -> str:
            result = self.model.transcribe(
                audio,
                language=self.language,
                fp16=False,
                verbose=False,
                condition_on_previous_text=False,
            )
            return str(result.get("text", "")).strip()

        try:
            text = await asyncio.wait_for(asyncio.to_thread(work), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("transcription timed out after %.1fs", TIMEOUT)
            return ""
        log.info("transcript: %r", text)
        return text


async def serve() -> None:
    model = await asyncio.to_thread(load_model)
    server = AsyncServer.from_uri(f"tcp://{HOST}:{PORT}")
    handler_factory = lambda reader, writer: WhisperEventHandler(reader, writer, model)
    log.info("Wyoming Whisper ASR listening on tcp://%s:%d", HOST, PORT)
    try:
        await server.run(handler_factory)
    except OSError as exc:
        log.error("cannot listen on %s:%d: %s", HOST, PORT, exc)
        raise


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [OLIVE-WHISPER] %(levelname)s: %(message)s",
    )
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
