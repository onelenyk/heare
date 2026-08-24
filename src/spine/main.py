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
    from src.spine.wake_phrases import own_name, wake_phrases

    # The name, handed over as a name. This used to read identity.json
    # here, wrap the result in «You are {name}» and pass that along to be
    # parsed back out by a regex — two modules agreeing on a format
    # neither of them ever writes.
    return wake_phrases(settings, name=own_name(settings))


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
                      full: bool = True, without: str = "", state=None):
    """Wire the conductor. full=False builds the bare chat skeleton
    (no wake/tools/persistence) — used by --text and quick checks.

    ``state`` is the daemon's State store. Passing it is what lets the
    engine see what the assistant is doing right now; without it every
    judgement about the present was made from half the facts.
    """
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
    loop.state = state
    loop.hear_all = bool(features["hear_all"])

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


async def _at_the_keyboard() -> float:
    """Seconds since a key or the mouse was last touched.

    Deliberately outside the watcher's switch. Everything in
    ``environment.py`` is about *what* you are doing and is off until
    asked for; this answers only whether anyone is at the desk, which is
    what keeps the engine from talking to an empty room — and, more
    often, from staying silent through the hour someone works without
    saying a word.

    It reads no content, needs no permission and writes nothing down.
    One 16 ms subprocess, on a five-second tick.
    """
    from src.spine.environment import _idle_seconds

    return await asyncio.to_thread(_idle_seconds)


