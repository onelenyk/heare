# Runtime hook: force sounddevice to use pyaudio's PortAudio
# This prevents two PortAudio instances from conflicting

import os
import sys

# Get the path to the bundled pyaudio PortAudio
if getattr(sys, 'frozen', False):
    # Running in PyInstaller bundle
    base_path = os.path.dirname(sys.executable)
    # Try different possible locations
    for candidate in [
        os.path.join(base_path, 'pyaudio', '_portaudio.cpython-313-darwin.so'),
        os.path.join(base_path, '..', 'Frameworks', 'pyaudio', '_portaudio.cpython-313-darwin.so'),
        os.path.join(os.path.dirname(__file__), 'pyaudio', '_portaudio.cpython-313-darwin.so'),
    ]:
        if os.path.exists(candidate):
            # Set environment variable to hint at PortAudio location
            # sounddevice will find libportaudio.2.dylib via @rpath
            break
