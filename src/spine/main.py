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

# How long after the assistant speaks a bare «дякую» still counts as a
# person answering rather than Whisper filling silence.
COURTESY_WINDOW_SECS = 25.0


def _wake_phrases(settings) -> list[str]:
    """Phrase variants from src/spine/wake_phrases.py.

    This used to be loaded by file path out of the old engine's tree: the
    module there is framework-free, but importing it as a package member
    runs that package's __init__ — which imports pipecat. The table now
    lives in the spine's own tree, so a plain import keeps the spine
    pipecat-free.
    """
    from pathlib import Path as _P

    from src.spine.wake_phrases import wake_phrases

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
    return wake_phrases(settings, persona=f"You are {name}" if name else "")


def _live_cfg(settings, fallback):
    """A callable that re-resolves the provider on every use.

    Resolved once at startup, switching provider or model in the dashboard
    would take effect on the next restart — which, for something meant to
    be running all day, means never. Resolution is a dict lookup and a few
    getattrs against a Settings object the API mutates in place: cheaper
    than the DNS lookup that follows it.

    It can only fail by every key having gone missing, and in that case the
    config we already hold is still the better answer than an exception on
    the conversational path.
    """
    from src.spine.llm import resolve_llm

    def _cfg():
        try:
            return resolve_llm(settings)
        except RuntimeError:
            return fallback

    return _cfg


async def _build_loop(settings, *, audio, voice: str, hold_s: float,
                      full: bool = True, without: str = ""):
    """Wire the conductor. full=False builds the bare chat skeleton
    (no wake/tools/persistence) — used by --text and quick checks."""
    from datetime import datetime

    import time

    from src.spine.hallucinations import is_junk
    from src.spine.llm import resolve_llm, stream_chat, stream_chat_events
    from src.spine.loop import SpineLoop
    from src.spine.sentences import sentences
    from src.spine.stt import Transcript, transcribe
    from src.spine.tts import synthesise
    from src.spine.turn import TurnAssembler
    from src.spine.vad import EnergyVAD, loud_ms
    from src.spine.voicing import pick_voice

    from src.spine.features import describe, losses, resolve as resolve_features

    features = resolve_features(settings, without)
    # The engine's own answer to "what is actually running right now",
    # which is not the same as what the config asks for: env overrides
    # and --without win, and a subsystem can fail to build.
    loop_features = dict(features)
    logger.info("features: %s", describe(features))
    for line in losses(features):
        logger.warning("switched off — %s", line)

    cfg = resolve_llm(settings)
    _cfg = _live_cfg(settings, cfg)

    # Shorter than a spoken word: don't pay Groq to hallucinate on it.
    min_speech_ms = 240

    usage = None
    if full and features["usage"]:
        from src.spine.usage import SpineUsage

        usage = SpineUsage(settings.db_path)

    async def _stt(pcm: bytes):
        lang = settings.groq_language or "uk"
        if loud_ms(pcm) < min_speech_ms:
            return Transcript(text="", language=lang)
        if usage is not None:
            await asyncio.to_thread(usage.stt, len(pcm) / 32000.0)
        result = await transcribe(
            pcm, api_key=settings.groq_api_key or "", language=lang
        )
        # Loudness alone cannot tell a cough from a word; the text can.
        spoke_recently = (
            time.time() - getattr(loop, "last_spoke_ts", 0.0)
        ) < COURTESY_WINDOW_SECS
        if is_junk(result.text, agent_spoke_recently=spoke_recently):
            logger.info("dropped hallucination: %r", result.text[:60])
            return Transcript(text="", language=result.language)
        return result

    def _chat(messages: list[dict]) -> AsyncIterator[str]:
        return stream_chat(messages, _cfg())

    def _tts(text: str) -> AsyncIterator[bytes]:
        # The reply's script picks the voice: Cyrillic on an English
        # voice is silence. An explicit --voice wins.
        chosen = voice or pick_voice(text, fallback_lang="uk")
        return synthesise(text, voice=chosen)

    loop = SpineLoop(
        audio=audio,
        vad=EnergyVAD(
            stop_ms=int(getattr(settings, "spine_vad_stop_ms", 800))
        ),
        assembler=TurnAssembler(
            hold_s=hold_s,
            continuation_hold_s=getattr(
                settings, "spine_turn_continuation_hold_seconds", hold_s * 2
            ),
        ),
        transcribe=_stt,
        stream_chat=_chat,
        split_sentences=sentences,
        synthesise=_tts,
        usage=usage,
    )
    loop.features = loop_features

    if not full:
        return loop

    from src.memory.sqlite_backend import SQLiteBackend
    from src.spine.persist import SpinePersistence
    from src.spine.prompt import build_system_prompt, load_persona
    from src.spine.tools import VoiceToolbox
    from src.spine.wake import WakeGate

    if audio is not None and features["aec"]:
        from src.spine.aec import SpineAEC

        aec = SpineAEC()
        loop.aec = aec
        # The speaker owns the far-end reference: pushing it here, after
        # the mute check inside play(), is the only way the canceller
        # hears exactly what the room heard. See audio_io.play().
        audio.far_sink = aec
        logger.info("aec active: %s", aec.active)

    memory = None
    if features["memory"]:
        memory = SQLiteBackend(db_path=settings.db_path)
        await memory.initialize()
    try:
        return await _wire_full(loop, settings, cfg, memory, features)
    except Exception:
        # An unclosed aiosqlite worker thread is non-daemon: without
        # this, a failed build hangs the interpreter at exit with its
        # stdout still buffered — a silent, eternal --check.
        if memory is not None:
            await memory.close()
        raise