def _worth_saying(cfg_of):
    """The model's veto, and its voice.

    ``judge`` decides whether the engine *may* speak — not mid-turn, not
    at night, not to an empty room, not too soon. It cannot decide whether
    a particular remark is worth making, and until now nothing did: every
    intent that cleared the gate was read out verbatim, exactly as stored.

    That was tolerable while the only intents were "the disk check
    finished". It is not tolerable for a watcher, whose intents are
    guesses about what you are doing — "you have been in Chrome for three
    hours" is either the most useful thing said all day or an irritation,
    and the difference is not in the sentence.

    So one question, once, after the conditions have already passed. The
    model may answer with a better sentence, or with nothing at all, and
    nothing at all is a valid and expected answer. A failure here falls
    back to raising the intent as written — the same behaviour as before
    this existed.

    Asked in the abstract, it never says yes
    ----------------------------------------
    Measured on 24 August, against the live model: the first version of
    this asked «чи варто зараз про це озватись», with a sentence in front
    of it saying most such things are worth nothing. It answered НІ to
    every one of twenty-four probes across three quite different
    phrasings — including a full disk, a five-minute production outage,
    and a reminder the person had explicitly asked for. The only thing
    that got a yes was a house fire.

    That is not restraint, it is a constant function, and it sat at the
    end of every unbidden path this assistant has: intents, judge, trust,
    dedupe, the watcher, the repeats pass. All of it ended here and was
    refused, and the log line for it — «judged not worth saying» — reads
    exactly like the feature working. It was written down on 20 August as
    evidence that it did.

    A model asked a bare question of taste answers with its prior, and
    its prior about assistants that speak unprompted is that they are a
    nuisance. What moves it is not argument — removing the discouraging
    sentence changed nothing, and so did forcing a ТАК/НІ answer — but
    worked examples. With the six below it says the five worth saying and
    refuses seven of the eight that are not, on the same probe set.
    """

    async def ask(intent, situation) -> str | None:
        from src.spine.llm import stream_chat

        prompt = (
            "Ти голосовий асистент, який живе поруч із людиною і мовчить, "
            "поки нема про що. Умови вже перевірені: зараз говорити "
            "можна — людина тут, не ніч, ти нікого не перебиваєш.\n"
            "Лишилось одне: чи це те, за що вона подякує, що ти сказав.\n"
            "Якщо так — одне коротке речення, яким ти це скажеш, без "
            "преамбул і без пояснень, чому ти вирішив сказати.\n"
            "Якщо ні — рівно: НІ\n\n"
            "Ось як це виглядає.\n\n"
            "помітив: диск заповнений на 98 відсотків\n"
            "→ Диск майже повний — скоро нічого не запишеться.\n\n"
            "помітив: надворі сонячно\n"
            "→ НІ\n\n"
            "помітив: завдання «зібрати звіт» завершилось, файл у теці "
            "report\n"
            "→ Звіт зібрався, лежить у теці report.\n\n"
            "помітив: ти відкрив той самий файл двічі\n"
            "→ НІ\n\n"
            "помітив: ти просив нагадати про потяг о шостій, зараз 17:40\n"
            "→ За двадцять хвилин потяг.\n\n"
            "помітив: у тебе тридцять вкладок\n"
            "→ НІ\n\n"
            # The repeats pass hands over a sentence that already carries
            # its own evidence, and without an example of that shape the
            # model answers with an essay on whether to say it rather
            # than with the thing to say — the same preamble the
            # summariser had to be broken of.
            "помітив: Втретє за два дні чую від тебе: треба оновити "
            "сертифікат\n"
            "→ Втретє за два дні чую про сертифікат — може, зараз?\n\n"
            f"Обставини: {situation.describe()}\n"
            f"помітив: {intent.text}\n"
            "→"
        )
        parts: list[str] = []
        async for chunk in stream_chat(
            [{"role": "user", "content": prompt}], cfg_of(), temperature=0.4
        ):
            parts.append(chunk)
        answer = "".join(parts).strip()
        if not answer or answer.upper().startswith("НІ"):
            return None
        return answer

    return ask


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
        """What is forbidden right now, asked of the one object that
        knows: the active role.

        This used to build a `ModeProfile` and fall back to the `ambient`
        mode when no role was in session — which meant the gate consulted
        a mode registry to answer a question no mode had influenced since
        the spine was written. The modes are gone; the answer is the
        role, or nothing is in force.
        """

        @property
        def policy(self):
            from src.agent.tool_gate import OPEN, ToolPolicy

            active = loop.role_manager.active if loop.role_manager else None
            if active is None or not getattr(active, "deny_tools", ()):
                return OPEN
            return ToolPolicy(
                name=f"роль «{active.name}»",
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

    # Built before the toolbox, which now needs it: `forget` erases what
    # was overheard, and a tool that cannot reach the transcripts can
    # only apologise.
    persist = SpinePersistence(settings.db_path)
    if features["persist"]:
        loop.persist = persist

    if features["tools"]:
        loop.toolbox = VoiceToolbox(
            settings, memory, _deliver,
            hands_factory=_hands_factory,
            persist=persist if features["persist"] else None,
            # Not behind features["wake"]: switching the gate off does
            # not take the name out of four thousand rows that already
            # start with it.
            names=_wake_phrases(settings),
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
            await intent_store.sweep_expired()

            from src.spine.llm import stream_chat
            from src.spine.summary import summariser

            watch = None
            if features["watcher"]:
                from src.spine.environment import EnvironmentWatch

                watch = EnvironmentWatch()

            # The second source of things worth saying, and the one that
            # reads what is already written down rather than watching
            # anything. Off by default: this is the one thing the project
            # has already deleted once for being unbearable.
            repeats = None
            if features["repeats"]:
                from src.spine.repeats import (
                    ObservationStore, Repeats, detector,
                )

                observations = ObservationStore(records.db)
                await observations.init()

                async def _summaries(since_ts: float):
                    return await asyncio.to_thread(
                        persist.recent_summaries, since_ts
                    )

                repeats = Repeats(
                    store=observations,
                    summaries=_summaries,
                    detect=detector(_cfg, stream_chat),
                )

            async def _still_hearing():
                """Ask the ear how it is doing.

                Defined here rather than in the engine because the audio front
                end belongs to the conductor, and the engine is not allowed to
                reach for a device — everything it knows arrives as a
                collaborator. This is that collaborator, and it is two attribute
                reads.
                """
                from src.spine.hearing import read

                return read(loop.audio)

            loop.engine = Engine(
                store=intent_store,
                say=loop.inject,
                state=getattr(loop, "state", None),
                persist=persist,
                jobs=records.jobs,
                ask=_worth_saying(_cfg),
                idle=_at_the_keyboard,
                hearing=_still_hearing,
                summarise=summariser(_cfg, stream_chat),
                watch=watch,
                repeats=repeats,
            )
            logger.info(
                "engine: holding intents between turns%s%s",
                " and watching the room" if watch is not None else "",
                " and noticing what repeats" if repeats is not None else "",
            )
        except Exception:  # noqa: BLE001
            logger.exception("engine: unavailable (non-fatal)")


    # Whether it speaks first is a fact about the wiring, not about the
    # identity — and the persona has to say the true one. `loop.engine`
    # is what actually holds intents between turns; if it did not build,
    # nothing here speaks unbidden whatever the switches say.
    persona = load_persona(settings, speaks_first=loop.engine is not None)

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
