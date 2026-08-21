"""What a conversation was about, in two sentences.

Twenty conversations are on record and `summary` is empty in every one.
The field has existed since the first schema; nothing ever wrote it,
because nothing ever closed a conversation — and a summary is a thing
you can only write once the thing is over.

Why it is worth one model call
------------------------------
Reading a day of talk turn by turn is expensive and slow, and it is what
anything asking "have we discussed this before" would otherwise have to
do. Reading ten summaries is neither. So this is not a feature on its
own so much as the index everything after it reads.

Two rules, both about not lying
-------------------------------
* **A short conversation gets no summary.** Two exchanges about the
  weather compress to nothing, and a model asked to summarise them will
  produce a sentence rather than admit that. An empty field is honest;
  an invented one poisons every search that comes later.

* **It says what was said, not what it means.** No advice, no "the user
  seemed frustrated", no conclusions nobody reached. The single most
  useful property of a summary you will read in a month is that you can
  trust it to be a record.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("spine.summary")

# Below this many lines there is nothing to compress, and asking anyway
# produces a fabricated sentence about a conversation that did not
# happen.
ENOUGH_LINES = 4

# How much of a long conversation reaches the model. Roughly 8 000
# characters — a couple of hours of talk — kept from both ends, because
# what a conversation turned out to be about is usually decided at its
# start and its end, and the middle is where it wandered.
HEAD_CHARS = 4000
TAIL_CHARS = 4000

# A negative instruction is not enough. Told "no preambles", the model
# opened its first real summary with "Це був запис розмови з голосовим
# асистентом…" — one of the two sentences spent saying what the reader
# already knows. One example of each does what the rule could not.
PROMPT = (
    "Ось запис розмови між людиною і голосовим асистентом.\n\n"
    "{body}\n\n"
    "Опиши двома реченнями, про що це було. Пиши як нотатку для себе — "
    "щоб через місяць можна було зрозуміти, чи тут є те, що шукаєш.\n"
    "Тільки те, що справді прозвучало: жодних порад, висновків і "
    "припущень про настрій. Якщо йшлося про кілька різних речей — "
    "назви головні.\n\n"
    "Починай одразу з теми — не з того, що це розмова.\n"
    "Погано: «Це був запис розмови з асистентом, у якій обговорювали "
    "таймаути.»\n"
    "Добре: «Таймаути у голосовому агенті: підняли до тридцяти секунд, "
    "тимчасово. Окремо — чому падає збірка застосунку.»"
)


def transcript(said: list[tuple[int, str]]) -> str:
    """The conversation as text, trimmed from the middle if long.

    Kept pure and separate so the trimming rule is a table of cases in a
    test rather than something you find out about from a bill.
    """
    lines = [
        f"{'Асистент' if agent else 'Людина'}: {text.strip()}"
        for agent, text in said
        if text and text.strip()
    ]
    body = "\n".join(lines)
    if len(body) <= HEAD_CHARS + TAIL_CHARS:
        return body
    return (
        body[:HEAD_CHARS]
        + "\n\n[…середина розмови пропущена…]\n\n"
        + body[-TAIL_CHARS:]
    )


def tidy(text: str) -> str:
    """Take the model's answer down to the note it was asked for.

    The prompt says no preamble and carries an example of each, which
    took care of the common case. It does not hold every time: on a long
    conversation the answer came back as "Ось нотатка за записом
    розмови:" followed by the actual summary in bold. Both halves of
    that are wrong for what this field is — an index other things read
    and search. A heading wastes the first of two sentences, and `**`
    is markup in a record nothing renders.

    So it is a rule rather than a firmer request. Everything here that
    can be enforced in code is enforced in code; the model is asked one
    question, and how well it followed the formatting is not something
    to find out about from search results a month later.
    """
    text = (text or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # A first line that ends in a colon and is followed by more is an
    # announcement of the answer, not the answer.
    if len(lines) > 1 and lines[0].endswith(":"):
        lines = lines[1:]
    joined = " ".join(lines)
    for markup in ("**", "__", "###", "##", "#"):
        joined = joined.replace(markup, "")
    return " ".join(joined.split()).strip()


def worth_summarising(said: list[tuple[int, str]]) -> bool:
    """Whether there is anything here to compress."""
    return len([1 for _, text in said if text and text.strip()]) >= ENOUGH_LINES


def summariser(cfg_of: Any, stream: Any) -> Any:
    """Build the async callable the engine uses.

    Collaborators arrive as arguments for the same reason they do
    everywhere else here: the whole path can then be played out in a
    test with a fake model, and this module imports nothing of the
    spine's.
    """

    async def summarise(said: list[tuple[int, str]]) -> str | None:
        if not worth_summarising(said):
            logger.info("summary: too little was said — leaving it empty")
            return None
        prompt = PROMPT.format(body=transcript(said))
        parts: list[str] = []
        async for chunk in stream(
            [{"role": "user", "content": prompt}], cfg_of(), temperature=0.3
        ):
            parts.append(chunk)
        return tidy("".join(parts)) or None

    return summarise


__all__ = [
    "ENOUGH_LINES",
    "PROMPT",
    "tidy",
    "summariser",
    "transcript",
    "worth_summarising",
]
