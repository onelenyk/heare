# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Heare menu bar app (.app bundle)."""

import os
from PyInstaller.utils.hooks import collect_all

datas = [
    ('src/frontend/dist/index.html', 'src/frontend'),
    ('src/frontend/dist/assets', 'src/frontend/dist/assets'),
    ('src/frontend/onboarding.html', 'src/frontend'),
    ('prompts', 'prompts'),
    ('skills', 'skills'),
]

binaries = [
    ('/opt/homebrew/opt/libomp/lib/libomp.dylib', '.'),
]

hiddenimports = [
    'src.main',
    'src.menubar',
    'src.api',
    'src.memory',
    'queue',
    'logging.handlers',
    'pyloudnorm',
    'scipy.signal',
    'scipy.signal.firwin',
    'rumps',
    'pyobjc-core',
    'pyobjc-framework-cocoa',
]

for mod in ('pipecat', 'aiohttp', 'websockets', 'httpx', 'edge_tts', 'sounddevice', 'httpx'):
    tmp = collect_all(mod)
    datas += tmp[0]
    binaries += tmp[1]
    hiddenimports += tmp[2]

a = Analysis(
    ['src/menubar.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime-hook-portaudio.py'],
    # Measured on the 15 July bundle: 394 MB, of which 111 MB was llvmlite
    # — an LLVM JIT that never runs. It arrives as
    # llvmlite <- numba <- resampy <- pipecat, but pipecat's default
    # resampler is SOXR (pipecat/audio/utils.py:53) and soxr is already
    # bundled; resampy_resampler is a separate module nothing here selects.
    #
    # The transformers stack (~28 MB) looked droppable — we run
    # turn_end="sentence" and never load a local model. It is not.
    # pipecat's own user_turn_strategies.py imports local_smart_turn_v3
    # at module level, and that imports transformers at module level, so
    # anything using UserTurnStrategies pays for it. Excluding it built a
    # 187 MB bundle that died on launch. Left in deliberately; removing
    # it needs a patch to pipecat, not a line here.
    #
    # onnxruntime (59 MB) stays: Silero VAD imports it. scipy (35 MB)
    # stays: pyloudnorm plus our own scipy.signal.firwin. nltk (11 MB)
    # stays, unwillingly — pipecat/utils/string.py imports it at module
    # level, so there is no excluding it without patching pipecat.
    #
    # PyInstaller does not check excludes at build time. Anything removed
    # here that turns out to be imported lazily fails on the user's desk,
    # not on ours — so every change to this list gets a launch and a
    # spoken exchange before it ships.
    excludes=[
        'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'matplotlib', 'notebook', 'jupyter', 'torch',
        # 111 MB of JIT for a resampler we do not use
        'numba', 'llvmlite', 'resampy',
        # dev tooling that has no business in a shipped app
        'mypy', 'mypyc', 'basedpyright', 'pytest',
    ],
    noarchive=False,
    optimize=0,
)

# Deduplicate PortAudio
final_binaries = []
for item in a.binaries:
    dest = item[0]
    if 'portaudio-binaries/libportaudio.dylib' in dest:
        continue
    final_binaries.append(item)
a.binaries = final_binaries

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Heare',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['Heare.icns'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Heare',
)

app = BUNDLE(
    coll,
    name='Heare.app',
    icon='Heare.icns',
    bundle_identifier='com.heare.app',
    info_plist={
        'NSMicrophoneUsageDescription': 'Heare needs microphone access to hear your voice.',
        'LSUIElement': True,
    },
)
