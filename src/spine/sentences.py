from typing import AsyncIterator


async def sentences(
    deltas: AsyncIterator[str],
    *,
    min_chars: int = 3,
    max_buffer: int = 400,
) -> AsyncIterator[str]:
    """Groups streamed text deltas into whole sentences for TTS.

    A sentence ends at [.!?…] (one or more, e.g. "?!") when followed by
    whitespace or end of stream — so decimals like "3.14" and
    abbreviations glued to the next char do not split. Newlines flush the
    buffer. If the buffer exceeds max_buffer without a sentence end, flush
    at the last whitespace. Whatever remains at end of stream is flushed.
    Yielded strings are stripped; empty/whitespace-only pieces and pieces
    shorter than min_chars are merged into the next piece instead of
    being yielded alone (a lone "Ok." shorter than min_chars is still
    yielded at end of stream).
    """
    buffer: str = ""
    pending: str = ""

    async for delta in deltas:
        buffer += delta

        # Process buffer for complete units
        while buffer:
            # Priority 1: Check for newlines (always flush)
            nl_idx: int = buffer.find("\n")
            if nl_idx >= 0:
                part: str = buffer[:nl_idx]
                buffer = buffer[nl_idx + 1:]

                # Flush accumulated pending + this part
                if part.strip():
                    pending += part
                if pending.strip():
                    yield pending.strip()
                    pending = ""
                continue

            # Priority 2: Find sentence boundary
            sent_boundary: int = _find_sentence_boundary(buffer)
            if sent_boundary >= 0:
                sent: str = buffer[:sent_boundary]
                buffer = buffer[sent_boundary:]

                if sent:
                    pending += sent
                    stripped = pending.strip()
                    if len(stripped) >= min_chars:
                        yield stripped
                        pending = ""
                continue

            # Priority 3: Check buffer overflow
            if len(buffer) > max_buffer:
                last_space: int = buffer.rfind(" ")
                if last_space > 0:
                    part = buffer[:last_space]
                    buffer = buffer[last_space + 1:]

                    if part:
                        pending += part
                        stripped = pending.strip()
                        if len(stripped) >= min_chars:
                            yield stripped
                            pending = ""
                    continue
                else:
                    # Buffer is very long with no spaces; force flush
                    pending += buffer
                    buffer = ""
                    stripped = pending.strip()
                    if len(stripped) >= min_chars:
                        yield stripped
                        pending = ""
                    break

            # Can't make more progress right now
            break

    # End of stream: flush everything
    if buffer:
        pending += buffer

    if pending.strip():
        yield pending.strip()


def _find_sentence_boundary(text: str) -> int:
    """Return index after sentence-ending punctuation, or -1 if none found.

    A boundary exists when [.!?…] (one or more) is followed by whitespace
    or end of string.
    """
    for i in range(len(text)):
        if text[i] in ".!?…":
            j: int = i + 1
            while j < len(text) and text[j] in ".!?…":
                j += 1
            if j >= len(text) or text[j].isspace():
                return j
    return -1
