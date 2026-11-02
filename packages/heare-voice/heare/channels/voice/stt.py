"""Speech-to-text transcription via Groq Whisper API.

Raw PCM audio → in-memory WAV → Groq API → transcript.
No pipecat, no SDK — just stdlib + httpx.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import httpx


# One recogniser round trip, and the shortest a turn may wait for it
# before deciding the recogniser itself is dead.
STT_TIMEOUT_S = 15.0


@dataclass
class Transcript:
    """Transcribed speech result.

    Attributes
    ----------
    text
        Recognized speech, stripped of leading/trailing whitespace.
    language
        ISO 639-1 language code ("uk", "ru", "en", etc.).
        Derived from Groq's detected language name ("ukrainian" → "uk"),
        or the caller's hint if Groq returns no language field.
    """

    text: str
    language: str
    no_speech: float | None = None
    logprob: float | None = None


# Groq API language response names to ISO 639-1 codes.
_LANGUAGE_MAP = {
    "ukrainian": "uk",
    "russian": "ru",
    "english": "en",
}


class NoKey(Exception):
    """There is nothing to ask the recogniser with."""


async def transcribe(
    pcm: bytes,
    *,
    api_key: str,
    model: str = "whisper-large-v3",
    language: str | None = "uk",
    client: httpx.AsyncClient | None = None,
    timeout: float = STT_TIMEOUT_S,
) -> Transcript:
    """Transcribe 16 kHz mono int16 PCM audio.

    Parameters
    ----------
    pcm
        Raw audio bytes: 16 kHz, mono, 2 bytes per sample (int16).
    api_key
        Groq API key.
    model
        Whisper model identifier. Default: "whisper-large-v3".
    language
        Language hint for Groq's speech detector ("uk", "en", etc.).
        Passed through as-is when set; omitted from the request when None.
    client
        Existing httpx.AsyncClient to reuse. If None, a temporary client
        is created for this call and closed afterward.
    timeout
        HTTP request timeout in seconds.

    Returns
    -------
    Transcript
        Transcribed text and detected language (ISO code).

    Raises
    ------
    httpx.HTTPStatusError
        If the Groq API returns an error status.
    """
    if not api_key:
        raise NoKey
    # Build WAV in memory: 16 kHz, mono, 2-byte samples.
    wav_bytes = io.BytesIO()
    with wave.open(wav_bytes, "wb") as w:
        w.setnchannels(1)  # mono
        w.setsampwidth(2)  # 2 bytes per sample (int16)
        w.setframerate(16000)  # 16 kHz
        w.writeframes(pcm)
    wav_data = wav_bytes.getvalue()

    # Prepare multipart request.
    files = {"file": ("audio.wav", wav_data, "audio/wav")}
    data: dict[str, str] = {
        "model": model,
        "response_format": "verbose_json",
    }
    if language is not None:
        data["language"] = language
    headers = {"Authorization": f"Bearer {api_key}"}

    # Use provided client or create a temporary one.
    if client is not None:
        resp = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            files=files,
            data=data,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    else:
        async with httpx.AsyncClient() as temp_client:
            resp = await temp_client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                files=files,
                data=data,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            payload = resp.json()

    # Parse response.
    text = payload.get("text", "").strip()

    # Map language: Groq returns full names ("ukrainian"), we want ISO codes ("uk").
    detected_lang = payload.get("language", "").lower()
    language_iso = _LANGUAGE_MAP.get(detected_lang, detected_lang or (language or "uk"))

    # The recogniser's own doubt, kept beside what it heard: loudness cannot
    # tell noise from speech here, so this is what a filter can be measured on.
    pieces = payload.get("segments") or []
    doubt = max((float(p.get("no_speech_prob") or 0) for p in pieces), default=None)
    sure = min((float(p.get("avg_logprob") or 0) for p in pieces), default=None)
    return Transcript(text=text, language=language_iso, no_speech=doubt, logprob=sure)
