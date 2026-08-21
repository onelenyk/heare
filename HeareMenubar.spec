# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Heare menu bar app (.app bundle)."""

import os
from PyInstaller.utils.hooks import collect_all

datas = [
    ('src/frontend/dist/index.html', 'src/frontend'),
    ('src/frontend/dist/assets', 'src/frontend/dist/assets'),
    ('prompts', 'prompts'),
    ('skills', 'skills'),
    # Roles are read from `<root>/roles` — inside a bundle that resolves
    # to sys._MEIPASS, so without this line the shipped app has no
    # мітинг, вчитель, інтерв'ю or суфлер at all. It does not fail: the
    # loader finds an empty directory, the log says "spine roles loaded:"
    # with nothing after it, and every trigger phrase is simply never
    # matched. Added 16 August, packaged never.
    ('roles', 'roles'),
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

# `_sounddevice_data` is the one that matters and the one that was
# missing. `sounddevice` is a single module, not a package, so
# collect_all finds no libraries under it — the PortAudio dylib lives in
# a separate top-level data directory beside it. Until pipecat was
# dropped on 17 August something in its tree pulled libportaudio in
# transitively, and the runtime hook found it in Frameworks and linked
# it into place. With that gone, the built app raised
# "PortAudio library not found" at boot and released its audio streams:
# a bundle that starts, serves its dashboard, answers /state with 200 —
# and is deaf.
#
# Collected by name here so it does not depend on anyone else's
# dependency tree again.
for mod in ('aiohttp', 'websockets', 'httpx', 'edge_tts',
            'sounddevice', '_sounddevice_data'):
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

# Deduplicate PortAudio — keep the first copy, not none of them.
#
# This used to drop every entry matching the sounddevice path, because a
# second copy always arrived through pipecat's tree as
# Frameworks/libportaudio.2.dylib and the runtime hook symlinked it into
# place. Dropping pipecat took that copy away, and this line then removed
# the only one left: the app built clean, launched, served its dashboard
# and raised "PortAudio library not found" at boot — deaf, with nothing
# in the build output to say so.
#
# Written as "keep the first" rather than "keep this specific path" so
# that neither copy disappearing can produce that again.
seen_portaudio = False
final_binaries = []
for item in a.binaries:
    dest = item[0]
    if 'portaudio-binaries/libportaudio.dylib' in dest:
        if seen_portaudio:
            continue
        seen_portaudio = True
    final_binaries.append(item)
a.binaries = final_binaries

if not seen_portaudio:
    raise SystemExit(
        "HeareMenubar.spec: no PortAudio library collected. The built app "
        "would start, serve the dashboard and be unable to hear or speak. "
        "Check that _sounddevice_data is in the collect list above."
    )

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
