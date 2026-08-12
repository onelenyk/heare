"""When a person has finished saying what they were saying.

The assistant used to answer half a sentence. Said in one breath,

    Дока, привіт. Скажи одним реченням, як ти себе почуваєш

was greeted twice and then answered — three replies to one question,
in every measured run.

Nothing in that was the model's fault. Recognition returns words per
segment, and a long segment comes back long after it began, so one
breath reached the pipeline as four transcripts spread over five
seconds. Two independent timers then decided what to do with them:

  - the gate's debounce, started by a transcript arriving
  - the turn, started by silence in the room

Whichever fired first won, and the second fragment landed in a turn that
had already closed — so it opened a new one, and the model answered
twice. Holding the transcripts longer, which is the obvious fix, only
moves the race: held past the turn's own timer, the words arrive in no
turn at all and the assistant goes silent. That was measured too.

There is one clock here, and it is the words. A turn is over when the
person has stopped speaking *and* the recogniser has stopped producing —
not a fixed interval after the room went quiet, which is a guess about
how long recognition will take.

See docs/findings/two-clocks.md.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _build_strategy_class():
    from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
        SpeechTimeoutUserTurnStopStrategy,
    )

    class SentenceUserTurnStopStrategy(SpeechTimeoutUserTurnStopStrategy):
        """End the turn once the words stop, not once the room does.

        The strategy this extends starts its countdown when voice
        activity ends and does not restart it for transcripts that arrive
        afterwards — which is every transcript, since recognition takes
        about a second. Here each one pushes the deadline back, so a
        sentence whose second half is still being recognised stays one
        turn.

        The countdown still requires silence: while the person is
        speaking there is no deadline at all, and words are simply
        collected.
        """

        async def _handle_transcription(self, frame: Any) -> None:
            self._text += frame.text
            if getattr(frame, "finalized", False):
                self._transcript_finalized = True

            # Still talking: nothing to wait for yet. The deadline is set
            # when they stop, by the VAD handler upstream of this.
            if self._vad_user_speaking:
                return

            if self._timeout_task:
                await self.task_manager.cancel_task(self._timeout_task)
                self._timeout_task = None

            timeout = self._calculate_timeout()
            self._timeout_task = self.task_manager.create_task(
                self._timeout_handler(timeout), f"{self}::_timeout_handler"
            )
            # Let it be scheduled before anything else looks at it.
            await asyncio.sleep(0)

    return SentenceUserTurnStopStrategy


_cls: type | None = None


def sentence_turn_stop_strategy(*, user_speech_timeout: float):
    """The strategy, built lazily so importing this costs nothing.

    ``user_speech_timeout`` is how long to wait after the last word
    before answering. It is the only latency dial left in the turn: with
    the two timers collapsed into one, there is nothing else racing it.
    """
    global _cls
    if _cls is None:
        _cls = _build_strategy_class()
    return _cls(user_speech_timeout=user_speech_timeout)


__all__ = ["sentence_turn_stop_strategy"]
