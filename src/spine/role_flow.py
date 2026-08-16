"""The role platform's policy — what a session does, not how it sounds.

A role session is a mode the conversation enters on a spoken trigger and
leaves on a spoken end phrase: while it lasts the quiet channels record
instead of answering, and on the way out one LLM call turns the whole
session into an artifact and one short spoken summary.

That is a policy, not a conductor concern, so it lives here. This file
follows the same architecture rule as loop.py — **it imports no sibling
module**: every collaborator is an injected field, which is what lets a
whole session be played out in tests with fakes.

The seam (all optional; None means "that part is not wired"):

* ``role_manager`` — owns the active session:
  ``.active`` / ``.start(role) -> ack`` / ``.note_turn(turn_id)`` /
  ``await .finish(exchanges=, complete=) -> artifact`` and, optionally,
  ``.note_artifact(path)``.
* ``roles`` — the loaded registry, name -> role.
* ``trigger_match(text, roles) -> role | None`` — start phrases.
* ``end_match(text, role) -> bool`` — end phrases.
* ``save_artifact(role_name, markdown) -> path`` — the document.
* ``hint_sink(hint, question)`` — async; the hints channel's dashboard.
* ``say(text)`` — async; speaks a service phrase *now*, outside the LLM
  flow (the conductor's mouth: it also unmutes, drains and restores).
* ``persist_turn(text) -> turn_id | None`` — async; logs a heard turn on
  the quiet channels. Returning None means "not persisted".
* ``stream_chat(messages) -> AsyncIterator[str]`` — the model, used for
  hints and for building the artifact.
* ``get_muted()`` / ``set_muted(bool)`` — the speaker's mute. A ``log``
  channel mutes it for the whole session, and lifts it around the two
  phrases that must be heard anyway (the ack and the summary).

State the outside world watches (mirrored by the conductor under its old
names, which the daemon and CLI already read): ``log`` — the session's
exchanges, ``finishing`` — an artifact is being written right now,
``ended_ts`` — when the last session closed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("spine.role_flow")

# How long after a session closes an end phrase is still just the user
# pressing the button again.
END_PHRASE_GRACE_SECS = 90.0

FINISHING_LINE = "Хвилинку, збираю підсумок."

HINT_INSTRUCTION = (
    "Відповідай ТІЛЬКИ підказкою: 3-5 коротких пунктів "
    "плану відповіді, без вступу і пояснень."
)


@dataclass
class RoleFlow:
    """Start and end triggers, the quiet channels, the artifact.

    See the module docstring for the injected seam. Nothing here touches
    audio, sqlite or the network directly — it asks the callbacks.
    """

    role_manager: Any = None   # .start/.finish/.active/.note_turn
    roles: dict = field(default_factory=dict)
    trigger_match: Any = None  # (text, roles) -> role | None
    end_match: Any = None      # (text, role) -> bool
    save_artifact: Any = None  # (role_name, md) -> str path
    hint_sink: Any = None      # async (hint, question) -> None
    say: Any = None            # async (text) -> None
    persist_turn: Any = None   # async (text) -> turn_id | None
    stream_chat: Any = None    # (messages) -> AsyncIterator[str]
    get_muted: Any = None      # () -> bool
    set_muted: Any = None      # (bool) -> None
    log: list = field(default_factory=list)  # session exchanges
    # Building an artifact is a whole LLM call over a whole session —
    # seconds, sometimes tens of them. The flag makes that wait visible
    # and keeps the conversation from answering in the meantime.
    finishing: bool = False
    ended_ts: float = 0.0
    # Strong references to the work spawned off the turn loop. A bare
    # create_task() is garbage-collectable mid-flight, and its exception
    # is never retrieved — the session would end in silence with nothing
    # in the log to say why.
    _tasks: set = field(default_factory=set, repr=False)

    # -- what the conductor asks ---------------------------------------

    @property
    def in_session(self) -> bool:
        """True while a role owns the conversation. An active session
        hears everything — a meeting secretary that ignored the
        unaddressed room would have nothing to summarise — so the
        conductor lets these turns past the wake gate."""
        return self.role_manager is not None and bool(self.role_manager.active)

    async def handle(self, turn: str) -> bool:
        """Role lifecycle and the log channel. True when the turn is
        consumed here and must not go to the normal respond() path."""
        if self.role_manager is None:
            return False
        active = self.role_manager.active

        if self.finishing:
            # The summary is still being written; anything said now would
            # be answered by a conversation that is already over.
            logger.info("role finishing — turn held: %r", turn[:60])
            return True

        if active is not None and self.end_match is not None and self.end_match(
            turn, active
        ):
            # Off the turn loop: an 18-second summary must not look like
            # a dead button, and repeated «закінчили» must not queue up.
            self.finishing = True
            self._spawn(self.finish(), "role finish")
            return True

        if active is None and self._recently_ended(turn):
            logger.info("stray end phrase ignored: %r", turn[:60])
            return True

        if active is None and self.trigger_match is not None:
            role = self.trigger_match(turn, self.roles)
            if role is not None:
                ack = self.role_manager.start(role)
                self.log.clear()
                if getattr(role, "channel", "voice") == "log":
                    self._mute(True)
                await self._say(ack)
                return True

        channel = getattr(active, "channel", "voice") if active else "voice"
        if active is not None and channel in ("log", "hints"):
            # The quiet channels: everything is recorded, nothing is
            # spoken until the session ends. "hints" additionally turns
            # each heard turn into a silent prompt for the dashboard —
            # the interview prompter.
            self.log.append({"user": turn, "agent": None})
            await self._persist(turn)
            if channel == "hints" and self.hint_sink is not None:
                # Off the consuming path: a hint is useless if waiting
                # for it delays hearing the next question.
                self._spawn(self._make_hint(active, turn), "role hint")
            return True

        return False

    def note_exchange(
        self, user_text: str, reply: str, turn_id: int | None = None
    ) -> None:
        """A voice-channel session (teacher, interviewer) keeps its own
        transcript for the end-of-session artifact."""
        if self.role_manager is None or not self.role_manager.active:
            return
        self.role_manager.note_turn(turn_id)
        self.log.append({"user": user_text, "agent": reply or None})

    # -- the end of a session ------------------------------------------

    async def finish(self) -> None:
        """Close the session: announce, build the artifact, speak it.

        ``finishing`` holds every turn while it is set — that is its job —
        so nothing here may leave it set. Every step below can fail on
        something outside this process (ffmpeg gone, the output device
        dead, the model refusing), and a spoken line that raised used to
        escape this coroutine with the flag still True: from then on the
        assistant heard, transcribed, paid for the transcription and
        answered nothing until a restart. So the whole body runs under one
        ``finally`` that clears the flag, closes the session and lifts the
        mute, and each spoken line is allowed to fail on its own.
        """
        active = None
        artifact = None

        async def _complete(messages: list[dict]) -> str:
            parts: list[str] = []
            async for delta in self.stream_chat(messages):
                parts.append(delta)
            return "".join(parts)

        try:
            active = self.role_manager.active
            self._mute(False)
            # Building an artifact takes one whole LLM call over a whole
            # session — measured 7 to 30 seconds. Said out loud, that is a
            # pause; unsaid, it is the assistant having died.
            if self.log:
                await self._try_say(FINISHING_LINE)
            artifact = await self.role_manager.finish(
                exchanges=list(self.log), complete=_complete
            )
        except Exception:
            logger.exception("role finish failed")
            artifact = None
        finally:
            self.log.clear()
            self.finishing = False
            self.ended_ts = time.time()
            # A session that ended on a dead speaker must not leave the
            # quiet channel's mute latched on behind it.
            self._mute(False)
            self._close_session()

        # Past this point the conversation is already free: whatever the
        # speaker does now costs words, not the assistant's ears.
        if artifact is None:
            await self._try_say("Роль завершено.")
            return
        path = ""
        if self.save_artifact is not None and artifact.full_md.strip():
            try:
                path = self.save_artifact(
                    getattr(active, "name", "роль"), artifact.full_md
                )
                # The session row is closed before the file exists, so the
                # path is stamped here or the record never points at what
                # the session produced.
                note = getattr(self.role_manager, "note_artifact", None)
                if callable(note):
                    note(path)
            except Exception:
                logger.exception("artifact save failed")
        logger.info("role artifact: %s (%d chars)", path, len(artifact.full_md))
        logger.info("say (summary): %r", artifact.spoken[:80])
        await self._try_say(artifact.spoken)

    def _close_session(self) -> None:
        """A session must not survive its own ending.

        ``role_manager.finish()`` normally deactivates the role itself;
        when it raised before it got that far, ``cancel()`` is what closes
        the row and drops the session — otherwise ``in_session`` stays
        True, every later turn walks past the wake gate into a session
        nobody is recording, and the artifact never comes.
        """
        if self.role_manager is None:
            return
        try:
            if not self.role_manager.active:
                return
            cancel = getattr(self.role_manager, "cancel", None)
            if callable(cancel):
                cancel()
                logger.warning("role session force-closed after a failed finish")
        except Exception:
            logger.exception("role session could not be closed")

    def _spawn(self, coro: Any, what: str) -> Any:
        """Run something off the turn loop, keeping a reference to it and
        reporting what it did. Bare create_task() loses both."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def _done(t: Any) -> None:
            self._tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error("%s task failed: %r", what, exc, exc_info=exc)

        task.add_done_callback(_done)
        return task

    def _recently_ended(self, turn: str) -> bool:
        """An end phrase repeated just after a session closed is a person
        pressing the button again, not a new thing to discuss."""
        if time.time() - self.ended_ts > END_PHRASE_GRACE_SECS:
            return False
        if self.end_match is None:
            return False
        return any(self.end_match(turn, r) for r in self.roles.values())

    # -- the hints channel ---------------------------------------------

    async def _make_hint(self, role: Any, turn: str) -> None:
        try:
            recent = [e["user"] for e in self.log[-6:-1]]
            context = "\n".join(f"- {t}" for t in recent)
            messages = [
                {
                    "role": "system",
                    "content": f"{getattr(role, 'prompt', '')}\n\n{HINT_INSTRUCTION}",
                },
                {
                    "role": "user",
                    "content": (
                        (f"Перед цим звучало:\n{context}\n\n" if context else "")
                        + f"Щойно прозвучало: {turn}"
                    ),
                },
            ]
            parts: list[str] = []
            async for delta in self.stream_chat(messages):
                parts.append(delta)
            hint = "".join(parts).strip()
            logger.info("hint for %r: %d chars", turn[:40], len(hint))
            if hint:
                # The heard question travels with the plan: on screen the
                # user must see what it is a plan *for*.
                await self.hint_sink(hint, turn)
        except Exception:
            logger.exception("hint generation failed (non-fatal)")

    # -- the injected edges, each one optional -------------------------

    async def _say(self, text: str) -> None:
        """A service phrase, out loud, now. A quiet channel has the
        speaker muted and still has to be heard when it opens and when
        it closes — so the mute is lifted for exactly this phrase."""
        if self.say is None:
            return
        muted = self._muted()
        self._mute(False)
        try:
            await self.say(text)
        finally:
            self._mute(muted)

    async def _try_say(self, text: str) -> None:
        """A phrase whose failure must cost only the phrase. Losing the
        mouth is a bad minute; losing the session's end is a dead
        assistant, so the words are the part that gets dropped."""
        try:
            await self._say(text)
        except Exception:
            logger.exception("service phrase not spoken (non-fatal): %r", text[:40])

    async def _persist(self, turn: str) -> None:
        if self.persist_turn is None:
            return
        try:
            turn_id = await self.persist_turn(turn)
        except Exception:
            logger.exception("role persist failed (non-fatal)")
            return
        if turn_id is not None:
            self.role_manager.note_turn(turn_id)

    def _muted(self) -> bool:
        return bool(self.get_muted()) if self.get_muted is not None else False

    def _mute(self, muted: bool) -> None:
        if self.set_muted is not None:
            self.set_muted(muted)
