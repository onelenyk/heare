"""Tools — one declaration each.

In the old tree a tool was declared four times: a ``ToolDef`` in
``system.py``, a ``FunctionSchema`` in ``schemas.py``, a category entry in
``registry.py``, and a handler mapping — four tables that had to agree,
about 49 lines of pure declaration before the tool did anything.

Here the function *is* the declaration. The decorator reads the signature
for the schema and the docstring for the description, so ``volume`` (which
used to cost ~120 lines across nine files) would cost five.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Awaitable[str]]
    properties: dict[str, Any]
    required: list[str]
    timeout: float
    voice_only: bool = False


REGISTRY: dict[str, Tool] = {}

# Set once at startup; the memory tools are the only stateful ones.
_memory: Any = None
_settings: Any = None
_hands: Any = None


def tool(
    description: str, *, timeout: float = 60.0, voice_only: bool = False
) -> Callable[[Callable[..., Awaitable[str]]], Callable[..., Awaitable[str]]]:
    """Register an async function as a tool the model can call.

    ``voice_only`` marks a verb the conversational agent may use. Hands
    gets everything else — and never gets ``delegate``, so it cannot
    hand work back to itself.
    """

    def decorate(fn: Callable[..., Awaitable[str]]):
        sig = inspect.signature(fn)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, p in sig.parameters.items():
            json_type = _JSON_TYPES.get(p.annotation, "string")
            doc = f"{pname} for {fn.__name__}"
            properties[pname] = {"type": json_type, "description": doc}
            if p.default is inspect.Parameter.empty:
                required.append(pname)
        REGISTRY[fn.__name__] = Tool(
            name=fn.__name__,
            description=description,
            fn=fn,
            properties=properties,
            required=required,
            timeout=timeout,
            voice_only=voice_only,
        )
        return fn

    return decorate


# ── the one verb that costs nothing to say ────────────────────────────


@tool(
    "Hand a task to your worker: anything needing files, the shell, the "
    "web, or more than a moment. Returns at once — say you are on it, "
    "and the result will arrive as a separate message.",
    voice_only=True,
)
async def delegate(task: str) -> str:
    """Never awaits the work — that is the whole point of the split."""
    if _hands is None:
        return "worker unavailable"
    _hands.start(task)
    return "started; tell the user you are on it, in one short sentence"


# ── the eleven ────────────────────────────────────────────────────────


@tool("Run a shell command and return its output.", timeout=180)
async def bash(command: str) -> str:
    """Kills the whole process group on timeout — a lone SIGKILL to the
    shell leaves its children running and holding the pipe open."""
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(_settings.workspace_dir),
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_settings.bash_timeout_secs
        )
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        return f"timed out after {_settings.bash_timeout_secs:.0f}s"
    text = out.decode("utf-8", "replace").strip()
    return text[:8000] or f"(no output, exit {proc.returncode})"


@tool("Read a text file.")
async def read(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = _settings.workspace_dir / p
    if not p.is_file():
        return f"no such file: {p}"
    return p.read_text("utf-8", "replace")[:8000]


@tool("Write text to a file, replacing what is there.")
async def write(path: str, content: str) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = _settings.workspace_dir / p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, "utf-8")
    return f"wrote {len(content)} chars to {p}"


@tool("Fetch a URL and return its text.")
async def web_fetch(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as c:
        r = await c.get(url, headers={"User-Agent": "heare/core"})
        r.raise_for_status()
        body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
        return re.sub(r"<[^>]+>", " ", body).replace("&nbsp;", " ")[:8000].strip()


@tool("Search the web and return the top results.")
async def web_search(query: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as c:
        if _settings.serper_api_key:
            r = await c.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": _settings.serper_api_key},
                json={"q": query},
            )
            r.raise_for_status()
            hits = r.json().get("organic", [])[:5]
            return "\n".join(f"{h['title']} — {h.get('snippet','')}" for h in hits)
        r = await c.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        found = re.findall(r'result__a[^>]*>(.*?)</a>', r.text, flags=re.S)[:5]
        cleaned = [re.sub(r"<[^>]+>", "", h).strip() for h in found]
        return "\n".join(cleaned) or "nothing found"


@tool("Remember a fact about the user for later conversations.", voice_only=True)
async def remember(content: str) -> str:
    if _memory is None:
        return "memory unavailable"
    import time
    import uuid

    from src.memory.base import MemoryEntry, MemoryType

    await _memory.store(
        MemoryEntry(
            id=uuid.uuid4().hex,
            type=MemoryType.FACT,
            content=content,
            source="voice",
            created_ts=time.time(),
        )
    )
    return "remembered"


@tool("Recall previously remembered facts matching a query.", voice_only=True)
async def recall(query: str) -> str:
    if _memory is None:
        return "memory unavailable"
    entries = await _memory.search(query, limit=5)
    return "\n".join(f"- {e.content}" for e in entries) or "nothing remembered"


@tool("Forget a previously remembered fact by its id.")
async def forget(memory_id: str) -> str:
    if _memory is None:
        return "memory unavailable"
    return "forgotten" if await _memory.forget(memory_id) else "not found"


@tool("Report how many facts are remembered.")
async def memory_status() -> str:
    if _memory is None:
        return "memory unavailable"
    stats = await _memory.stats()
    return ", ".join(f"{k}: {v}" for k, v in stats.items())


@tool("Create a zip archive from a directory.")
async def create_archive(source: str, destination: str) -> str:
    src = Path(source).expanduser()
    dst = Path(destination).expanduser().with_suffix("")
    out = shutil.make_archive(str(dst), "zip", root_dir=str(src))
    return f"created {out}"


@tool("Extract a zip archive into a directory.")
async def extract_archive(archive: str, destination: str) -> str:
    shutil.unpack_archive(
        str(Path(archive).expanduser()), str(Path(destination).expanduser())
    )
    return f"extracted into {destination}"


# ── wiring ────────────────────────────────────────────────────────────


def voice_tools() -> list[Tool]:
    """What the conversational agent sees — three verbs, not sixty-three."""
    return [t for t in REGISTRY.values() if t.voice_only]


def schema(voice_only: bool = True) -> Any:
    """The ToolsSchema the conversational model sees."""
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

    chosen = voice_tools() if voice_only else list(REGISTRY.values())
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name=t.name,
                description=t.description,
                properties=t.properties,
                required=t.required,
            )
            for t in chosen
        ]
    )


def register(
    llm_service: Any,
    *,
    settings: Any,
    memory: Any = None,
    hands: Any = None,
    voice_only: bool = True,
) -> list[str]:
    """Attach the conversational agent's tools to the LLM service."""
    global _memory, _settings, _hands
    _memory, _settings, _hands = memory, settings, hands

    for t in voice_tools() if voice_only else list(REGISTRY.values()):

        def make(t: Tool = t):
            async def handler(params: Any) -> None:
                args = dict(params.arguments or {})
                logger.info("tool %s(%s)", t.name, args)
                try:
                    result = await asyncio.wait_for(t.fn(**args), timeout=t.timeout)
                except asyncio.TimeoutError:
                    result = f"{t.name} timed out after {t.timeout:.0f}s"
                except Exception as exc:  # a failed tool must still answer
                    logger.exception("tool %s failed", t.name)
                    result = f"{t.name} failed: {exc}"
                logger.info("tool %s -> %.120s", t.name, result)
                await params.result_callback(result)

            return handler

        # Deadline is ours, not pipecat's 10s default — that default
        # delivers result=None on expiry and the turn dies in silence.
        llm_service.register_function(
            t.name, make(), cancel_on_interruption=True, timeout_secs=t.timeout + 5
        )
    return [t.name for t in (voice_tools() if voice_only else REGISTRY.values())]
