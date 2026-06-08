# -*- mode: python ; coding: utf-8 -*-
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
hiddenimports = ['src.main', 'src.api', 'src.memory', 'pyloudnorm', 'scipy.signal', 'scipy.signal.firwin']
tmp_ret = collect_all('pipecat')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('aiohttp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('websockets')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('httpx')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('edge_tts')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('sounddevice')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime-hook-portaudio.py'],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'matplotlib', 'notebook', 'jupyter', 'torch'],
    noarchive=False,
    optimize=0,
)

# Deduplicate PortAudio: remove sounddevice's copy, keep only brew's libportaudio.2.dylib
final_binaries = []
for item in a.binaries:
    dest = item[0]
    if 'portaudio-binaries/libportaudio.dylib' in dest:
        continue  # Skip sounddevice's PortAudio
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
    console=True,
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
    },
)
