from dataclasses import dataclass
from enum import Enum
import struct
import math
from collections import deque


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
    ) -> None:
        self.rate = rate
        self.frame_ms = frame_ms
        self.threshold_db = threshold_db

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
        """
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

    def reset(self) -> None:
        """Reset VAD state."""
        self.above_threshold_count = 0
        self.below_threshold_count = 0
        self.in_speech = False
        self.speech_frame_count = 0
        self.preroll_buffer.clear()
        self.pending_frames.clear()
        self.saved_preroll = b""


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
