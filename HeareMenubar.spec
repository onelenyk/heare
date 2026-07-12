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
    excludes=[
        'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'matplotlib', 'notebook', 'jupyter', 'torch',
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
