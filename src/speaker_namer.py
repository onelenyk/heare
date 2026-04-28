"""LLM-driven identity inference for guest speakers.

The acoustic side (speaker_processor + speaker_gallery) labels turns as
`owner` / `guest_NN` / None. SpeakerNamer consumes those tagged turns
and periodically asks an LLM to propose a real first name per unnamed
guest, renaming the gallery entry when confidence sustains.

Design pillars:
- **Non-blocking**: enqueue is put_nowait with silent drop on QueueFull.
  The audio/STT path is never awaited by this module.
- **Parallel**: run() is an asyncio task, spawned alongside the pipeline.
- **Owner immune**: filtered at enqueue AND apply_prediction time.
- **Re-assignable**: higher-confidence prediction overwrites prior name
  beyond `speaker_namer_confidence_hysteresis` to prevent flapping.
- **Fail-closed**: any predictor exception/timeout/malformed JSON is
  logged and discarded — the loop never crashes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

import httpx

from .speaker_gallery import LabelValidationError, sanitize_label

if TYPE_CHECKING:
    from .config import Settings
    from .speaker_gallery import SpeakerGallery


_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


logger = logging.getLogger("heare.speaker_namer")


# (name-or-None, confidence-in-[0,1])
PredictResult = tuple[str | None, float]
PredictFn = Callable[[str, list[str]], Awaitable[PredictResult]]


@dataclass(frozen=True)
class TurnRecord:
    speaker_id: str
    text: str
    timestamp: float
    turn_id: int | None = None


@dataclass
class _SpeakerState:
    buffer: deque[tuple[str, float]] = field(default_factory=deque)
    new_turns_since_check: int = 0
    last_check_at: float = 0.0
    current_confidence: float = 0.0


class SpeakerNamer:
    def __init__(
        self,
        gallery: "SpeakerGallery",
        settings: "Settings",
        predict_fn: PredictFn,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gallery = gallery
        self._settings = settings
        self._predict_fn = predict_fn
        self._clock = clock
        self._queue: asyncio.Queue[TurnRecord] = asyncio.Queue(
            maxsize=settings.speaker_namer_queue_max
        )
        self._states: dict[str, _SpeakerState] = {}

    # -- ingest ---------------------------------------------------------

    def enqueue(self, record: TurnRecord) -> None:
        """Non-blocking enqueue. Owner turns and queue-full are silently dropped."""
        if record.speaker_id == "owner":
            return
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.debug(
                "namer queue full (max=%d) — dropping turn for %s",
                self._settings.speaker_namer_queue_max,
                record.speaker_id,
            )

    # -- internals (exposed for unit tests) ------------------------------

    def _state(self, speaker_id: str) -> _SpeakerState:
        st = self._states.get(speaker_id)
        if st is None:
            st = _SpeakerState(
                buffer=deque(maxlen=self._settings.speaker_namer_buffer_size)
            )
            self._states[speaker_id] = st
        return st

    def _ingest(self, record: TurnRecord) -> None:
        st = self._state(record.speaker_id)
        st.buffer.append((record.text, record.timestamp))
        st.new_turns_since_check += 1

    def _should_trigger(self, speaker_id: str) -> bool:
        if speaker_id == "owner":
            return False
        st = self._states.get(speaker_id)
        if st is None:
            return False
        if st.new_turns_since_check < self._settings.speaker_namer_min_turns:
            return False
        elapsed = self._clock() - st.last_check_at
        if elapsed < self._settings.speaker_namer_debounce_seconds:
            return False
        return True

    def _apply_prediction(
        self, speaker_id: str, name: str | None, confidence: float
    ) -> bool:
        """Rename the gallery entry when the prediction clears the gates.

        Returns True iff a rename happened.
        """
        if speaker_id == "owner":
            return False
        if not name:
            return False
        if confidence < self._settings.speaker_namer_confidence_threshold:
            return False
        st = self._state(speaker_id)
        if confidence <= st.current_confidence + self._settings.speaker_namer_confidence_hysteresis:
            return False
        try:
            ok = self._gallery.rename_speaker(speaker_id, name)
        except LabelValidationError as e:
            logger.warning("namer: invalid label %r rejected by gallery: %s", name, e)
            return False
        except Exception:  # noqa: BLE001
            logger.exception("namer: gallery rename failed for %s", speaker_id)
            return False
        if not ok:
            # speaker no longer in gallery — nothing to rename
            return False
        st.current_confidence = confidence
        logger.info(
            "[NAMER] renamed %s -> %s (conf=%.2f)", speaker_id, name, confidence
        )
        try:
            from .indication import IndicationKind, get_indication

            ind = get_indication()
            if ind is not None:
                ind.notify(
                    IndicationKind.GUEST_RENAMED,
                    body=f"{speaker_id} → {name}",
                )
        except Exception:  # noqa: BLE001
            logger.warning("guest rename indication notify failed", exc_info=True)
        return True

    # -- run loop --------------------------------------------------------

    async def run(self) -> None:
        """Drain the queue, buffer per-speaker turns, trigger prediction."""
        while True:
            record = await self._queue.get()
            self._ingest(record)
            if not self._should_trigger(record.speaker_id):
                continue
            await self._run_prediction(record.speaker_id)

    async def _run_prediction(self, speaker_id: str) -> None:
        st = self._state(speaker_id)
        utterances = [text for text, _ts in st.buffer]
        # Reset counters FIRST so concurrent enqueues during the LLM call
        # accumulate against the next trigger window, not this one.
        st.new_turns_since_check = 0
        st.last_check_at = self._clock()
        try:
            result = await asyncio.wait_for(
                self._predict_fn(speaker_id, utterances),
                timeout=self._settings.speaker_namer_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning(
                "namer: predict_fn timed out after %.1fs for %s",
                self._settings.speaker_namer_timeout_seconds,
                speaker_id,
            )
            return
        except Exception:  # noqa: BLE001
            logger.exception("namer: predict_fn raised for %s", speaker_id)
            return
        name, confidence = result
        self._apply_prediction(speaker_id, name, confidence)


# ---------------------------------------------------------------------
# OpenRouter predictor adapter
# ---------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You infer a speaker's first name from a short transcript of their "
    "recent utterances. Look for self-introductions ('I'm Alice'), other "
    "speakers addressing them, nicknames, or clear contextual cues. "
    "Reply with ONLY compact JSON of the form "
    '{"name": "<first name or null>", "confidence": <float 0..1>}. '
    'Use null for "name" when you are not sure. Never include any other text.'
)


def _parse_prediction_json(raw: str) -> PredictResult:
    """Fail-closed JSON parser for the predictor's reply.

    Accepts either a bare JSON object or a JSON object embedded in other
    text (first {...} block wins). Returns (None, 0.0) on any failure.
    """
    text = raw.strip()
    if not text:
        return None, 0.0
    # Extract first {...} block to tolerate stray prose wrapping.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, 0.0
    blob = text[start : end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None, 0.0
    if not isinstance(data, dict):
        return None, 0.0
    name_raw = data.get("name")
    conf_raw = data.get("confidence")
    if conf_raw is None:
        return None, 0.0
    try:
        confidence = float(conf_raw)
    except (TypeError, ValueError):
        return None, 0.0
    if confidence != confidence:  # NaN
        return None, 0.0
    confidence = max(0.0, min(1.0, confidence))
    if name_raw is None:
        return None, 0.0
    if not isinstance(name_raw, str):
        return None, 0.0
    stripped = name_raw.strip()
    if not stripped or stripped.lower() in {"null", "none", "unknown"}:
        return None, 0.0
    try:
        clean = sanitize_label(stripped)
    except LabelValidationError:
        return None, 0.0
    return clean, confidence


def maybe_build_namer(
    settings: "Settings",
    gallery: "SpeakerGallery | None",
    openrouter_api_key: str | None,
    clock: Callable[[], float] = time.monotonic,
) -> "SpeakerNamer | None":
    """Return a configured SpeakerNamer, or None when disabled/unconfigured.

    Gates (all must pass):
    - settings.speaker_namer_enabled is True
    - settings.speaker_id_enabled is True (no point naming without acoustic tags)
    - openrouter_api_key is non-empty
    - gallery is not None
    """
    if not settings.speaker_namer_enabled:
        return None
    if not settings.speaker_id_enabled:
        return None
    if not openrouter_api_key:
        return None
    if gallery is None:
        return None
    predictor = build_openrouter_predictor(
        api_key=openrouter_api_key,
        model=settings.speaker_namer_model,
        timeout=settings.speaker_namer_timeout_seconds,
    )
    return SpeakerNamer(gallery, settings, predictor, clock=clock)


def build_openrouter_predictor(
    *,
    api_key: str,
    model: str,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PredictFn:
    """Return an async predictor that asks OpenRouter for a name guess.

    Issues a single non-streaming /chat/completions POST and reads
    ``choices[0].message.content`` as JSON. Any network/parse failure
    yields (None, 0.0) so SpeakerNamer.run() stays alive.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/onelenyk/heare",
        "X-Title": "heare",
    }

    async def _predict(speaker_id: str, utterances: list[str]) -> PredictResult:
        if not utterances:
            return None, 0.0
        numbered = "\n".join(
            f"turn {i + 1}: {text}" for i, text in enumerate(utterances)
        )
        user_prompt = (
            f"Speaker tag: {speaker_id}\n"
            f"Recent utterances:\n{numbered}\n\n"
            "Return the JSON now."
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 200,
        }
        kwargs: dict = {"timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.post(
                    _OPENROUTER_URL, json=payload, headers=headers
                )
                if resp.status_code != 200:
                    logger.warning(
                        "namer predictor: openrouter HTTP %d", resp.status_code
                    )
                    return None, 0.0
                data = resp.json()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("namer predictor: openrouter call failed: %s", e)
            return None, 0.0
        try:
            raw = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None, 0.0
        if not isinstance(raw, str):
            return None, 0.0
        return _parse_prediction_json(raw)

    return _predict
