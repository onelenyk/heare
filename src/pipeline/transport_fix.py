from concurrent.futures import ThreadPoolExecutor

from pipecat.frames.frames import StartFrame
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.local.audio import (
    LocalAudioOutputTransport,
    LocalAudioTransport,
    LocalAudioTransportParams,
)

import pyaudio


class FixedLocalAudioOutputTransport(LocalAudioOutputTransport):
    """Overrides start() to add frames_per_buffer=20ms — fixes audio glitches in PyInstaller .app."""

    def __init__(self, py_audio: pyaudio.PyAudio, params: LocalAudioTransportParams):
        super().__init__(py_audio, params)

    async def start(self, frame: StartFrame):
        from pipecat.transports.base_output import BaseOutputTransport
        await BaseOutputTransport.start(self, frame)

        if self._out_stream:
            return

        self._sample_rate = self._params.audio_out_sample_rate or frame.audio_out_sample_rate

        frames_per_buffer = int(self._sample_rate / 100) * 2

        self._out_stream = self._py_audio.open(
            format=self._py_audio.get_format_from_width(2),
            channels=self._params.audio_out_channels,
            rate=self._sample_rate,
            frames_per_buffer=frames_per_buffer,
            output=True,
            output_device_index=self._params.output_device_index,
        )
        self._out_stream.start_stream()

        await self.set_transport_ready(frame)


class FixedLocalAudioTransport(LocalAudioTransport):
    """Uses FixedLocalAudioOutputTransport for output — fixes audio glitches in .app."""

    def output(self) -> FrameProcessor:
        if not self._output:
            self._output = FixedLocalAudioOutputTransport(self._pyaudio, self._params)
        return self._output
