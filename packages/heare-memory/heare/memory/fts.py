"""Turn a natural-language query into an FTS5 MATCH expression.

Carried over from the old memory backend, where the transcript search
borrowed it.
"""

from __future__ import annotations

import re

_STOPWORDS: frozenset[str] = frozenset(
    {
        # Ukrainian
        "я", "ти", "ми", "ви", "він", "вона", "воно", "вони",
        "мені", "тобі", "мене", "нас", "вас", "його", "її", "їх",
        "що", "як", "яке", "який", "яка", "які", "чи", "не", "і", "й", "та", "а",
        "в", "у", "на", "з", "із", "зі", "до", "про", "це", "той", "ця", "ці",
        "же", "ж", "б", "би",
        "казав", "казала", "казали", "говорив", "говорила", "сказав", "сказала",
        "було", "буде", "є",
        # Russian
        "ты", "мы", "вы", "он", "она", "оно", "они",
        "мне", "тебе", "меня", "его", "её", "их",
        "какой", "какая", "какое", "какие", "ли", "со",
        "это", "тот", "то", "те",
        "бы", "говорил", "говорила", "сказал", "сказала", "было", "будет", "есть",
        # English
        "the", "a", "an", "i", "you", "me", "my", "what", "which", "is", "was",
        "do", "did", "to", "of", "in", "on", "and", "or",
    }
)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


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
    terms: list[str] = []
    for token in kept:
        terms.append(f'"{token}"')
        if len(token) >= 5:
            terms.append(f'"{token[:-1]}"*')
    return " OR ".join(terms)

