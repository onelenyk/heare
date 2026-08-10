"""Entry point.

    uv run python -m src.core.main --check     # wire everything, open nothing
    uv run python -m src.core.main             # listen and talk
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logger = logging.getLogger("core")


async def _serve(
    check: bool, probe: bool = False, aec: bool = True, gate: bool = False
) -> int:
    from src.core.pipeline import build
    from src.core.settings import load_settings
    from src.core.state import State

    settings = load_settings()
    if not settings.groq_api_key:
        print("no GROQ_API_KEY — speech recognition is unavailable", file=sys.stderr)
        return 1
    if not (settings.deepseek_api_key or settings.zai_api_key):
        print("no LLM key configured", file=sys.stderr)
        return 1

    from src.memory.sqlite_backend import SQLiteBackend

    memory = SQLiteBackend(db_path=settings.db_path)
    await memory.initialize()

    task, _llm = await build(
        settings,
        State(),
        memory,
        probe_audio=probe,
        use_aec=aec,
        use_gate=gate,
    )

    if check:
        print("ok  settings, memory, pipeline built")
        print(f"ok  voice={settings.tts_voice}  stt_lang={settings.groq_language}")
        print("\nready — run without --check to open the microphone")
        await memory.close()
        return 0

    from pipecat.pipeline.runner import PipelineRunner

    print("listening — ctrl-c to stop")
    try:
        await PipelineRunner(handle_sigint=True).run(task)
    finally:
        await memory.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="build everything, open nothing")
    p.add_argument(
        "--probe-audio",
        action="store_true",
        help="log mic level before/after each filter (why is barge-in silent)",
    )
    p.add_argument(
        "--no-aec",
        action="store_true",
        help="control: drop echo cancellation entirely",
    )
    p.add_argument(
        "--gate",
        action="store_true",
        help="also run the correlation echo gate (it mutes during playback)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(
        _serve(args.check, args.probe_audio, not args.no_aec, args.gate)
    )


if __name__ == "__main__":
    sys.exit(main())
