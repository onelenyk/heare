"""Normalize TTS input text — fix run-on Cyrillic words.

Some LLMs (notably DeepSeek) occasionally output Ukrainian / Russian text with
no spaces between words, producing run-on gibberish when fed to the TTS engine.

This module uses a heuristic dictionary-based segmenter to detect and repair
run-on Cyrillic segments without affecting English or already-correct text.
"""  # noqa: D205, D400
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Ukrainian / Russian word dictionary
# ---------------------------------------------------------------------------

# Frequent short words (prepositions, pronouns, particles, conjunctions —
# keep these lean so they don't accidentally split longer real words).
_COMMON_UK_WORDS: set[str] = {
    # Personal pronouns
    "я",
    "ти",
    "він",
    "вона",
    "ми",
    "ви",
    "вони",
    "мене",
    "тебе",
    "його",
    "її",
    "нас",
    "вас",
    "їх",
    "мені",
    "тобі",
    "йому",
    "нам",
    "вам",
    "їм",
    # Demonstratives
    "це",
    "цей",
    "ця",
    "ці",
    "той",
    "та",
    "те",
    "ті",
    # Possessives
    "мій",
    "твій",
    "наш",
    "ваш",
    "свій",
    "моя",
    "твоя",
    "наша",
    "ваша",
    "своя",
    "моє",
    "твоє",
    "наше",
    "ваше",
    "своє",
    "мої",
    "твої",
    "наші",
    "ваші",
    "свої",
    # Question words
    "що",
    "як",
    "де",
    "коли",
    "чому",
    "хто",
    "який",
    "яка",
    "яке",
    "які",
    "чий",
    "чия",
    "чиє",
    "чиї",
    "скільки",
    "куди",
    "звідки",
    "навіщо",
    # Prepositions
    "в",
    "у",
    "на",
    "з",
    "до",
    "від",
    "про",
    "за",
    "перед",
    "під",
    "над",
    "між",
    "через",
    "без",
    "для",
    "після",
    "при",
    "крім",
    "серед",
    "поза",
    "попід",
    "понад",
    "заради",
    "проти",
    # Conjunctions
    "і",
    "та",
    "а",
    "але",
    "бо",
    "чи",
    "або",
    "щоб",
    "якщо",
    "тому",
    "коли",
    "ніж",
    "проте",
    "однак",
    # Particles
    "не",
    "ж",
    "би",
    "б",
    "ось",
    "от",
    "навіть",
    "лише",
    "тільки",
    "саме",
    "ледве",
    "майже",
    "ніби",
    "мов",
    "немов",
    "неначе",
    "хай",
    "нехай",
    # Common verbs (present)
    "є",
    "був",
    "була",
    "було",
    "були",
    "буде",
    "будуть",
    "можу",
    "можеш",
    "може",
    "можемо",
    "можете",
    "можуть",
    "хочу",
    "хочеш",
    "хоче",
    "хочемо",
    "хочете",
    "хочуть",
    "знаю",
    "знаєш",
    "знає",
    "знаємо",
    "знаєте",
    "знають",
    "думаю",
    "думаєш",
    "думає",
    "думаємо",
    "думаєте",
    "думають",
    "бачу",
    "бачиш",
    "бачить",
    "бачимо",
    "бачите",
    "бачать",
    "кажу",
    "кажеш",
    "каже",
    "кажемо",
    "кажете",
    "кажуть",
    "роблю",
    "робиш",
    "робить",
    "робимо",
    "робите",
    "роблять",
    "йду",
    "йдеш",
    "йде",
    "йдемо",
    "йдете",
    "йдуть",
    "люблю",
    "любиш",
    "любить",
    "любимо",
    "любите",
    "люблять",
    "маю",
    "маєш",
    "має",
    "маємо",
    "маєте",
    "мають",
    # Modal / impersonal
    "треба",
    "потрібно",
    "можна",
    "варто",
    "слід",
    # Politeness
    "дякую",
    "будь",
    "ласка",
    "прошу",
    "вибачте",
    "перепрошую",
    "навзаєм",
    # Affirmation / negation / common adverbs
    "так",
    "ні",
    "добре",
    "гаразд",
    "зараз",
    "потім",
    "вже",
    "ще",
    "дуже",
    "багато",
    "мало",
    "трохи",
    "сьогодні",
    "вчора",
    "завтра",
    "завжди",
    "ніколи",
    "іноді",
    "часто",
    "рідко",
    "там",
    "тут",
    "туди",
    "сюди",
    "звідти",
    "звідси",
    "також",
    "теж",
    # Common adjectives
    "важливий",
    "важлива",
    "важливе",
    "важливі",
    "великий",
    "велика",
    "велике",
    "великі",
    "малий",
    "мала",
    "мале",
    "малі",
    "новий",
    "нова",
    "нове",
    "нові",
    "старий",
    "стара",
    "старе",
    "старі",
    "гарний",
    "гарна",
    "гарне",
    "гарні",
    "поганий",
    "погана",
    "погане",
    "погані",
    "перший",
    "перша",
    "перше",
    "перші",
    "останній",
    "остання",
    "останнє",
    "останні",
    "цікавий",
    "цікава",
    "цікаве",
    "цікаві",
    "простий",
    "проста",
    "просте",
    "прості",
    "складний",
    "складна",
    "складне",
    "складні",
    # Common nouns
    "запит",
    "запити",
    "питання",
    "питанню",
    "завдання",
    "завданню",
    "файл",
    "файли",
    "файлу",
    "файлів",
    "файлом",
    "папка",
    "папки",
    "папку",
    "папці",
    "код",
    "коду",
    "кодом",
    "програма",
    "програми",
    "програму",
    "програмі",
    "помічник",
    "помічника",
    "помічнику",
    "помічником",
    "людина",
    "людини",
    "людині",
    "людиною",
    "люди",
    "людей",
    "людям",
    "час",
    "часу",
    "часом",
    "день",
    "дня",
    "дню",
    "днем",
    "робота",
    "роботи",
    "роботу",
    "роботі",
    "роботою",
    "система",
    "системи",
    "систему",
    "системі",
    "системою",
    "мову",
    "мови",
    "мові",
    "мовою",
    "голос",
    "голосу",
    "голосом",
    "слово",
    "слова",
    "слову",
    "словом",
    "текст",
    "тексту",
    "текстом",
    "сторінка",
    "сторінки",
    "сторінку",
    "сторінці",
    # Numbers
    "один",
    "одна",
    "одне",
    "одні",
    "два",
    "дві",
    "три",
    "чотири",
    "п'ять",
    "шість",
    "сім",
    "вісім",
    "дев'ять",
    "десять",
}

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_CYRILLIC_RE = re.compile(r"[а-яА-ЯіІїЇєЄґҐ]")


