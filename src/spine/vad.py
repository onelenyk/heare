from dataclasses import dataclass
from enum import Enum
import struct
import math
import time
from collections import deque

# Bound at import so a test can swap the clock without patching the
# stdlib module for everyone else.
_monotonic = time.monotonic


class EventKind(Enum):
    START = "start"
    END = "end"


@dataclass
class SpeechEvent:
    kind: EventKind
    utterance: bytes = b""


class EnergyVAD:
    """Segments 16 kHz mono int16 frames into utterances by RMS energy.

    Speech starts after `start_ms` of consecutive frames above `threshold_db`
    (dBFS); ends after `stop_ms` of consecutive frames below it. A ring of
    `preroll_ms` pre-speech frames is prepended so the first word is not
    clipped. `max_utterance_s` forces an END during continuous speech.

    Every threshold above is counted in FED frames, so a VAD that stops
    being fed simply freezes: `in_speech` stays True and `pending_frames`
    holds the tail of the sentence for as long as the silence lasts.
    Muting the microphone does exactly that (the audio layer drops frames
    before they reach here), and on unmute the next quiet stretch emits an
    END whose utterance is *pre-mute audio glued to post-unmute audio* —
    the words someone muted to avoid get transcribed and answered. So the
    stream's wall clock is checked too: a hole longer than `gap_ms`
    between two fed frames discards whatever was in flight
    (`drop_in_flight`). It is one `time.monotonic()` per frame, and it is
    correct even when nobody remembers to announce the mute.
    """

    def __init__(
        self,
        rate: int = 16000,
        frame_ms: int = 20,
        threshold_db: float = -38.0,
        start_ms: int = 120,
        stop_ms: int = 600,
        preroll_ms: int = 300,
        max_utterance_s: float = 30.0,
        gap_ms: int = 1000,
    ) -> None:
        self.rate = rate
        self.frame_ms = frame_ms
        self.threshold_db = threshold_db

        # Wall-clock hole that means "frames stopped arriving": deliberately
        # well above frame_ms and above the 600 ms stop window, because
        # frames can also arrive in a burst after the event loop stalls,
        # and dropping a live sentence over scheduling jitter would be a
        # worse bug than the one this guards. 0 disables the check.
        self.gap_ms = gap_ms
        self._last_feed_ts: float | None = None

        # Convert time thresholds to frame counts.
        # Frame count = (time_ms * rate / 1000) / (frame_ms * rate / 1000) = time_ms / frame_ms
        self.start_frames = max(1, start_ms // frame_ms)
        self.stop_frames = max(1, stop_ms // frame_ms)
        self.max_frames = max(1, int(max_utterance_s * 1000 // frame_ms))

        # Number of frames in preroll ring buffer.
        self.preroll_frames = max(1, preroll_ms // frame_ms)

        # Hysteresis counters: consecutive frames above/below threshold.
        self.above_threshold_count = 0
        self.below_threshold_count = 0

        # State tracking.
        self.in_speech = False
        self.speech_frame_count = 0

        # Ring buffer of preroll (pre-speech silence frames).
        self.preroll_buffer: deque[bytes] = deque(maxlen=self.preroll_frames)

        # Frames accumulated since potential speech onset.
        self.pending_frames: list[bytes] = []

        # Saved preroll for the next utterance.
        self.saved_preroll = b""

    def _rms_db(self, frame: bytes) -> float:
        """Calculate RMS in dBFS for a 16-bit signed mono frame.

        dBFS = 20*log10(rms/32768.0), guards rms==0 -> -120.0
        """
        num_samples = len(frame) // 2
        if num_samples == 0:
            return -120.0

        samples = struct.unpack(f"<{num_samples}h", frame)
        sum_sq = sum(s * s for s in samples)
        mean_sq = sum_sq / num_samples
        rms = math.sqrt(mean_sq)

        if rms == 0:
            return -120.0
        return 20 * math.log10(rms / 32768.0)

    def feed(self, frame: bytes) -> SpeechEvent | None:
        """Process one frame, return SpeechEvent or None.

        Returns START exactly once at onset (after start_ms of loud frames).
        Returns END exactly once with utterance bytes (preroll + speech).
        Otherwise returns None.

        A frame arriving more than `gap_ms` after the previous one means
        the microphone stream had a hole (mute, device switch, a stalled
        producer). Whatever was in flight belongs to the other side of
        that hole and is discarded before this frame is looked at — no
        END is emitted for it, because the audio nobody could hear is
        exactly the audio nobody asked to have transcribed.
        """
        if self.gap_ms:
            now = _monotonic()
            last = self._last_feed_ts
            self._last_feed_ts = now
            if last is not None and (now - last) * 1000.0 >= self.gap_ms:
                self.drop_in_flight()

        energy_db = self._rms_db(frame)
        is_loud = energy_db > self.threshold_db

        # Update hysteresis counters.
        if is_loud:
            self.above_threshold_count += 1
            self.below_threshold_count = 0
        else:
            self.below_threshold_count += 1
            self.above_threshold_count = 0

        if not self.in_speech:
            # In silence/deciding phase.
            if not self.pending_frames and not is_loud:
                # Still in pure silence, maintain preroll buffer.
                self.preroll_buffer.append(frame)
            else:
                # Potential speech onset: save preroll on first loud frame.
                if not self.pending_frames:
                    self.saved_preroll = b"".join(self.preroll_buffer)
                self.pending_frames.append(frame)
                if not is_loud:
                    # The onset died before start_ms — a blip, not speech.
                    # Fold the candidate frames back into the preroll ring
                    # (bounded by its maxlen) and return to pure silence;
                    # without this, one chair creak makes pending_frames
                    # grow forever and freezes the preroll.
                    self.preroll_buffer.extend(self.pending_frames)
                    self.pending_frames.clear()
                    self.saved_preroll = b""

            # Check for speech onset after start_ms of loud frames.
            if self.above_threshold_count >= self.start_frames:
                self.in_speech = True
                self.speech_frame_count = 0
                return SpeechEvent(kind=EventKind.START)
        else:
            # In speech: accumulate frames.
            self.pending_frames.append(frame)
            self.speech_frame_count += 1

            # Check for forced end due to max duration.
            if self.speech_frame_count >= self.max_frames:
                utterance = self.saved_preroll + b"".join(self.pending_frames)
                self.pending_frames.clear()
                self.saved_preroll = b""
                self.speech_frame_count = 0
                self.in_speech = False
                self.above_threshold_count = 0
                self.below_threshold_count = 0
                return SpeechEvent(kind=EventKind.END, utterance=utterance)

            # Check for speech offset after stop_ms of quiet frames.
            if self.below_threshold_count >= self.stop_frames:
                utterance = self.saved_preroll + b"".join(self.pending_frames)
                self.pending_frames.clear()
                self.saved_preroll = b""
                self.speech_frame_count = 0
                self.in_speech = False
                self.above_threshold_count = 0
                self.below_threshold_count = 0
                return SpeechEvent(kind=EventKind.END, utterance=utterance)

        return None

    def drop_in_flight(self) -> int:
        """Throw away the utterance being collected; return bytes dropped.

        Safe to call at any point, mid-utterance included: it touches only
        the per-utterance state (pending frames, saved preroll, the preroll
        ring, the hysteresis counters), never the thresholds, so the next
        frame is judged exactly as a fresh VAD would judge it. No event is
        returned — the audio is discarded, not ended.

        The preroll ring is emptied along with the rest on purpose: those
        frames are pre-gap audio too, and keeping them would prepend the
        muted words to the next utterance, which is the whole bug.

        The caller is left holding a START with no matching END if the drop
        lands mid-speech; that is the honest report — the utterance never
        finished — and callers that count starts must treat this as one.
        """
        dropped = sum(len(f) for f in self.pending_frames) + len(self.saved_preroll)
        self.above_threshold_count = 0
        self.below_threshold_count = 0
        self.in_speech = False
        self.speech_frame_count = 0
        self.preroll_buffer.clear()
        self.pending_frames.clear()
        self.saved_preroll = b""
        return dropped

    def reset(self) -> None:
        """Reset VAD state."""
        # Same clearing, kept as one implementation: reset() is the
        # no-questions-asked version of drop_in_flight().
        self.drop_in_flight()
        self._last_feed_ts = None


def loud_ms(
    pcm: bytes,
    *,
    rate: int = 16000,
    frame_ms: int = 20,
    threshold_db: float = -38.0,
) -> int:
    """Milliseconds of audio above the threshold — a cheap pre-STT gate.

    A VAD utterance is mostly preroll and trailing silence; Whisper
    hallucinates on such input («Дякую за перегляд!» on dead air is a
    documented failure of this deployment). Callers skip transcription
    when the genuinely loud part is shorter than a spoken word.
    """
    probe = EnergyVAD(rate=rate, frame_ms=frame_ms, threshold_db=threshold_db)
    frame_bytes = (rate * frame_ms // 1000) * 2
    loud = 0
    for off in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        if probe._rms_db(pcm[off:off + frame_bytes]) > threshold_db:
            loud += frame_ms
    return loud
