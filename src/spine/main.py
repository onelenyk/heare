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
from pathlib import Path
from functools import partial
from typing import AsyncIterator

logger = logging.getLogger("spine")


def _wake_phrases(settings) -> list[str]:
    """Phrase variants from src/pipeline/wake.py, loaded by file path.

    The module itself is framework-free, but importing it as
    src.pipeline.wake runs src/pipeline/__init__.py — which imports
    pipecat. Loading by path keeps the spine pipecat-free.
    """
    import importlib.util
    from pathlib import Path as _P

    path = _P(__file__).resolve().parent.parent / "pipeline" / "wake.py"
    spec = importlib.util.spec_from_file_location("_heare_wake", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # People call the assistant by its generated name, not only by the
    # configured wake word; wake_phrases parses the name from a
    # "You are <Name>" persona line, so hand it one.
    name = ""
    try:
        import json as _json

        ident = _json.loads(_P(settings.identity_file).read_text("utf-8"))
        name = str(ident.get("name") or "")
    except Exception:
        pass
    return mod.wake_phrases(settings, persona=f"You are {name}" if name else "")


async def _build_loop(settings, *, audio, voice: str, hold_s: float,
                      full: bool = True):
    """Wire the conductor. full=False builds the bare chat skeleton
    (no wake/tools/persistence) — used by --text and quick checks."""
    from datetime import datetime

    from src.spine.llm import resolve_llm, stream_chat, stream_chat_events
    from src.spine.loop import SpineLoop
    from src.spine.sentences import sentences
    from src.spine.stt import Transcript, transcribe
    from src.spine.tts import synthesise
    from src.spine.turn import TurnAssembler
    from src.spine.vad import EnergyVAD, loud_ms
    from src.spine.voicing import pick_voice

    cfg = resolve_llm(settings)

    # Shorter than a spoken word: don't pay Groq to hallucinate on it.
    min_speech_ms = 240

    usage = None
    if full:
        from src.spine.usage import SpineUsage

        usage = SpineUsage(settings.db_path)

    async def _stt(pcm: bytes):
        if loud_ms(pcm) < min_speech_ms:
            return Transcript(text="", language=settings.groq_language or "uk")
        if usage is not None:
            await asyncio.to_thread(usage.stt, len(pcm) / 32000.0)
        return await transcribe(
            pcm,
            api_key=settings.groq_api_key or "",
            language=(settings.groq_language or "uk"),
        )

    def _chat(messages: list[dict]) -> AsyncIterator[str]:
        return stream_chat(messages, cfg)

    def _tts(text: str) -> AsyncIterator[bytes]:
        # The reply's script picks the voice: Cyrillic on an English
        # voice is silence. An explicit --voice wins.
        chosen = voice or pick_voice(text, fallback_lang="uk")
        return synthesise(text, voice=chosen)

    loop = SpineLoop(
        audio=audio,
        vad=EnergyVAD(),
        assembler=TurnAssembler(hold_s=hold_s),
        transcribe=_stt,
        stream_chat=_chat,
        split_sentences=sentences,
        synthesise=_tts,
        usage=usage,
    )

    if not full:
        return loop

    from src.memory.sqlite_backend import SQLiteBackend
    from src.spine.persist import SpinePersistence
    from src.spine.prompt import build_system_prompt, load_persona
    from src.spine.tools import VoiceToolbox
    from src.spine.wake import WakeGate

    if audio is not None:
        from src.spine.aec import SpineAEC

        aec = SpineAEC()
        loop.aec = aec
        logger.info("aec active: %s", aec.active)

    memory = SQLiteBackend(db_path=settings.db_path)
    await memory.initialize()
    try:
        return await _wire_full(loop, settings, cfg, memory)
    except Exception:
        # An unclosed aiosqlite worker thread is non-daemon: without
        # this, a failed build hangs the interpreter at exit with its
        # stdout still buffered — a silent, eternal --check.
        await memory.close()
        raise


async def _wire_full(loop, settings, cfg, memory):
    from datetime import datetime

    from src.spine.llm import stream_chat_events
    from src.spine.persist import SpinePersistence
    from src.spine.prompt import build_system_prompt, load_persona
    from src.spine.role_session import RoleManager
    from src.spine.roles import RoleLoader, is_end_trigger, match_trigger
    from src.spine.tools import VoiceToolbox
    from src.spine.wake import WakeGate

    async def _deliver(text: str) -> None:
        await loop.inject(text)

    # -- the role platform --------------------------------------------
    role_paths = [
        Path(__file__).resolve().parent.parent.parent / "roles",
        Path.home() / ".heare" / "roles",
    ]
    loop.roles = RoleLoader(role_paths).load()
    loop.role_manager = RoleManager()
    loop.trigger_match = match_trigger
    loop.end_match = is_end_trigger
    logger.info("roles loaded: %s", ", ".join(sorted(loop.roles)) or "none")

    artifacts_dir = Path(settings.workspace_dir) / "artifacts"

    def _save_artifact(role_name: str, md: str) -> str:
        from datetime import datetime as _dt

        # Models love wrapping a requested markdown document in a code
        # fence; the file must be the document, not a quote of it.
        text = md.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3].rstrip() + "\n"

        artifacts_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.now().strftime("%Y-%m-%d-%H%M")
        path = artifacts_dir / f"{stamp}-{role_name}.md"
        path.write_text(text, "utf-8")
        return str(path)

    loop.save_artifact = _save_artifact

    class _RoleSessionState:
        """Duck-typed session_state for the Hands mode gate: the active
        role's deny_tools become a live ModeProfile. No role → None
        profile is not allowed by mode_gate_refusal, so fall back to the
        permissive default profile."""

        @property
        def profile(self):
            from src.agent.modes import MODE_PROFILES, ModeProfile

            active = loop.role_manager.active if loop.role_manager else None
            if active is None or not getattr(active, "deny_tools", ()):
                return MODE_PROFILES["ambient"]
            return ModeProfile(
                name=f"role:{active.name}",
                denied_tool_patterns=tuple(active.deny_tools),
                voice_muted=getattr(active, "channel", "voice") == "log",
            )

    role_session_state = _RoleSessionState()

    def _hands_factory(s):
        from src.agent.hands import Hands

        # The live session_state makes the active role's deny_tools an
        # enforced gate inside the worker, not a prompt suggestion.
        return Hands(s, session_state=role_session_state)

    loop.toolbox = VoiceToolbox(
        settings, memory, _deliver, hands_factory=_hands_factory
    )
    loop.stream_events = lambda messages, tools: stream_chat_events(
        messages, cfg, tools=tools
    )

    loop.wake = WakeGate(
        _wake_phrases(settings),
        window_s=getattr(settings, "wake_window_seconds", 45.0),
        required=getattr(settings, "wake_required", True),
    )

    persist = SpinePersistence(settings.db_path)
    loop.persist = persist

    persona = load_persona(settings)

    async def _make_prompt() -> str:
        exchanges = await asyncio.to_thread(persist.recent_exchanges, 4)
        query = loop.history[-1]["content"] if loop.history else ""
        memory_block = ""
        try:
            memory_block = await memory.context(query=query, limit=3)
        except Exception:
            logger.debug("memory context failed (non-fatal)")
        # An active role layers its behavior right after the persona —
        # stable for the whole session, so the prefix cache only resets
        # on role switches, not on every turn.
        persona_block = persona
        active = loop.role_manager.active if loop.role_manager else None
        if active is not None and getattr(active, "prompt", ""):
            persona_block = (
                f"{persona}\n\nЗараз ти в ролі «{active.name}».\n{active.prompt}"
            )
        return build_system_prompt(
            persona=persona_block,
            memory_block=memory_block or "",
            exchanges=exchanges,
            now=datetime.now(),
        )

    loop.make_system_prompt = _make_prompt
    # Without closing these, the aiosqlite worker thread (non-daemon)
    # keeps the interpreter alive after main returns — the process hangs
    # with its stdout still buffered.
    loop._closers = [memory.close, persist.close]
    if loop.usage is not None:
        loop._closers.append(loop.usage.close)
    return loop


