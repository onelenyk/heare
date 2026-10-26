"""The shape of the firmware, enforced on the host.

The panel is not a Python distribution and must never import ``heare``.
The pure modules must also stay runnable on a laptop, so they import no
``machine``/``network``/``esp``. Every firmware file is compiled with
CPython, which catches syntax errors before a board is plugged in.

CPython having a module does not mean the board has it. The import guard
below allows only the documented MicroPython built-ins and the hardware
modules the panel is expected to use on the ESP32-C3.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parents[2] / "firmware"
PURE = {
    "http.py",
    "sse.py",
    "state.py",
    "config.py",
    "render.py",
    "portal.py",
    "viewport.py",
}
HARDWARE = {"machine", "network", "esp"}

# MicroPython's built-in and ESP32 hardware modules, per its documentation.
# Anything else (dataclasses, typing, functools, enum, __future__) is absent
# on the board even though the host CPython has it.
MICROPYTHON = {
    "array",
    "asyncio",
    "binascii",
    "bluetooth",
    "builtins",
    "cmath",
    "collections",
    "errno",
    "gc",
    "gzip",
    "hashlib",
    "heapq",
    "io",
    "json",
    "marshal",
    "math",
    "os",
    "platform",
    "random",
    "re",
    "select",
    "socket",
    "ssl",
    "struct",
    "sys",
    "time",
    "weakref",
    "zlib",
    "_thread",
    "machine",
    "micropython",
    "network",
    "esp",
    "esp32",
    "framebuf",
    "ssd1306",
    "sh1106",
    "utime",
}


def _root() -> Path:
    override = os.environ.get("HEARE_FIRMWARE_ROOT")
    return Path(override).resolve() if override else FIRMWARE


def _files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _modules(path: Path) -> list[str]:
    """Every absolute import in a file; relative imports are local by definition."""
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def _imports(path: Path) -> set[str]:
    return set(_modules(path))


def _is_local(module: str) -> bool:
    return module == "heare_panel" or module.startswith("heare_panel.")


def _is_micropython(module: str) -> bool:
    return _is_local(module) or module.split(".")[0] in MICROPYTHON


def _unavailable(root: Path) -> list[str]:
    return [
        f"{_relative(path, root)}: {module}"
        for path in _files(root)
        for module in _modules(path)
        if not _is_micropython(module)
    ]


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def test_firmware_never_imports_heare():
    root = _root()
    guilty = [
        f"{_relative(path, root)}: {module}"
        for path in _files(root)
        for module in _imports(path)
        if module == "heare" or module.startswith("heare.")
    ]
    assert not guilty, f"firmware imports heare: {guilty}"


def test_pure_modules_import_no_hardware():
    root = _root()
    guilty = [
        f"{_relative(path, root)}: {module}"
        for path in _files(root)
        if path.name in PURE
        for module in _imports(path)
        if module.split(".")[0] in HARDWARE
    ]
    assert not guilty, f"a pure module imports hardware: {guilty}"


def test_every_firmware_file_imports_only_micropython_modules():
    root = _root()
    guilty = _unavailable(root)
    assert not guilty, f"firmware imports modules MicroPython lacks: {guilty}"


def test_the_import_guard_rejects_modules_micropython_lacks(tmp_path):
    (tmp_path / "bad.py").write_text(
        "from __future__ import annotations\n"
        "import dataclasses\n"
        "import functools\n"
        "import typing\n"
        "import enum\n"
        "import json\n"
        "from . import local\n"
        "from heare_panel import state\n",
        encoding="utf-8",
    )
    failures = _unavailable(tmp_path)
    for module in ("__future__", "dataclasses", "functools", "typing", "enum"):
        assert any(line.endswith(f": {module}") for line in failures), failures
    assert not any(line.endswith(": json") for line in failures), failures
    assert not any("local" in line or "heare_panel" in line for line in failures)


def test_every_firmware_file_compiles_with_cpython():
    root = _root()
    failures = []
    for path in _files(root):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as error:
            failures.append(f"{_relative(path, root)}: {error}")
    assert not failures, f"firmware does not compile: {failures}"