def _has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text))


_TOKENISE_RE = re.compile(r"(\s+|[.,!?;:…()\[\]{}\-]+)")


def _segment_words(run_on: str) -> str:
    """Attempt to split a run-on Cyrillic string into known words.

    Uses greedy longest-match with a safety valve: if the longest
    dictionary match starting at position *i* is itself decomposable
    into two or more shorter known words, we prefer the decomposition.
    """
    if not run_on:
        return run_on

    i = 0
    n = len(run_on)
    result: list[str] = []

    while i < n:
        best_match: tuple[int, str] | None = None
        for word_len in range(min(20, n - i), 0, -1):
            candidate = run_on[i : i + word_len].lower()
            if candidate in _COMMON_UK_WORDS:
                if word_len > 4 and _can_decompose(candidate):
                    continue
                best_match = (word_len, run_on[i : i + word_len])
                break

        if best_match is not None:
            result.append(best_match[1])
            i += best_match[0]
        else:
            result.append(run_on[i])
            i += 1

    return " ".join(result)


def _can_decompose(word: str) -> bool:
    w = word.lower()
    if len(w) <= 4:
        return False
    for split in range(1, len(w)):
        left = w[:split]
        right = w[split:]
        if left in _COMMON_UK_WORDS and right in _COMMON_UK_WORDS:
            return True
        if left in _COMMON_UK_WORDS and _can_decompose(right):
            return True
    return False


MIN_CYRILLIC_RUN = 18


def has_run_on_cyrillic(text: str) -> bool:
    if not _has_cyrillic(text):
        return False

    segments = _TOKENISE_RE.split(text)
    for seg in segments:
        if not seg or _TOKENISE_RE.fullmatch(seg):
            continue
        if " " in seg:
            continue
        cyrillic_count = len(_CYRILLIC_RE.findall(seg))
        if cyrillic_count > MIN_CYRILLIC_RUN or _can_decompose(seg):
            return True
    return False


def normalize_cyrillic_spacing(text: str) -> str:
    if not _has_cyrillic(text):
        return text

    tokens = _TOKENISE_RE.split(text)
    fixed = False
    result: list[str] = []

    for tok in tokens:
        if not tok or _TOKENISE_RE.fullmatch(tok):
            result.append(tok)
            continue

        if " " in tok:
            result.append(tok)
            continue

        cyrillic_count = len(_CYRILLIC_RE.findall(tok))
        if cyrillic_count > MIN_CYRILLIC_RUN or _can_decompose(tok):
            fixed = True
            tok = _segment_words(tok)

        if result and not result[-1].isspace():
            result.append(" ")
        result.append(tok)

    if not fixed:
        return text
    return "".join(result)