async def _wire_full(loop, settings, cfg, memory, features):
    _cfg = _live_cfg(settings, cfg)
    from datetime import datetime

    from src.spine.llm import stream_chat_events
    from src.spine.persist import SpinePersistence
    from src.spine.prompt import build_system_prompt, load_persona
    from src.spine.role_session import RoleManager
    from src.spine.roles import RoleLoader, is_end_trigger, match_trigger
    from src.spine.tools import (
        SpineRecords, VoiceToolbox, make_hands_factory, open_spine_records,
    )
    from src.spine.wake import WakeGate

    async def _deliver(text: str) -> None:
        await loop.inject(text)

    # -- the role platform --------------------------------------------
    role_paths = [
        Path(__file__).resolve().parent.parent.parent / "roles",
        Path.home() / ".heare" / "roles",
    ]
    from src.spine.role_flow import RoleFlow

    # The conductor asks the flow whether a turn belongs to a session; with
    # no flow adopted it never asks, and every role is inert.
    if features["roles"]:
        loop.adopt_role_flow(RoleFlow())
    loop.roles = RoleLoader(role_paths).load()
    loop.role_manager = RoleManager()
    loop.trigger_match = match_trigger
    loop.end_match = is_end_trigger
    logger.info("roles loaded: %s", ", ".join(sorted(loop.roles)) or "none")

    from src.spine.artifacts import save_artifact

    artifacts_dir = Path(settings.workspace_dir) / "artifacts"
    loop.save_artifact = partial(save_artifact, artifacts_dir)

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

    # The live session_state makes the active role's deny_tools an
    # enforced gate inside the worker, not a prompt suggestion. The MCP
    # bridge is looked up at call time (``loop.mcp``) because the servers
    # connect after the loop is wired — see src/daemon/spine_engine.py.
    # Delegated work that outlives the turn — and the process. Without
    # these the jobs and actions tables stayed empty on this engine: a
    # restart mid-job left the user waiting for an answer that could
    # never arrive, and the dashboard could show what was said but never
    # what was done.
    records = (
        await open_spine_records(settings.db_path)
        if features["persist"]
        else SpineRecords()
    )
    loop.records = records
    if records.stranded:
        logger.info("jobs: %d interrupted by a restart", len(records.stranded))

    def _long_running(label: str):
        cb = getattr(loop, "on_long_running", None)
        return cb(label) if cb is not None else None

    _hands_factory = make_hands_factory(
        session_state=role_session_state,
        mcp_provider=lambda: getattr(loop, "mcp", None),
        jobs=records.jobs,
        conversation_manager=records.actions,
        on_long_running=_long_running,
    )

    if features["tools"]:
        loop.toolbox = VoiceToolbox(
            settings, memory, _deliver, hands_factory=_hands_factory
        )
        loop.stream_events = lambda messages, tools: stream_chat_events(
            messages, _cfg(), tools=tools
        )

    if features["wake"]:
        loop.wake = WakeGate(
            _wake_phrases(settings),
            window_s=getattr(settings, "wake_window_seconds", 45.0),
            required=getattr(settings, "wake_required", True),
        )

    persist = SpinePersistence(settings.db_path)
    if features["persist"]:
        loop.persist = persist
    # Mirror role sessions to the DB on the CLI path too, so a session
    # cut short by a restart is recoverable there as well.
    if loop.role_manager is not None:
        loop.role_manager.persist = persist

    # The engine: the only thing here that outlives a turn. Everything
    # else in this file wires a conversation; this holds what is left
    # over when one ends — what it means to raise, and whether now is
    # the moment. Injected like the rest, so the conductor stays a
    # conductor.
    loop.engine = None
    if features["engine"] and records.db is not None:
        try:
            from src.spine.engine import Engine
            from src.spine.intents import IntentStore

            intent_store = IntentStore(records.db)
            await intent_store.init()
            await intent_store.sweep_stale()

            loop.engine = Engine(
                store=intent_store,
                say=loop.inject,
                state=getattr(loop, "state", None),
                persist=persist,
                jobs=records.jobs,
            )
            logger.info("engine: holding intents between turns")
        except Exception:  # noqa: BLE001
            logger.exception("engine: unavailable (non-fatal)")


    persona = load_persona(settings)

    async def _make_prompt() -> str:
        exchanges = await asyncio.to_thread(persist.recent_exchanges, 4)
        query = loop.history[-1]["content"] if loop.history else ""
        memory_block = ""
        try:
            # context() returns MemoryEntry objects; handing the list
            # straight to the prompt put a Python repr of the dataclass
            # into the system message — id, confidence, timestamps and
            # all — instead of the fact itself.
            entries = (
                await memory.context(query=query, limit=3)
                if memory is not None
                else []
            )
            memory_block = "\n".join(
                f"- {getattr(e, 'content', e)}" for e in entries or []
            )
        except Exception:
            logger.debug("memory context failed (non-fatal)")
        # An active role layers its behavior right after the persona —
        # stable for the whole session, so the prefix cache only resets
        # on role switches, not on every turn.
        # What the worker can reach through MCP. The voice agent has three
        # verbs and cannot call any of it — so the block is framed as a
        # reason to delegate, not as a menu. Static for the life of the
        # process (servers connect once at boot), so it sits in the
        # cacheable part of the prompt, right after the persona.
        mcp_block = ""
        bridge = getattr(loop, "mcp", None)
        if bridge is not None:
            try:
                block = bridge.prompt_block()
            except Exception:
                logger.debug("mcp prompt block failed (non-fatal)")
                block = ""
            if block:
                mcp_block = (
                    "Це вміє твій виконавець, не ти: сам ці інструменти не "
                    "викликай, а коли треба — доручай через delegate. Кажи "
                    "«зроблю», а не «не налаштовано».\n" + block
                )
        persona_block = persona
        active = loop.role_manager.active if loop.role_manager else None
        if active is not None and getattr(active, "prompt", ""):
            persona_block = (
                f"{persona}\n\nЗараз ти в ролі «{active.name}».\n{active.prompt}"
            )
        situation_block = ""
        if loop.engine is not None:
            try:
                situation_block = await loop.engine.prompt_block()
            except Exception:
                logger.debug("engine prompt block failed (non-fatal)")

        return build_system_prompt(
            persona=persona_block,
            mcp_block=mcp_block,
            memory_block=memory_block or "",
            exchanges=exchanges,
            situation_block=situation_block,
            now=datetime.now(),
        )

    loop.make_system_prompt = _make_prompt
    # Without closing these, the aiosqlite worker thread (non-daemon)
    # keeps the interpreter alive after main returns — the process hangs
    # with its stdout still buffered.
    # The dashboard's memories card reads this backend through the API.
    loop.memory = memory
    loop._closers = [persist.close, records.close]
    if memory is not None:
        loop._closers.append(memory.close)
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
    from src.config import load_env, load_settings

    # Keys live in ~/.heare/.env; load_settings() only reads os.environ.
    load_env()
    settings = load_settings()
    if not settings.groq_api_key and not args.text and not args.check:
        print("no GROQ_API_KEY — the ear is unavailable", file=sys.stderr)
        return 1

    # Empty means "pick per reply text" (voicing.pick_voice): Edge TTS
    # renders Cyrillic on an English voice as silence, so the reply's
    # script decides. An explicit --voice overrides everything.
    hold = args.hold or float(
        getattr(settings, "spine_turn_hold_seconds", 1.3)
    )
    voice = args.voice

    if args.check:
        loop = await _build_loop(
            settings, audio=None, voice=voice, hold_s=hold, full=True, without=args.without
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
            settings, audio=audio, voice=voice, hold_s=hold, full=False, without=args.without
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
        settings, audio=audio, voice=voice, hold_s=hold, full=True, without=args.without
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
    parser.add_argument(
        "--hold",
        type=float,
        default=0.0,
        help="seconds of quiet that end a turn (0 = value from config)",
    )
    parser.add_argument(
        "--without",
        type=str,
        default="",
        help="switch subsystems off: roles,mcp,wake,tools,memory,persist,"
             "usage,telemetry,aec (also HEARE_WITHOUT=..., HEARE_SAFE_MODE=1)",
    )
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
