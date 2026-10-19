"""What can run an agent, how it is found, and how it is set up.

The assistant does not drive a model itself; it starts an agent CLI that
speaks ACP. Which CLI that is belongs to one table (``KNOWN``), so the
hands that spawn it and the page that configures it read the same answer.

A daemon started by launchd inherits a minimal ``PATH``. A bare
``opencode`` is therefore found only when the person happens to have it in
one of the places launchd sees — so ``resolve`` looks in the usual install
directories too, and hands spawns an absolute path.

Nothing here returns a secret. Sign-in for opencode is the *names* of the
providers in its auth store; a key is reported as present or absent, never
as a value. ``env`` copies the process environment and overlays only what
a chosen sign-in needs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypedDict

from heare import keys

# How a known CLI is started and what it is called on the page. The agent
# itself is an ACP server behind these commands; for Claude that server is
# an adapter fetched through npx, and ``binary`` is the CLI sign-in is read
# from rather than the process that is spawned.
KNOWN: dict[str, "Runner"] = {
    "opencode": {
        "binary": "opencode",
        "command": ["opencode", "acp"],
        "label": "OpenCode",
    },
    "claude": {
        "binary": "claude",
        "command": ["npx", "-y", "@agentclientprotocol/claude-agent-acp@0.81.2"],
        "label": "Claude Code",
    },
}

# CLI installers write here and do not always touch the shell's PATH.
_EXTRA_DIRS = (
    "~/.opencode/bin",
    "~/.superset/bin",
    "~/.local/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.volta/bin",
)

# ``opencode models`` prints one ``provider/model`` per line.
_MODEL_LINE = re.compile(r"^[\w.-]+/\S+$")
_CLAUDE_ALIASES = ("fable", "opus", "sonnet", "haiku")

# Listing models spawns the CLI, so an answer is kept for a while. A person
# who signs in to a new provider sees it after this window, not a restart.
_MODELS_TTL_S = 300.0
_MODELS_CACHE: dict[str, tuple[float, list["ModelGroup"]]] = {}


class Runner(TypedDict):
    binary: str
    command: list[str]
    label: str


class Model(TypedDict):
    id: str
    name: str


class ModelGroup(TypedDict):
    group: str
    models: list[Model]


class Login(TypedDict):
    ok: bool
    summary: str
    detail: str


class Check(TypedDict):
    name: str
    ok: bool
    detail: str


class Health(TypedDict):
    ok: bool
    checks: list[Check]


def resolve(binary: str) -> str | None:
    """An absolute path to *binary*, or None.

    An absolute path is taken as given. A bare name is looked for on PATH,
    then in the directories ``launchd`` does not put there.
    """
    if not binary:
        return None
    if os.path.isabs(binary):
        return binary if os.path.exists(binary) else None
    found = shutil.which(binary)
    if found:
        return found
    for directory in _EXTRA_DIRS:
        path = Path(os.path.expanduser(directory)) / binary
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return None


def detect() -> list[dict[str, str]]:
    """Every known runner this machine has: type, binary and its path."""
    found: list[dict[str, str]] = []
    for kind, runner in KNOWN.items():
        path = resolve(runner["binary"])
        if path:
            found.append({"type": kind, "binary": runner["binary"], "path": path})
    return found


def login(kind: str, binary: str | None = None) -> Login:
    """Whether *kind* is signed in, without reading a secret."""
    if kind == "opencode":
        return _opencode_login()
    if kind == "claude":
        return _claude_login(binary)
    return {
        "ok": False,
        "summary": "Unknown runner",
        "detail": f"no sign-in check for {kind}",
    }


def models(kind: str, binary: str | None = None) -> list[ModelGroup]:
    """The models *kind* accepts, grouped by provider and never fatal.

    opencode is asked; Claude Code takes a small set of aliases, so it is
    answered from the table rather than by starting it.
    """
    if kind == "claude":
        return [{
            "group": "Latest of each family",
            "models": [
                {"id": alias, "name": alias}
                for alias in _CLAUDE_ALIASES
            ],
        }]
    if kind != "opencode":
        return []

    target = resolve(binary or KNOWN["opencode"]["binary"])
    if target is None:
        return []
    now = time.monotonic()
    hit = _MODELS_CACHE.get(target)
    if hit is not None and now - hit[0] < _MODELS_TTL_S:
        return hit[1]
    groups = _opencode_models(target)
    _MODELS_CACHE[target] = (now, groups)
    return groups


def health(
    kind: str,
    binary: str | None,
    auth: str,
    auth_env: str,
    base_url: str,
) -> Health:
    """The three things a runner needs: a binary, a way in, a reachable URL."""
    entry = KNOWN.get(kind, KNOWN["opencode"])
    target = binary or entry["binary"]
    resolved = resolve(target)
    checks: list[Check] = [{
        "name": "binary",
        "ok": resolved is not None,
        "detail": resolved or f"{target} was not found",
    }]

    if auth == "login":
        state = login(kind, resolved)
        detail = state["summary"]
        if state["detail"]:
            detail = f"{detail} — {state['detail']}"
        checks.append({"name": "auth", "ok": state["ok"], "detail": detail})
    elif auth == "key":
        present = bool(auth_env) and keys.get(auth_env) is not None
        source = keys.source(auth_env) if auth_env else None
        where = f" ({source})" if present and source else ""
        checks.append({
            "name": "auth",
            "ok": present,
            "detail": (
                f"{auth_env or 'no variable'} is "
                f"{'set' if present else 'not set'}{where}"
            ),
        })
    else:
        checks.append({
            "name": "auth",
            "ok": False,
            "detail": f"unknown sign-in mode: {auth}",
        })

    if base_url:
        checks.append(_base_url_check(base_url))

    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def env(
    kind: str,
    auth: str,
    auth_env: str,
    base_url: str,
    model: str,
) -> dict[str, str]:
    """The environment for a spawned runner: the process's own, plus auth.

    A key is read through ``heare.keys`` and only ever placed in the copy,
    never logged. Claude Code prefers an API key in its environment over its
    own OAuth login, so a login run drops both Anthropic key variables — a
    person who chose "its own sign-in" should not be billed through a key
    some shell happened to export. For opencode a base URL is a provider
    option, not an environment variable; see ``opencode_config_patch``.
    """
    merged = dict(os.environ)

    if auth == "key" and auth_env:
        value = keys.get(auth_env)
        if value is not None:
            merged[auth_env] = value

    if auth == "login" and kind == "claude":
        merged.pop("ANTHROPIC_API_KEY", None)
        merged.pop("ANTHROPIC_AUTH_TOKEN", None)

    if base_url and kind == "claude":
        merged["ANTHROPIC_BASE_URL"] = base_url

    # ``model`` is accepted so both setup helpers share one signature; the
    # environment does not carry it.
    return merged


def opencode_config_patch(
    kind: str, base_url: str, model: str
) -> dict[str, dict[str, dict[str, object]]]:
    """The opencode config fragment that points a model's provider at a URL.

    A provider is the prefix of a model id (``deepseek/deepseek-chat``
    speaks to DeepSeek). Without a URL, or without a prefixed model, there
    is nothing to patch.
    """
    if kind != "opencode" or not base_url or "/" not in model:
        return {}
    provider = model.split("/", 1)[0]
    return {"provider": {provider: {"options": {"baseURL": base_url}}}}


def _probe(
    argv: list[str], timeout: float
) -> subprocess.CompletedProcess[str] | None:
    """Run a short read-only probe; a CLI that fails is an answer, not a crash."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _opencode_login() -> Login:
    path = Path.home() / ".local/share/opencode/auth.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    names = sorted(data) if isinstance(data, dict) else []
    if not names:
        return {
            "ok": False,
            "summary": "No providers signed in",
            "detail": "run `opencode auth login` in a terminal",
        }
    count = len(names)
    return {
        "ok": True,
        "summary": f"{count} provider{'s' if count != 1 else ''} signed in",
        "detail": ", ".join(names),
    }


