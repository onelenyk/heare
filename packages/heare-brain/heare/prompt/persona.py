"""Who the assistant is and how it talks: the text at the head of every prompt.

Kept beside the core, not in it. The core reads this through one
function; the rules themselves are a file a person can edit
(``prompts/voice_rules.txt``), and the name is the one the old app
already answers to (``~/.heare/identity.json``).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from heare.laws import TEXT

RULES = Path(__file__).resolve().parent / "prompts" / "voice_rules.txt"
IDENTITY = Path.home() / ".heare" / "identity.json"
_DAYS = ("понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя")


def name() -> str:
    try:
        return str(json.loads(IDENTITY.read_text(encoding="utf-8")).get("name") or "")
    except (OSError, ValueError):
        return ""


def names() -> frozenset[str]:
    """The name as a word of a command must not count: «Дока, стоп» is «стоп»."""
    return frozenset(w for w in (name().lower(), "дока", "heare") if w)


async def prompt() -> str:
    """Read again on every turn: a saved rule counts from the next reply."""
    try:
        rules = RULES.read_text(encoding="utf-8").strip()
    except OSError:
        rules = ""
    parts = [rules] if rules else []
    if called := name():
        parts.append(f"Your name is {called}.")
    # The laws are commands, so they stand in the prompt with the other
    # commands — not inside the profile, which is framed as data.
    if TEXT.strip():
        parts.append("The laws you act under:\n" + TEXT.strip())
    # The register of speech follows the language the person speaks: the
    # prompt is English, the answer is not.
    parts.append("Speak of yourself, and address the person, the way their "
                 "language expects.")
    # What it is, so it does not have to guess — it once answered «у мене
    # немає доступу до мікрофона», and once built a music recogniser when
    # asked not to react to music.
    parts.append(
        "You hear the person through a microphone and answer with your voice; "
        "you have a canvas — a screen in front of the person; real work with "
        "files, code and programs you hand to an agent. When music or video is "
        "playing nearby, you answer only when called by name — that already "
        "works; nothing needs building for it. If asked to change how you "
        "behave and you have no action for it, say plainly that you cannot — "
        "do not delegate."
    )
    # A model does not know the hour unless it is told.
    now = datetime.now()
    parts.append(f"It is {_DAYS[now.weekday()]}, {now:%d.%m.%Y}, {now:%H:%M}.")
    return "\n\n".join(parts)


# When to speak without being called by name. Examples in both
# directions, because a rule alone was read as "never": the old app's
# judge turned everything down for a month. The word МОВЧУ is the core's.
JUDGE = (
    "The person said the next line in the room, not addressing you by name. "
    "Decide for yourself whether it is worth saying anything.\n"
    "Answer if: it is a question or a request you can answer or carry out; the "
    "person is clearly talking to you («привіт», «ти мене чуєш?», «а що ти про "
    "це думаєш?»); they say they have to do or not forget something, and you "
    "can offer help in one sentence.\n"
    "Stay silent if: it is a conversation with someone else or on the phone; "
    "thinking aloud, exclamations, swearing, single words with no meaning "
    "(«так», «ну», «угу», «блін»); sound from a video; you have nothing to add.\n"
    "Examples. «Чутно мене чи ні?» — answer. «Котра зараз година?» — answer. "
    "«Треба не забути подзвонити мамі» — offer to remind. «Так, я тобі "
    "передзвоню ввечері» — stay silent. «Ну таке» — stay silent. «Передай "
    "сіль» — stay silent.\n"
    "If you decide to stay silent, write exactly one word: МОВЧУ. Otherwise "
    "answer as usual, briefly."
)
