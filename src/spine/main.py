"""Entry point for the spine — the pipecat-free walking skeleton.

    uv run python -m src.spine.main --check        # wire everything, open nothing
    uv run python -m src.spine.main --text "..."   # one text turn, prints (and speaks) the reply
    uv run python -m src.spine.main                # live: microphone in, voice out

Live mode wants the daemon stopped first: two processes talking through
one speaker hear each other, and the skeleton has no echo cancellation yet.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from functools import partial
from typing import AsyncIterator

logger = logging.getLogger("spine")


def _build_loop(settings, *, audio, voice: str, hold_s: float):
    from src.spine.llm import resolve_llm, stream_chat
    from src.spine.loop import SpineLoop
    from src.spine.sentences import sentences
    from src.spine.stt import Transcript, transcribe
    from src.spine.tts import synthesise
    from src.spine.turn import TurnAssembler
    from src.spine.vad import EnergyVAD, loud_ms

    cfg = resolve_llm(settings)

    # Shorter than a spoken word: don't pay Groq to hallucinate on it.
    min_speech_ms = 240

    async def _stt(pcm: bytes):
        if loud_ms(pcm) < min_speech_ms:
            return Transcript(text="", language=settings.groq_language or "uk")
        return await transcribe(
            pcm,
            api_key=settings.groq_api_key or "",
            language=(settings.groq_language or "uk"),
        )

    def _chat(messages: list[dict]) -> AsyncIterator[str]:
        return stream_chat(messages, cfg)

    def _tts(text: str) -> AsyncIterator[bytes]:
        return synthesise(text, voice=voice)

    return SpineLoop(
        audio=audio,
        vad=EnergyVAD(),
        assembler=TurnAssembler(hold_s=hold_s),
        transcribe=_stt,
        stream_chat=_chat,
        split_sentences=sentences,
        synthesise=_tts,
    )


async def _amain(args: argparse.Namespace) -> int:
    from pathlib import Path

    from dotenv import load_dotenv

    from src.config import load_settings

    # Keys live in ~/.heare/.env; load_settings() only reads os.environ.
    load_dotenv(Path.home() / ".heare" / ".env", override=False)
    settings = load_settings()
    if not settings.groq_api_key and not args.text and not args.check:
        print("no GROQ_API_KEY — the ear is unavailable", file=sys.stderr)
        return 1

    # Not settings.tts_voice: the deployment default there is an English
    # voice, and Edge TTS renders Cyrillic on an English voice as silence.
    voice = args.voice or "uk-UA-PolinaNeural"

    if args.check:
        loop = _build_loop(settings, audio=None, voice=voice, hold_s=args.hold)
        print("ok  settings, llm, stt, tts, vad, turn, loop wired")
        print(f"ok  voice={voice}  stt_lang={settings.groq_language or 'uk'}")
        print("\nready — run without --check to open the microphone")
        return 0

    if args.text:
        audio = None
        if not args.no_speak:
            from src.spine.audio_io import AudioIO

            audio = AudioIO()
            await audio.start()
        loop = _build_loop(settings, audio=audio, voice=voice, hold_s=args.hold)
        reply = await loop.respond(args.text, speak=audio is not None)
        print(reply)
        if audio is not None:
            await audio.stop()
        return 0

    from src.spine.audio_io import AudioIO

    audio = AudioIO()
    await audio.start()
    loop = _build_loop(settings, audio=audio, voice=voice, hold_s=args.hold)
    print("spine: слухаю (Ctrl+C — вихід)")
    try:
        await loop.run()
    finally:
        await audio.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="heare spine — pipecat-free skeleton")
    parser.add_argument("--check", action="store_true", help="wire everything, open nothing")
    parser.add_argument("--text", type=str, default="", help="one text turn instead of the microphone")
    parser.add_argument("--no-speak", action="store_true", help="with --text: print only, no TTS")
    parser.add_argument("--voice", type=str, default="", help="Edge TTS voice override")
    parser.add_argument("--hold", type=float, default=1.0, help="seconds of quiet that end a turn")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("\nspine: зупинено")
        return 0


if __name__ == "__main__":
    sys.exit(main())
