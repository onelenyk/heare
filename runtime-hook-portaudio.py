import os
import sys

if getattr(sys, 'frozen', False):
    pa2 = os.path.join(
        os.path.dirname(sys.executable),  # Contents/MacOS/
        '..', 'Frameworks', 'libportaudio.2.dylib',
    )
    sd_target = os.path.join(
        os.path.dirname(sys.executable),
        '..', 'Resources',
        '_sounddevice_data', 'portaudio-binaries', 'libportaudio.dylib',
    )

    if os.path.exists(pa2) and not os.path.exists(sd_target):
        os.makedirs(os.path.dirname(sd_target), exist_ok=True)
        try:
            rel = os.path.relpath(pa2, os.path.dirname(sd_target))
            os.symlink(rel, sd_target)
        except OSError:
            pass
