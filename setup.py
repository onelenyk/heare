from setuptools import setup

APP = ["src/main.py"]
APP_NAME = "Heare"

DATA_FILES = ["src/frontend/index.html"]

OPTIONS = {
    "argv_emulation": False,
    "site_packages": True,
    "packages": [
        "src",
        "pipecat",
        "aiohttp",
        "websockets",
        "httpx",
        "sounddevice",
        "pyaudio",
        "anthropic",
        "edge_tts",
        "aiosqlite",
        "textual",
    ],
    "includes": [
        "src.main",
        "src.config",
        "src.api",
        "src.state",
        "src.daemon.events",
        "src.agent.llm.switchable",
        "src.agent.llm.providers",
        "src.agent.llm.prompt_sections",
        "src.agent.tools.system",
        "src.agent.tools.definitions",
        "src.pipeline.build",
    ],
    "excludes": [
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "scipy",
        "notebook",
        "jupyter",
    ],
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "com.heare.app",
        "CFBundleVersion": "0.1.1",
        "CFBundleShortVersionString": "0.1.1",
        "CFBundleDevelopmentRegion": "uk",
        "NSMicrophoneUsageDescription": "Heare needs microphone access to listen to voice commands.",
        "CFBundleExecutable": "main",
        "CFBundleIconFile": False,
    },
}

setup(
    name=APP_NAME,
    version="0.1.1",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
