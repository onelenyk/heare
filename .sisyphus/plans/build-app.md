# Build — Self-Contained macOS App

## Goal
One `.app` file. Download → drag to Applications → double-click → works.
No brew. No terminal. No "install portaudio".

## How
Use py2app to bundle Python + all deps + portaudio C library into a single `.app`.

```
Heare.app (40-80 MB)
├── Python + all pip packages
├── libportaudio.2.dylib   ← bundled C library
├── src/                    ← our code
├── src/frontend/index.html
└── Info.plist
```

## Build script

Install build tools (once):
```bash
pip install py2app
```

Build the app:
```bash
python setup.py py2app
```

Output: `dist/Heare.app`

## What it does on launch
1. `Heare.app/Contents/MacOS/python -m src.main start`
2. Daemon starts → HTTP server on :9778
3. Opens `http://127.0.0.1:9778/` in browser
4. First launch: settings panel → enter API keys → use

## How portaudio gets bundled
py2app automatically detects `pyaudio` → finds its dependency `libportaudio.2.dylib` → copies it into the `.app` bundle → patches the library search path. No manual steps.

## Verification
- [ ] Build on a clean macOS (no portaudio installed)
- [ ] Drag `Heare.app` to /Applications
- [ ] Double-click → browser opens at :9778
- [ ] Audio works without `brew install portaudio`
