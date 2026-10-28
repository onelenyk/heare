"""Turn a natural-language query into an FTS5 MATCH expression.

Carried over from the old memory backend, where the transcript search
borrowed it.
"""

from __future__ import annotations

import re

from heare.core.i18n import language

_STOPWORDS: frozenset[str] = frozenset(
    {
        # Ukrainian
        "я",
        "ти",
        "ми",
        "ви",
        "він",
        "вона",
        "воно",
        "вони",
        "мені",
        "тобі",
        "мене",
        "нас",
        "вас",
        "його",
        "її",
        "їх",
        "що",
        "як",
        "яке",
        "який",
        "яка",
        "які",
        "чи",
        "не",
        "і",
        "й",
        "та",
        "а",
        "в",
        "у",
        "на",
        "з",
        "із",
        "зі",
        "до",
        "про",
        "це",
        "той",
        "ця",
        "ці",
        "же",
        "ж",
        "б",
        "би",
        "казав",
        "казала",
        "казали",
        "говорив",
        "говорила",
        "сказав",
        "сказала",
        "було",
        "буде",
        "є",
        # Russian
        "ты",
        "мы",
        "вы",
        "он",
        "она",
        "оно",
        "они",
        "мне",
        "тебе",
        "меня",
        "его",
        "её",
        "их",
        "какой",
        "какая",
        "какое",
        "какие",
        "ли",
        "со",
        "это",
        "тот",
        "то",
        "те",
        "бы",
        "говорил",
        "говорила",
        "сказал",
        "сказала",
        "было",
        "будет",
        "есть",
        # English
        "the",
        "a",
        "an",
        "i",
        "you",
        "me",
        "my",
        "what",
        "which",
        "is",
        "was",
        "do",
        "did",
        "to",
        "of",
        "in",
        "on",
        "and",
        "or",
        "we",
        "us",
        "our",
        "he",
        "she",
        "it",
        "its",
        "they",
        "them",
        "their",
        "his",
        "her",
        "him",
        "this",
        "that",
        "these",
        "those",
        "there",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "for",
        "with",
        "at",
        "from",
        "by",
        "as",
        "but",
        "not",
        "no",
        "so",
        "if",
        "then",
        "than",
        "can",
        "could",
        "would",
        "should",
        "will",
        "just",
        "any",
        "all",
        "about",
        "into",
        "when",
        "where",
        "who",
        "say",
        "said",
        "saying",
        "talk",
        "talked",
        "talking",
        "tell",
        "told",
        "remember",
        "recall",
        "mention",
        "mentioned",
        "anything",
        "something",
        "don",
        "doesn",
        "didn",
        "isn",
        "wasn",
        "aren",
        "weren",
        "won",
        "couldn",
        "wouldn",
        "shouldn",
        "haven",
        "hasn",
        "hadn",
        "t",
        "s",
        "m",
        "d",
        "ll",
        "ve",
        "re",
    }
)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# English inflections that FTS5's default unicode61 tokenizer keeps as
# different tokens. The query side is stemmed and the stems are searched
# as prefixes, which is the same trick the Ukrainian declension path
# uses: it needs no schema change, so an existing database keeps its
# index and starts answering "deployment" for "deployments" today.


def _english_stems(token: str) -> list[str]:
    """The stems an English word's endings would hide from FTS5."""
    word = token.lower()
    stems: list[str] = []

    def add(stem: str) -> None:
        if len(stem) >= 3 and stem != word and stem not in stems:
            stems.append(stem)

    if word.endswith("ies") and len(word) > 4:
        add(word[:-3] + "y")
    if word.endswith("es") and len(word) > 3:
        add(word[:-2])
        add(word[:-1])
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        add(word[:-1])
    if word.endswith("ing") and len(word) > 5:
        base = word[:-3]
        add(base)
        if len(base) > 2 and base[-1] == base[-2]:
            add(base[:-1])
    if word.endswith("ed") and len(word) > 4:
        base = word[:-2]
        add(base)
        add(word[:-1])
        if len(base) > 2 and base[-1] == base[-2]:
            add(base[:-1])
    if word.endswith("ly") and len(word) > 4:
        add(word[:-2])
    return stems


def _sanitize_fts_query(raw: str) -> str | None:
    """Turn a natural-language query into an FTS5 MATCH expression.

    Tokens are extracted with a word-boundary regex (so stray punctuation
    like the trailing "?" in "казав?" doesn't stop it matching the
    stopword "казав"), stopwords are dropped, and each remaining token is
    quoted (to stay a safe bareword for FTS5, whatever characters it
    contains) and OR-ed together -- not AND-ed, which is FTS5's default for
    a bareword list and is what previously required every word in a query
    to appear in a memory for it to match at all.

    Returns ``None`` when nothing meaningful is left to search on (an empty
    query, or a query made entirely of stopwords), signalling the caller to
    fall back to another strategy instead of running a MATCH with no terms.
    """
    tokens = _TOKEN_RE.findall(raw)
    kept = [t for t in tokens if t.lower() not in _STOPWORDS]
    if not kept:
        return None

    # Ukrainian declines: a memory holding «собака» is invisible to a
    # question about «собаку», and the tokenizer matches whole tokens
    # only. Each word is therefore searched both exactly and as a
    # prefix of its stem, so the case endings stop mattering.
    #
    # English inflects the other way round — the query says
    # "deployments" while the transcript says "deployment" — so its
    # tokens get a suffix-stripped stem and a prefix of the word itself.
    # Both paths are safe to run in either language: a Ukrainian token
    # has no English ending, and an English one has no Cyrillic case.
    english = language() == "en"
    terms: list[str] = []
    for token in kept:
        terms.append(f'"{token}"')
        if english:
            if len(token) >= 3:
                terms.append(f'"{token}"*')
            terms.extend(f'"{stem}"*' for stem in _english_stems(token))
        elif len(token) >= 5:
            terms.append(f'"{token[:-1]}"*')
    return " OR ".join(dict.fromkeys(terms))