async def _close_loop(loop) -> None:
    for closer in getattr(loop, "_closers", []):
        try:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.debug("closer failed (non-fatal)", exc_info=True)


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

    # Empty means "pick per reply text" (voicing.pick_voice): Edge TTS
    # renders Cyrillic on an English voice as silence, so the reply's
    # script decides. An explicit --voice overrides everything.
    voice = args.voice

    if args.check:
        loop = await _build_loop(
            settings, audio=None, voice=voice, hold_s=args.hold, full=True
        )
        aec_state = "n/a (no audio)" if loop.aec is None else loop.aec.active
        print("ok  settings, llm, stt, tts, vad, turn, loop wired")
        print(f"ok  wake={loop.wake is not None}  tools={loop.toolbox is not None}"
              f"  persist={loop.persist is not None}  aec={aec_state}")
        print(f"ok  voice={voice or 'за мовою відповіді'}  "
              f"stt_lang={settings.groq_language or 'uk'}")
        print("\nready — run without --check to open the microphone")
        await _close_loop(loop)
        return 0

    if args.text:
        audio = None
        if not args.no_speak:
            from src.spine.audio_io import AudioIO

            audio = AudioIO()
            await audio.start()
        loop = await _build_loop(
            settings, audio=audio, voice=voice, hold_s=args.hold, full=False
        )
        reply = await loop.respond(args.text, speak=audio is not None)
        print(reply)
        if audio is not None:
            await audio.stop()
        await _close_loop(loop)
        return 0

    from src.spine.audio_io import AudioIO

    audio = AudioIO()
    await audio.start()
    loop = await _build_loop(
        settings, audio=audio, voice=voice, hold_s=args.hold, full=True
    )
    duplex = "повний дуплекс" if loop._duplex else "напівдуплекс"
    print(f"spine: слухаю ({duplex}, wake={'on' if loop.wake else 'off'}; "
          f"Ctrl+C — вихід)")
    try:
        await loop.run()
    finally:
        await audio.stop()
        await _close_loop(loop)
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
