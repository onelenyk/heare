"""Tests for src.spine.stt — Groq Whisper transcription.

No network; uses a fake client to capture and validate request structure.
"""

from __future__ import annotations

import io
import wave
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.spine.stt import Transcript, transcribe


def _make_pcm_silence(duration_ms: int = 100, sample_rate: int = 16000) -> bytes:
    """Generate silence: 16 kHz mono int16."""
    num_samples = (sample_rate * duration_ms) // 1000
    return b"\x00" * (num_samples * 2)


class FakeAsyncClient:
    """Fake httpx.AsyncClient for testing."""

    def __init__(self, response_payload: dict):
        self.response_payload = response_payload
        self.last_request = None

    async def post(
        self,
        url: str,
        *,
        files=None,
        data=None,
        timeout=None,
    ):
        """Capture request and return a fake response."""
        self.last_request = {
            "url": url,
            "files": files,
            "data": data,
            "timeout": timeout,
        }
        return FakeResponse(self.response_payload)


class FakeResponse:
    """Fake httpx.Response."""

    def __init__(self, payload: dict):
        self._payload = payload
        self._raised = False

    def raise_for_status(self):
        """No-op; subclasses can set _raised = True to simulate errors."""
        if self._raised:
            raise RuntimeError("Fake HTTP error")

    def json(self):
        """Return the response payload."""
        return self._payload


@pytest.mark.asyncio
async def test_transcribe_url_correct():
    """Verify POST goes to the right Groq endpoint."""
    client = FakeAsyncClient({"text": "hello", "language": "english"})
    pcm = _make_pcm_silence()

    await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    assert (
        client.last_request["url"]
        == "https://api.groq.com/openai/v1/audio/transcriptions"
    )


@pytest.mark.asyncio
async def test_transcribe_wav_structure():
    """Decode captured WAV and verify rate, channels, sample width."""
    client = FakeAsyncClient({"text": "hello", "language": "english"})
    pcm = _make_pcm_silence(duration_ms=200)

    await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    files = client.last_request["files"]
    assert files is not None
    assert "file" in files

    filename, wav_bytes, mime = files["file"]
    assert filename == "audio.wav"
    assert mime == "audio/wav"

    # Decode and validate WAV structure.
    wav_io = io.BytesIO(wav_bytes)
    with wave.open(wav_io, "rb") as w:
        assert w.getframerate() == 16000, "Expected 16 kHz"
        assert w.getnchannels() == 1, "Expected mono"
        assert w.getsampwidth() == 2, "Expected 2 bytes per sample (int16)"
        # Verify we can read the audio data back.
        data = w.readframes(w.getnframes())
        assert len(data) == len(pcm), "WAV payload size mismatch"


@pytest.mark.asyncio
async def test_transcribe_request_fields():
    """Verify model, response_format, and language are in the data dict."""
    client = FakeAsyncClient({"text": "hello"})
    pcm = _make_pcm_silence()

    await transcribe(
        pcm,
        api_key="test_key",
        model="whisper-large-v3",
        language="uk",
        client=client,
    )

    data = client.last_request["data"]
    assert data["model"] == "whisper-large-v3"
    assert data["response_format"] == "verbose_json"
    assert data["language"] == "uk"


@pytest.mark.asyncio
async def test_transcribe_language_omitted_when_none():
    """When language hint is None, omit it from the request."""
    client = FakeAsyncClient({"text": "hello"})
    pcm = _make_pcm_silence()

    await transcribe(
        pcm,
        api_key="test_key",
        language=None,
        client=client,
    )

    data = client.last_request["data"]
    assert "language" not in data


