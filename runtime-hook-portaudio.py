import os
import sys

if getattr(sys, 'frozen', False):
    base = os.path.dirname(sys.executable)
    if not os.path.exists(os.path.join(base, 'pyaudio')):
        base = os.path.join(base, '..', 'Frameworks')

    pa2 = os.path.join(base, 'libportaudio.2.dylib')
    sd_dir = os.path.join(base, '_sounddevice_data', 'portaudio-binaries')
    target = os.path.join(sd_dir, 'libportaudio.dylib')

    if os.path.exists(pa2) and not os.path.exists(target):
        os.makedirs(sd_dir, exist_ok=True)
        try:
            os.symlink(pa2, target)
        except OSError:
            pass
