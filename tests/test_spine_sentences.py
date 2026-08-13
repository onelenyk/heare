import pytest
from typing import AsyncIterator

from src.spine.sentences import sentences


async def _gen(*parts: str) -> AsyncIterator[str]:
    """Helper to create an async generator from parts."""
    for part in parts:
        yield part


@pytest.mark.asyncio
async def test_multi_delta_multiple_sentences():
    """Case 1: Multi-delta Ukrainian text with multiple sentences."""
    result = [s async for s in sentences(_gen("Привіт", ", світ", ". Як", " справи?"))]
    assert result == ["Привіт, світ.", "Як справи?"]


@pytest.mark.asyncio
async def test_one_big_delta_three_sentences():
    """Case 2: One big delta with three sentences."""
    result = [s async for s in sentences(_gen("First. Second! Third?"))]
    assert result == ["First.", "Second!", "Third?"]


@pytest.mark.asyncio
async def test_decimal_number_no_split():
    """Case 3: Decimal number 3.14 stays in one sentence."""
    result = [s async for s in sentences(_gen("число 3.14 і далі."))]
    assert result == ["число 3.14 і далі."]


@pytest.mark.asyncio
async def test_multiple_punctuation_marks():
    """Case 4: Multiple punctuation marks treated as single ending."""
    result = [s async for s in sentences(_gen("Що?!"))]
    assert result == ["Що?!"]


@pytest.mark.asyncio
async def test_newline_flush():
    """Case 5: Newline always flushes the buffer."""
    result = [s async for s in sentences(_gen("перший рядок\nдругий"))]
    assert result == ["перший рядок", "другий"]


@pytest.mark.asyncio
async def test_no_terminator_flush_at_end():
    """Case 6: Text with no sentence terminator flushes at end of stream."""
    result = [s async for s in sentences(_gen("just some text"))]
    assert result == ["just some text"]


@pytest.mark.asyncio
async def test_buffer_overflow_at_whitespace():
    """Case 7: Long text exceeding max_buffer flushes at last whitespace."""
    long_text = "word " * 100
    result = [s async for s in sentences(_gen(long_text), max_buffer=50)]
    # All words should be recovered
    rejoined = " ".join(result)
    assert long_text.strip() == rejoined.strip()


@pytest.mark.asyncio
async def test_empty_stream():
    """Case 8: Empty stream produces no output."""
    result = [s async for s in sentences(_gen())]
    assert result == []


@pytest.mark.asyncio
async def test_short_sentence_merged_with_next():
    """Short sentences (< min_chars) merge into the next one."""
    result = [s async for s in sentences(_gen("Hi. This is a longer sentence."), min_chars=5)]
    assert result == ["Hi. This is a longer sentence."]


@pytest.mark.asyncio
async def test_short_sentence_at_end_of_stream():
    """A short sentence at end of stream is still yielded."""
    result = [s async for s in sentences(_gen("First sentence. Hi."), min_chars=5)]
    # "First sentence." is long enough to yield
    # "Hi." is short, but at end of stream it should be yielded
    assert result == ["First sentence.", "Hi."]


@pytest.mark.asyncio
async def test_ellipsis_as_sentence_end():
    """Ellipsis (…) counts as sentence ending."""
    result = [s async for s in sentences(_gen("Wait for it… Then continue."))]
    assert result == ["Wait for it…", "Then continue."]


@pytest.mark.asyncio
async def test_multiple_spaces_after_sentence():
    """Multiple spaces after sentence are handled correctly."""
    result = [s async for s in sentences(_gen("First.   Second."))]
    assert result == ["First.", "Second."]


@pytest.mark.asyncio
async def test_no_space_after_sentence_boundary():
    """Sentence ending followed immediately by end of string."""
    result = [s async for s in sentences(_gen("One sentence."))]
    assert result == ["One sentence."]


@pytest.mark.asyncio
async def test_abbreviation_with_period_mid_text():
    """Period followed by space IS a sentence boundary per spec."""
    result = [s async for s in sentences(_gen("Dr. Smith went home."))]
    # "Dr." is followed by space, so it IS a boundary
    # Then "Smith went home." ends at the period
    assert result == ["Dr.", "Smith went home."]


@pytest.mark.asyncio
async def test_mixed_punctuation_sequence():
    """Multiple different punctuation marks in sequence."""
    result = [s async for s in sentences(_gen("What!?!?!"))]
    assert result == ["What!?!?!"]


@pytest.mark.asyncio
async def test_newline_with_multiple_sentences():
    """Newlines interspersed with sentences."""
    result = [s async for s in sentences(_gen("First sentence.\nSecond. Third."))]
    assert result == ["First sentence.", "Second.", "Third."]


@pytest.mark.asyncio
async def test_only_whitespace_lines():
    """Lines with only whitespace are skipped."""
    result = [s async for s in sentences(_gen("First.\n\n\nSecond."))]
    assert result == ["First.", "Second."]


@pytest.mark.asyncio
async def test_paragraph_with_newline_no_sentence_end():
    """Newline flushes even without sentence punctuation."""
    result = [s async for s in sentences(_gen("No period here\nSecond line"))]
    assert result == ["No period here", "Second line"]


@pytest.mark.asyncio
async def test_period_at_buffer_boundary():
    """Period arriving at different delta boundaries."""
    result = [s async for s in sentences(_gen("Hello", ".", " World", "."))]
    assert result == ["Hello.", "World."]


@pytest.mark.asyncio
async def test_max_buffer_no_space_fallback():
    """Buffer exceeds max_buffer with no spaces; all accumulated text is flushed."""
    # Create text with no spaces that exceeds max_buffer
    long_no_space = "x" * 500
    result = [s async for s in sentences(_gen(long_no_space), max_buffer=100)]
    # Should still output the text
    assert "".join(result) == long_no_space


@pytest.mark.asyncio
async def test_split_delta_punctuation():
    """Punctuation split across deltas yields when boundary found."""
    result = [s async for s in sentences(_gen("Hello", "?", "!"))]
    # "Hello?" is yielded when "?" completes the boundary
    # "!" is too short (1 char < 3), so it's in pending
    # At end of stream, pending is yielded
    assert result == ["Hello?", "!"]


@pytest.mark.asyncio
async def test_whitespace_only_input():
    """Input with only whitespace produces no output."""
    result = [s async for s in sentences(_gen("   \n  \t  "))]
    assert result == []
