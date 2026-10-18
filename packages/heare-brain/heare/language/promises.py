"""A reply that promises work it did not start.

Measured on 27 September with the real voice model: about one turn in
ten, it answers «Гляну, секунду.» or «Ставлю нагадування» and calls no
tool. Nothing was started, so the answer it promised can never arrive,
and the person waits on a sentence that meant nothing.

A prompt rule would be one more sentence the model can ignore. So the
loop checks: a short reply in the first person present or future —
looking, checking, opening, setting — or asking for a moment, with no
tool call beside it, is treated as the delegation the model meant.

The error is asymmetric, which is why the net is wide. Delegating a
turn that needed nothing costs one extra line from the hands; missing
one costs the answer.
"""

from __future__ import annotations

import re

_PROMISE = re.compile(
    r"\b("
    r"гляну|погляну|дивлюс[ья]|подивлюс[ья]|перевірю|перевіряю|шукаю|пошукаю|знайду|"
    r"нагадаю|"
    r"відкриваю|відкрию|запускаю|запущу|ставлю|поставлю|налаштую|"
    r"зараз\s+зроблю|роблю|зроблю|займусь|займуся|беру|берусь|беруся|"
    # Found by the listener's live run on 12 October: «Створюю.» with no
    # call, then «файл створено» on the next turn — about a file that
    # never existed. Every verb of doing something to the world belongs here.
    r"створюю|створю|пишу|напишу|записую|запишу|зберігаю|збережу|"
    r"видаляю|видалю|надсилаю|надішлю|виконую|виконаю|додаю|додам|"
    r"змінюю|зміню|оновлюю|оновлю|виправляю|виправлю|"
    # «Доручив агенту створити файл» with no call: 12 October, three
    # requests in a row, after the prompt began to list the work.
    r"доручив|доручила|доручаю|доручу|передав|передала|передаю|передам|"
    r"секунду|секундочку|хвилинку|момент"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

# Past the length of an acknowledgement, a reply is an answer that
# happens to contain one of the words above («я перевірю це завтра, а
# поки що…»), not a promise standing in for one.
_MAX_PROMISE_CHARS = 120


def promises_work(reply: str) -> bool:
    """True when `reply` announces work in place of doing it."""
    text = (reply or "").strip()
    return bool(text) and len(text) <= _MAX_PROMISE_CHARS and bool(_PROMISE.search(text))


# A reply that says something was drawn or changed. Found on 12 October:
# «Оновив погоду — тепер там і вологість» and «Готую план із п'яти
# кроків», each with no call at all — the screen stayed as it was. Unlike
# a promise of work, this is never handed to an executor: it is asked for
# again, and that is all.
_CLAIM = re.compile(
    r"\b("
    r"оновив|оновила|додав|додала|поставив|поставила|намалював|намалювала|"
    r"прибрав|прибрала|змінив|змінила|зробив|зробила|готово|готую|малюю|"
    r"намалюю|показую|покажу|ставлю|поставлю|оновлюю|оновлю|додаю|додам"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


def claims_done(reply: str) -> bool:
    """True when `reply` says a thing was shown or changed."""
    text = (reply or "").strip()
    return bool(text) and len(text) <= _MAX_PROMISE_CHARS and bool(_CLAIM.search(text))
