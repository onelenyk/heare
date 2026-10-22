"""How much of the conversation the model sees: the assembly's budget.

The core counts messages; a model counts tokens. This is a policy, not a
law of conversation, so it lives in the brain: the pinned system prompt is
kept whole, and the oldest turns go first. Four characters per token is an
estimate until a real tokenizer is measured.
"""

from __future__ import annotations

import logging
from math import ceil

CHARS_PER_TOKEN = 4
DEFAULT_TOKENS = 6000

logger = logging.getLogger("heare.context")


def _tokens(text: str) -> int:
    return ceil(len(text) / CHARS_PER_TOKEN)


def fit(
    system: str, turns: list[dict], *, limit: int = DEFAULT_TOKENS
) -> tuple[str, list[dict]]:
    """Trim the oldest turns until the prompt fits the budget.

    The system prompt is pinned — laws and profile included — so it is
    never cut here, even when it alone is over. One turn is dropped with
    its pair, keeping user/assistant order intact, and the last two turns
    stay whatever the estimate says: an empty conversation is worse than a
    long one.
    """
    used = _tokens(system)
    if used >= limit:
        logger.warning(
            "system prompt alone is %d tokens, over the %d budget — kept whole",
            used, limit,
        )
    kept = list(turns)
    while len(kept) > 2 and used + sum(
        _tokens(str(m.get("content", ""))) for m in kept
    ) > limit:
        kept = kept[2:]
    return system, kept
