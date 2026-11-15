"""What can run an agent, how it is found, and how it is set up.

The assistant does not drive a model itself; it starts opencode's own
server (`opencode serve`) and speaks to it over HTTP. Which CLI that is
belongs to one table (``KNOWN``), so the hands that spawn it and the page
that configures it read the same answer.

A daemon started by launchd inherits a minimal ``PATH``. A bare
``opencode`` is therefore found only when the person happens to have it in
one of the places launchd sees — so ``resolve`` looks in the usual install
directories too, and hands spawn an absolute path.

Nothing here returns a secret. Sign-in for opencode is the *names* of the
providers in its auth store; a key is reported as present or absent, never
as a value. ``env`` starts from an allowlist — a path home, a locale and the
agent's own configuration folders — and adds exactly one key, the one the
person chose. The hands' own tokens and every other exported secret stay
behind.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import BinaryIO, TypedDict

from heare import keys

# How a known CLI is started and what it is called on the page. The server
# is opencode's own: the port is chosen by the hands and appended here.
KNOWN: dict[str, "Runner"] = {
    "opencode": {
        "binary": "opencode",
        "command": ["opencode", "serve"],
        "label": "OpenCode",
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

# What a spawned agent may inherit: where commands are, its home, the
# locale, and its own configuration folders. Nothing that carries a
# credential. ``HOME`` is what lets an agent read its own sign-in store.
_ENV_ALWAYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM")
_ENV_DIRS = {
    "opencode": ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                 "XDG_STATE_HOME"),
}

# The agent's own home, under the hands': its config, sign-in, sessions and
# models live here, not in the person's own opencode. `~/.opencode` is read
# by opencode whatever XDG says, so HOME itself has to move.
AGENT_HOME = "opencode"
# What the person's account gives a coder that must not be lost with the
# home: git's identity and the keys ssh reads.
_CARRIED = (".gitconfig", ".config/git/config")
_CARRIED_KEYS = ".ssh"

# Listing models spawns the CLI, so an answer is kept for a while. A person
# who signs in to a new provider sees it after this window, not a restart.
_MODELS_TTL_S = 300.0
_MODELS_CACHE: dict[str, tuple[float, list["ModelGroup"]]] = {}

# A server that does not answer in this long is not the agent's problem to
# hide: the card says so. A loaded machine may take a moment to start one.
_CHECK_S = 30.0


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


def agent_home(root: Path | None = None) -> Path:
    """Where our agent keeps its own opencode: not the person's."""
    return (root or Path.home() / ".heare" / "machine") / AGENT_HOME


