"""Execution engine for dynamically created tools.

Provides async functions to execute user-created tools with different
implementation types: bash commands and HTTP fetches. The legacy ``python``
implementation type used ``eval()`` and was removed for security; new
python-typed dynamic tools are rejected at registration time, and any
already-stored python definitions error out at execution.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # pragma: no cover

logger = logging.getLogger("heare.dynamic_tools")


async def execute_bash_tool(
    command: str, args: dict[str, Any], settings: Any
) -> dict[str, Any]:
    """Execute a bash tool with argument substitution.

    Args are substituted into the command using ``{arg}`` placeholders;
    each substituted value is passed through ``shlex.quote`` so an LLM
    cannot break out of the intended command via crafted argument text.
    The command runs in a subprocess with a 30-second timeout.
    """
    # Substitute {arg} placeholders, shell-quoting each value.
    for key, value in args.items():
        placeholder = f"{{{key}}}"
        if placeholder in command:
            command = command.replace(placeholder, shlex.quote(str(value)))

    try:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

        if proc.returncode == 0:
            return {
                "success": True,
                "output": stdout.decode().strip(),
            }
        else:
            return {
                "success": False,
                "error": stderr.decode().strip() or "Command failed",
            }
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return {"success": False, "error": "Command timed out after 30 seconds"}
    except Exception as e:
        logger.exception("bash tool execution failed")
        return {"success": False, "error": str(e)}


async def execute_fetch_tool(
    url: str, args: dict[str, Any], settings: Any
) -> dict[str, Any]:
    """Execute a fetch tool with argument substitution.

    Args are substituted into the URL using {arg} placeholders.
    Performs an HTTP GET request with a 10-second timeout.
    """
    if httpx is None:
        return {"success": False, "error": "httpx is not installed"}

    # Substitute {arg} placeholders, URL-quoting each value so a `?` or `&`
    # in user input can't graft extra query parameters onto the request.
    import urllib.parse as _urllib_parse

    for key, value in args.items():
        placeholder = f"{{{key}}}"
        if placeholder in url:
            url = url.replace(placeholder, _urllib_parse.quote(str(value), safe=""))

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return {
                "success": True,
                "output": response.text,
            }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except Exception as e:
        logger.exception("fetch tool execution failed")
        return {"success": False, "error": str(e)}


async def execute_python_tool(
    code: str, args: dict[str, Any], settings: Any
) -> dict[str, Any]:
    """Refuse: the python implementation type was removed.

    The legacy version called ``eval(code, {"__builtins__": {}}, ...)`` which is
    trivially escapable (``().__class__.__bases__[0].__subclasses__()`` chain),
    giving an LLM-defined dynamic tool full process privileges. New python-typed
    dynamic tools should now be rejected at registration time; this stub keeps
    any already-persisted definitions from executing.
    """
    return {
        "success": False,
        "error": (
            "python dynamic tools are no longer supported "
            "(use a bash or fetch tool instead)"
        ),
    }


__all__ = [
    "execute_bash_tool",
    "execute_fetch_tool",
    "execute_python_tool",
]
