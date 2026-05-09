"""ONNX Runtime wrapper around the YAMNet AudioSet classifier.

YAMNet exposes the canonical 521-class AudioSet ontology. Two ONNX
variants exist in the wild:

* **Embedded / mel-input** (Qualcomm's qai-hub release): the model
  graph starts at the convolutional stack — the caller is expected to
  produce the log-mel spectrogram and feed it as a ``[1, 1, 96, 64]``
  tensor.
* **End-to-end / waveform-input**: produced by running ``tf2onnx`` on
  the official TF Hub model. Accepts raw 1-D float32 PCM directly.

This wrapper targets the **mel-input** variant because that artefact is
already published and small (~14 MB float). The log-mel transform is
re-implemented in NumPy here so we don't drag TensorFlow into the
runtime. Parameters mirror the reference ``yamnet_params.py`` exactly:

    SAMPLE_RATE                = 16000 Hz
    STFT_WINDOW_LENGTH_SECONDS = 0.025  → 400 samples
    STFT_HOP_SECONDS           = 0.010  → 160 samples
    FFT_LENGTH                 = 512    → 257 magnitude bins
    MEL_BANDS                  = 64
    MEL_MIN_HZ                 = 125
    MEL_MAX_HZ                 = 7500
    LOG_OFFSET                 = 0.001

The patch length is fixed at 96 frames × 64 mel bins, which corresponds
to exactly 15 360 input samples (0.96 s) zero-padded to 15 600 samples
on the way into the STFT.

If you instead want the waveform-input variant, run the conversion::

    pip install tensorflow tensorflow-hub tf2onnx
    python -m tf2onnx.convert \\
        --saved-model "$(python - <<PY
import tensorflow_hub as hub, os
print(os.path.dirname(hub.resolve('https://tfhub.dev/google/yamnet/1')))
PY
)" \\
        --output ~/.heare/models/yamnet.onnx \\
        --opset 13

…and switch this module to feed raw waveform straight to the session.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


class YamnetClassifier:
    """Thin wrapper around an :class:`onnxruntime.InferenceSession`.

    Lazy imports keep ``onnxruntime`` and ``numpy`` out of the import
    graph for callers that never instantiate the class (the factory in
    :mod:`src.audio_event.observer` catches :class:`ImportError`).
    """

    SAMPLE_RATE: int = 16000
    WINDOW_SAMPLES: int = 15360  # 0.96 s × 16 kHz
    _STFT_FRAME: int = 400      # 25 ms × 16 kHz
    _STFT_HOP: int = 160        # 10 ms × 16 kHz
    _FFT_LENGTH: int = 512
    _MEL_BANDS: int = 64
    _MEL_MIN_HZ: float = 125.0
    _MEL_MAX_HZ: float = 7500.0
    _LOG_OFFSET: float = 0.001
    _PATCH_FRAMES: int = 96

    def __init__(self, model_path: Path) -> None:
        if not model_path.exists():
            raise FileNotFoundError(f"YAMNet model not found at {model_path}")
        import numpy as np
        import onnxruntime as ort

        opts = ort.SessionOptions()
        # Single-threaded inference keeps CPU contention with the audio loop
        # negligible; the observer offloads each call to its own worker thread.
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
            sess_options=opts,
        )
        inputs = self._session.get_inputs()
        if not inputs:
            raise RuntimeError(f"YAMNet model at {model_path} exposes no inputs")
        self._input_name = inputs[0].name
        self._mel_filterbank: "np.ndarray" = self._build_mel_filterbank(np)
        # Periodic Hann window — matches ``tf.signal.hann_window(periodic=True)``.
        self._hann = (0.5 - 0.5 * np.cos(
            2.0 * np.pi * np.arange(self._STFT_FRAME) / self._STFT_FRAME
        )).astype(np.float32)

    @classmethod
    def _build_mel_filterbank(cls, np_):
        """Triangular HTK-style mel filterbank — ``[FFT_BINS, MEL_BANDS]``.

        Mirrors ``tf.signal.linear_to_mel_weight_matrix``: place
        ``MEL_BANDS + 2`` linearly-spaced points on the mel axis between
        ``MEL_MIN_HZ`` and ``MEL_MAX_HZ``, convert back to Hz, and build
        triangular filters whose left/right edges sit on adjacent
        points.
        """
        num_bins = cls._FFT_LENGTH // 2 + 1
        spec_freqs = np_.linspace(0.0, cls.SAMPLE_RATE / 2, num_bins)
        lower_mel = cls._hz_to_mel(cls._MEL_MIN_HZ)
        upper_mel = cls._hz_to_mel(cls._MEL_MAX_HZ)
        edges_mel = np_.linspace(lower_mel, upper_mel, cls._MEL_BANDS + 2)
        edges_hz = cls._mel_to_hz(edges_mel)
        weights = np_.zeros((num_bins, cls._MEL_BANDS), dtype=np_.float32)
        for i in range(cls._MEL_BANDS):
            lower, center, upper = edges_hz[i], edges_hz[i + 1], edges_hz[i + 2]
            for j, f in enumerate(spec_freqs):
                if lower < f < center:
                    weights[j, i] = (f - lower) / (center - lower)
                elif center <= f < upper:
                    weights[j, i] = (upper - f) / (upper - center)
        return weights

    @staticmethod
    def _hz_to_mel(hz):
        import numpy as np

        return 2595.0 * np.log10(1.0 + hz / 700.0)

    @staticmethod
    def _mel_to_hz(mel):
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    def _waveform_to_log_mel(self, waveform: "np.ndarray") -> "np.ndarray":
        """Convert a 0.96 s waveform to a ``[96, 64]`` log-mel patch."""
        import numpy as np

        # Zero-pad so the last STFT window completes — equivalent to
        # ``tf.signal.stft(..., pad_end=True)`` for our 96-frame target.
        target_len = (self._PATCH_FRAMES - 1) * self._STFT_HOP + self._STFT_FRAME
        if waveform.shape[0] < target_len:
            waveform = np.pad(waveform, (0, target_len - waveform.shape[0]))

        # Frame and window.
        starts = np.arange(self._PATCH_FRAMES) * self._STFT_HOP
        frames = np.stack(
            [waveform[s : s + self._STFT_FRAME] for s in starts]
        )
        frames = frames * self._hann

        # Magnitude spectrogram (not power) — matches ``tf.abs(tf.signal.stft(...))``.
        spec = np.abs(np.fft.rfft(frames, n=self._FFT_LENGTH, axis=-1))

        # Mel projection + log offset.
        mel = spec @ self._mel_filterbank
        log_mel = np.log(mel + self._LOG_OFFSET)
        return log_mel.astype(np.float32)

    def classify(self, waveform: "np.ndarray") -> list[tuple[int, float]]:
        """Run inference on a single 0.96 s window.

        Returns 521 ``(class_index, score)`` tuples sorted descending
        by score. The caller filters through the curated allowlist.
        """
        import numpy as np

        if waveform.shape != (self.WINDOW_SAMPLES,):
            raise ValueError(
                f"YAMNet expects a 1-D float32 waveform of length "
                f"{self.WINDOW_SAMPLES}, got shape {waveform.shape}"
            )
        if waveform.dtype != np.float32:
            waveform = waveform.astype(np.float32, copy=False)

        log_mel = self._waveform_to_log_mel(waveform)
        # Add batch + channel dims: ``[1, 1, 96, 64]``.
        model_input = log_mel[np.newaxis, np.newaxis, :, :]
        outputs = self._session.run(None, {self._input_name: model_input})
        logits = outputs[0]
        if logits.ndim == 2:
            logits = logits[0]
        # The Qualcomm export emits raw logits; the original Google YAMNet
        # emits softmax probabilities. Normalise here so callers can use a
        # single ``[0.0, 1.0]`` threshold regardless of which artefact ships.
        shifted = logits - logits.max()
        exp = np.exp(shifted)
        probs = exp / exp.sum()
        order = probs.argsort()[::-1]
        return [(int(i), float(probs[i])) for i in order]


__all__ = ["YamnetClassifier"]