def agent_env(home: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """*base* pointed inside *home*: opencode reads and writes its config,
    sign-in and models there, and nothing of the person's own opencode.

    Two things are carried over into the new home, because a coder without
    them cannot do the work: git's identity (an ``include``) and the ssh
    keys (a link — the agent could already read them; this only lets ssh
    find them).
    """
    inner = home / "home"
    for path in (inner, home / "config", home / "data", home / "state",
                 home / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    person = Path(os.environ.get("HOME") or Path.home())
    _carry_identity(inner, person)
    _carry_keys(inner, person)
    env = dict(base or {})
    env.update({
        "HOME": str(inner),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_CACHE_HOME": str(home / "cache"),
    })
    return env


def _carry_identity(home: Path, person: Path) -> None:
    """git's own config, included: the person's name, email and signing."""
    lines = ["# heare: the agent's home; git's identity is the person's"]
    for name in _CARRIED:
        found = person / name
        if found.is_file():
            lines += ["[include]", f"\tpath = {found}"]
    wanted = "\n".join(lines) + "\n"
    path = home / ".gitconfig"
    try:
        if path.read_text(encoding="utf-8") == wanted:
            return
    except OSError:
        pass
    try:
        path.write_text(wanted, encoding="utf-8")
    except OSError:
        pass


def _carry_keys(home: Path, person: Path) -> None:
    """The person's keys, reachable as ``~/.ssh`` from the agent's home."""
    keys = person / _CARRIED_KEYS
    link = home / _CARRIED_KEYS
    if not keys.is_dir() or link.exists():
        return
    try:
        link.symlink_to(keys, target_is_directory=True)
    except OSError:
        pass


def login(kind: str, binary: str | None = None,
          home: Path | None = None) -> Login:
    """Whether *kind* is signed in, without reading a secret."""
    if kind == "opencode":
        return _opencode_login(home or agent_home())
    return {
        "ok": False,
        "summary": "Unknown runner",
        "detail": f"no sign-in check for {kind}",
    }


def models(kind: str, binary: str | None = None,
           home: Path | None = None) -> list[ModelGroup]:
    """The models *kind* accepts, grouped by provider and never fatal. The
    list is ours: it comes from the agent's own home, not the person's."""
    if kind != "opencode":
        return []
    target = resolve(binary or KNOWN["opencode"]["binary"])
    if target is None:
        return []
    home = home or agent_home()
    key = f"{target}\0{home}"
    now = time.monotonic()
    hit = _MODELS_CACHE.get(key)
    if hit is not None and now - hit[0] < _MODELS_TTL_S:
        return hit[1]
    groups = _opencode_models(target, home)
    _MODELS_CACHE[key] = (now, groups)
    return groups


def health(
    kind: str,
    binary: str | None,
    auth: str,
    auth_env: str,
    base_url: str,
    model: str = "",
) -> Health:
    """The four things a runner needs: a binary, a way in, a URL, and a
    server that answers. The last one is the smoke test the hands were
    missing: «Перевірити» starts the agent and asks it whether it is up."""
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

    if resolved is not None:
        checks.append(_agent_check(
            resolved, agent_env(agent_home(),
                                env(kind, auth, auth_env, base_url, model))))

    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def env(
    kind: str,
    auth: str,
    auth_env: str,
    base_url: str,
    model: str,
) -> dict[str, str]:
    """The environment for a spawned runner: an allowlist, plus one key.

    The hands' environment holds the person's whole shell; an agent has no
    business seeing it. Only the path, home, locale and the agent's own
    configuration folders cross over, and exactly one credential: the
    variable the person chose for this runner. A login run carries none —
    the agent uses its own sign-in store. For opencode a base URL is a
    provider option, not an environment variable; see
    ``opencode_config_patch``.
    """
    merged: dict[str, str] = {}
    for name in _ENV_ALWAYS:
        value = os.environ.get(name)
        if value:
            merged[name] = value
    merged.setdefault("HOME", str(Path.home()))
    merged.setdefault("PATH", os.defpath)
    for name in _ENV_DIRS.get(kind, ()):
        value = os.environ.get(name)
        if value:
            merged[name] = value

    if auth == "key" and auth_env:
        value = keys.get(auth_env)
        if value is not None:
            merged[auth_env] = value

    # ``model`` and ``base_url`` are accepted so both setup helpers share one
    # signature; the environment does not carry them.
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _probe(
    argv: list[str], timeout: float, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str] | None:
    """Run a short read-only probe; a CLI that fails is an answer, not a crash."""
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def _agent_check(binary: str, env: dict[str, str]) -> Check:
    """Start the agent and ask it whether it is up: the smoke test.

    The probe is local, so it never goes through a proxy the person may have
    set for the browser — and what the agent said on its way out is kept: a
    check that fails must say why, not just that it did.
    """
    port = _free_port()
    try:
        err: BinaryIO | int = tempfile.TemporaryFile()
    except OSError:
        err = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            [binary, "serve", "--port", str(port), "--hostname", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=err,
            env=env, start_new_session=True,
        )
    except OSError as exc:
        return {"name": "agent", "ok": False, "detail": f"could not start: {exc}"}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + _CHECK_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return {"name": "agent", "ok": False,
                        "detail": f"exited with {proc.returncode}{_said(err)}"}
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/global/health")
                with opener.open(request, timeout=2) as response:
                    answer = json.loads(response.read().decode() or "{}")
                if isinstance(answer, dict) and answer.get("healthy"):
                    version = answer.get("version") or "?"
                    return {"name": "agent", "ok": True,
                            "detail": f"opencode {version} answers"}
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.2)
        return {"name": "agent", "ok": False,
                "detail": f"no answer in {int(_CHECK_S)} s{_said(err)}"}
    finally:
        _stop(proc)


def _said(err: BinaryIO | int) -> str:
    """The last words a failed start left behind, if any."""
    if isinstance(err, int):
        return ""
    try:
        err.seek(0)
        text = err.read().decode("utf-8", "replace").strip()
    except (OSError, ValueError):
        return ""
    if not text:
        return ""
    return ": " + " ".join(text.splitlines()[-2:])[:200]


def _stop(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, 15)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=2)


def _opencode_login(home: Path) -> Login:
    path = home / "data" / "opencode" / "auth.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    names = sorted(data) if isinstance(data, dict) else []
    if not names:
        return {
            "ok": False,
            "summary": "No providers signed in",
            "detail": "run `heare-cli-hands login` in a terminal",
        }
    count = len(names)
    return {
        "ok": True,
        "summary": f"{count} provider{'s' if count != 1 else ''} signed in",
        "detail": ", ".join(names),
    }


def _opencode_models(binary: str, home: Path) -> list[ModelGroup]:
    proc = _probe([binary, "models"], 20,
                  env=agent_env(home, env("opencode", "login", "", "", "")))
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
    path = agent_home() / "cache" / "opencode" / "models.json"
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