def _claude_login(binary: str | None) -> Login:
    target = resolve(binary) if binary else resolve(KNOWN["claude"]["binary"])
    if target is None:
        return {
            "ok": False,
            "summary": "Not signed in",
            "detail": "run `claude auth login` in a terminal",
        }
    proc = _probe([target, "auth", "status"], 15)
    if proc is None or proc.returncode != 0:
        return {
            "ok": False,
            "summary": "Sign-in could not be checked",
            "detail": "run `claude auth login` in a terminal",
        }
    try:
        status = json.loads(proc.stdout or "")
    except ValueError:
        status = {}
    if not isinstance(status, dict):
        status = {}
    if status.get("loggedIn"):
        who = str(status.get("email") or status.get("orgName") or "signed in")
        method = status.get("authMethod") or "login"
        return {"ok": True, "summary": f"Signed in as {who}",
                "detail": f"via {method}"}
    return {
        "ok": False,
        "summary": "Not signed in",
        "detail": "run `claude auth login` in a terminal",
    }


def _opencode_models(binary: str) -> list[ModelGroup]:
    proc = _probe([binary, "models"], 20)
    output = (proc.stdout or "") if proc and proc.returncode == 0 else ""
    snapshot = _models_snapshot()
    groups: dict[str, ModelGroup] = {}
    seen: set[str] = set()
    for raw in output.splitlines():
        model_id = raw.strip()
        if not _MODEL_LINE.match(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        provider, _, rest = model_id.partition("/")
        meta = _snapshot_model(snapshot, provider, rest)
        group = groups.setdefault(provider, {"group": provider, "models": []})
        group["models"].append(
            {"id": model_id, "name": str(meta.get("name") or rest)}
        )
    return list(groups.values())


def _models_snapshot() -> dict[str, object]:
    path = Path.home() / ".cache/opencode/models.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _snapshot_model(
    snapshot: dict[str, object], provider: str, model: str
) -> dict[str, object]:
    provider_entry = snapshot.get(provider)
    if not isinstance(provider_entry, dict):
        return {}
    table = provider_entry.get("models")
    if not isinstance(table, dict):
        return {}
    entry = table.get(model)
    return entry if isinstance(entry, dict) else {}


def _base_url_check(url: str) -> Check:
    """A HEAD request; any HTTP status means something answered."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            code = response.getcode()
        return {"name": "base_url", "ok": True,
                "detail": f"{url} answered HTTP {code}"}
    except urllib.error.HTTPError as exc:
        return {"name": "base_url", "ok": True,
                "detail": f"{url} answered HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"name": "base_url", "ok": False,
                "detail": f"{url} is unreachable: {exc}"}