@pytest.mark.asyncio
async def test_transcribe_ukrainian_mapping():
    """Groq returns 'ukrainian' → we map to 'uk'."""
    client = FakeAsyncClient({"text": "привіт", "language": "ukrainian"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    assert result.text == "привіт"
    assert result.language == "uk"


@pytest.mark.asyncio
async def test_transcribe_russian_mapping():
    """Groq returns 'russian' → we map to 'ru'."""
    client = FakeAsyncClient({"text": "привет", "language": "russian"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    assert result.text == "привет"
    assert result.language == "ru"


@pytest.mark.asyncio
async def test_transcribe_english_mapping():
    """Groq returns 'english' → we map to 'en'."""
    client = FakeAsyncClient({"text": "hello", "language": "english"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    assert result.text == "hello"
    assert result.language == "en"


@pytest.mark.asyncio
async def test_transcribe_unknown_language_passthrough():
    """Unknown language names pass through lowercased."""
    client = FakeAsyncClient({"text": "text", "language": "SPANISH"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        language="es",
        client=client,
    )

    assert result.language == "spanish"


@pytest.mark.asyncio
async def test_transcribe_missing_language_uses_hint():
    """Groq omits language field → use the caller's hint."""
    client = FakeAsyncClient({"text": "text"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        language="ru",
        client=client,
    )

    assert result.language == "ru"


@pytest.mark.asyncio
async def test_transcribe_missing_language_no_hint():
    """Groq omits language, no hint → default to 'uk'."""
    client = FakeAsyncClient({"text": "text"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        language=None,
        client=client,
    )

    assert result.language == "uk"


@pytest.mark.asyncio
async def test_transcribe_empty_text():
    """Empty response text → Transcript(text='')."""
    client = FakeAsyncClient({"text": "", "language": "english"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    assert result.text == ""
    assert result.language == "en"


@pytest.mark.asyncio
async def test_transcribe_text_stripped():
    """Leading/trailing whitespace is stripped."""
    client = FakeAsyncClient({"text": "  hello world  ", "language": "english"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    assert result.text == "hello world"


@pytest.mark.asyncio
async def test_transcribe_default_model():
    """Default model is 'whisper-large-v3'."""
    client = FakeAsyncClient({"text": "hello"})
    pcm = _make_pcm_silence()

    await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    data = client.last_request["data"]
    assert data["model"] == "whisper-large-v3"


@pytest.mark.asyncio
async def test_transcribe_custom_model():
    """Custom model is passed through."""
    client = FakeAsyncClient({"text": "hello"})
    pcm = _make_pcm_silence()

    await transcribe(
        pcm,
        api_key="test_key",
        model="custom-whisper",
        client=client,
    )

    data = client.last_request["data"]
    assert data["model"] == "custom-whisper"


@pytest.mark.asyncio
async def test_transcribe_default_language():
    """Default language hint is 'uk'."""
    client = FakeAsyncClient({"text": "hello"})
    pcm = _make_pcm_silence()

    await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    data = client.last_request["data"]
    assert data["language"] == "uk"


@pytest.mark.asyncio
async def test_transcribe_custom_timeout():
    """Custom timeout is passed through."""
    client = FakeAsyncClient({"text": "hello"})
    pcm = _make_pcm_silence()

    await transcribe(
        pcm,
        api_key="test_key",
        timeout=30.0,
        client=client,
    )

    assert client.last_request["timeout"] == 30.0


@pytest.mark.asyncio
async def test_transcribe_no_client_creates_temporary():
    """When no client is provided, one is created and used."""
    # We can't easily test the "closed" part without mocking AsyncClient,
    # but we can verify that the function works without an explicit client.
    # For this test, we mock httpx.AsyncClient itself.
    fake_response = FakeResponse({"text": "hello", "language": "english"})

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            return fake_response

    import httpx
    import unittest.mock as mock

    pcm = _make_pcm_silence()

    with mock.patch("httpx.AsyncClient", return_value=MockAsyncClient()):
        result = await transcribe(
            pcm,
            api_key="test_key",
        )
        assert result.text == "hello"
        assert result.language == "en"


@pytest.mark.asyncio
async def test_transcribe_with_provided_client():
    """When a client is provided, it is used directly (not closed by caller)."""
    client = FakeAsyncClient({"text": "hello", "language": "english"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    assert result.text == "hello"
    # The caller's client should not be closed by transcribe.
    # (We can't easily verify this without instrumentation, but the
    # function signature guarantees it in the docstring.)


@pytest.mark.asyncio
async def test_transcribe_response_without_text_field():
    """Missing 'text' field → defaults to empty string."""
    client = FakeAsyncClient({"language": "english"})
    pcm = _make_pcm_silence()

    result = await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    assert result.text == ""


@pytest.mark.asyncio
async def test_transcribe_response_format_always_verbose_json():
    """response_format is always set to 'verbose_json'."""
    client = FakeAsyncClient({"text": "hello"})
    pcm = _make_pcm_silence()

    await transcribe(
        pcm,
        api_key="test_key",
        client=client,
    )

    data = client.last_request["data"]
    assert data["response_format"] == "verbose_json"


@pytest.mark.asyncio
async def test_transcript_dataclass():
    """Transcript dataclass can be instantiated and accessed."""
    t = Transcript(text="hello", language="en")
    assert t.text == "hello"
    assert t.language == "en"
