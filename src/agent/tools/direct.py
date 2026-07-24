"""Direct tool execution without Claude CLI.

Implements simple tools that run directly without Claude's reasoning layer.
Fast path for common operations; complex tools (edit, MCP) still use Claude CLI.

Tool definitions are centralized in tool_registry.py — add new tools there.

Spoken contract
---------------
Each handler MAY include a ``spoken`` key in its return dict.  The action
callback in main.py uses this field to build the TTS frame instead of
truncating the raw ``output``.

``spoken`` may be one of:

* ``dict[str, str]`` — keys are ISO-639-1 language codes (``en``, ``uk``,
  ``ru``); values are short, voice-friendly sentences.  ``en`` is required
  when the dict form is used; all other languages are optional.
* ``str`` — the same English sentence is used for all languages.
* ``None`` or absent — the action callback falls back to
  ``_spoken_action_summary(...)`` truncation of the raw output (legacy
  behaviour preserved for back-compat).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from src.async_utils import safe_task
import re
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger("heare.direct_tools")

# Memory backend singleton (set by build_pipeline)
_memory_backend: Any = None


from src.agent.llm.providers import PROVIDERS  # noqa: E402
from src.agent.tools.subagent import run_opencode  # noqa: E402

# Import tool definitions from central registry
from src.agent.tools.registry import get_direct_tools, is_mcp_tool  # noqa: E402


def set_memory_backend(backend: Any) -> None:
    global _memory_backend
    _memory_backend = backend


SIMPLE_TOOLS = get_direct_tools()


class _PathOutsideWorkspace(Exception):
    """Raised when an LLM-supplied path resolves outside the workspace root."""


def _resolve_in_workspace(filepath: str, workspace: Path) -> Path:
    """Resolve ``filepath`` against ``workspace`` as the *default* root.

    The workspace is a convenience anchor, not a sandbox: the agent has
    full filesystem access by design. Resolution rules:

    * Relative paths anchor to ``workspace`` — so an unqualified filename
      lands in the agent's own dir when no location is specified.
    * Absolute paths and ``~``-prefixed paths are honoured as-is,
      anywhere on the filesystem.

    Never raises ``_PathOutsideWorkspace``; the class is kept only so
    existing callers' ``except`` blocks stay valid.
    """
    candidate = Path(filepath).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    return candidate.resolve()


def _is_simple_tool(tool: str) -> bool:
    """Check if tool can be executed directly (no Claude needed)."""
    if tool in SIMPLE_TOOLS:
        return True
    # MCP tools are complex (need Claude)
    if is_mcp_tool(tool):
        return False
    return False


async def execute_direct(
    tool: str,
    args: str,
    settings: "Settings | None" = None,
) -> dict:
    """Execute a simple tool directly without Claude CLI.

    Args:
        tool: Tool name (bash, read, write, web_fetch, web_search)
        args: Tool arguments
        settings: heare Settings (for workspace dir, API keys, etc.)

    Returns:
        dict with:
            - success (bool)
            - output (str) - stdout or result
            - error (str | None) - error message if failed
            - exit_code (int | None) - for bash commands
            - spoken (dict[str, str] | str | None) - optional voice-friendly
              summary used by the TTS callback instead of raw output.
              When a dict, keys are ISO-639-1 codes (en/uk/ru) and values are
              short sentences; ``en`` is required.  When a str, the same
              sentence is used for all languages.  When None or absent, the
              callback falls back to ``_spoken_action_summary(...)``
              truncation of the raw output.
    """
    if tool == "bash":
        return await _execute_bash(args, settings)
    elif tool == "read":
        return await _execute_read(args, settings)
    elif tool == "write":
        return await _execute_write(args, settings)
    elif tool == "web_fetch":
        return await _execute_web_fetch(args, settings)
    elif tool == "web_search":
        return await _execute_web_search(args, settings)
    elif tool == "create_tool":
        return await _execute_create_tool(args, settings)
    elif tool == "update_tool":
        return await _execute_update_tool(args, settings)
    elif tool == "delete_tool":
        return await _execute_delete_tool(args, settings)
    elif tool == "list_tools":
        return await _execute_list_tools(args, settings)
    elif tool == "create_archive":
        return await _execute_create_archive(args, settings)
    elif tool == "extract_archive":
        return await _execute_extract_archive(args, settings)
    elif tool == "batch_operation":
        return await _execute_batch_operation(args, settings)
    elif tool == "add_favorite":
        return await _execute_add_favorite(args, settings)
    elif tool == "list_favorites":
        return await _execute_list_favorites(args, settings)
    elif tool == "set_view_preference":
        return await _execute_set_view_preference(args, settings)
    elif tool == "show_profile":
        return await _execute_show_profile(args, settings)
    elif tool == "list_skills":
        return await _execute_list_skills(args, settings)
    elif tool == "run_skill":
        return await _execute_run_skill(args, settings)
    elif tool == "set_provider":
        return await _execute_set_provider(args, settings)
    elif tool == "set_mode":
        return await _execute_set_mode(args, settings)
    elif tool == "show_text":
        return await _execute_show_display(args, settings)
    elif tool == "show_canvas":
        return await _execute_show_display(args, settings)
    elif tool == "discover_capability":
        return await _execute_discover_capability(args, settings)
    elif tool == "install_skill_tool":
        return await _execute_install_skill_tool(args, settings)
    elif tool == "create_skill":
        return await _execute_create_skill(args, settings)
    elif tool == "stop_daemon":
        return await _execute_stop_daemon(args, settings)
    elif tool == "restart_daemon":
        return await _execute_restart_daemon(args, settings)
    elif tool == "install_mcp_server_tool":
        return await _execute_install_mcp_server_tool(args, settings)
    elif tool == "register_mcp_server":
        return await _execute_register_mcp_server(args, settings)
    elif tool == "revoke_capability":
        return await _execute_revoke_capability(args, settings)
    elif tool == "list_capabilities":
        return await _execute_list_capabilities(args, settings)
    elif tool == "read_browser_page":
        return await _execute_read_browser_page(args, settings)
    elif tool == "list_browser_tabs":
        return await _execute_list_browser_tabs(args, settings)
    elif tool == "click_in_browser":
        return await _execute_click_in_browser(args, settings)
    elif tool == "fill_in_browser":
        return await _execute_fill_in_browser(args, settings)
    elif tool == "navigate_browser":
        return await _execute_navigate_browser(args, settings)
    elif tool == "extract_in_browser":
        return await _execute_extract_in_browser(args, settings)
    elif tool == "open_browser_tab":
        return await _execute_open_browser_tab(args, settings)
    elif tool == "activate_browser_tab":
        return await _execute_activate_browser_tab(args, settings)
    elif tool == "mute_bot":
        return await _execute_mute_bot(args, settings)
    elif tool == "mute_mic":
        return await _execute_mute_mic(args, settings)
    elif tool == "audio_input":
        return await _execute_audio_device(args, settings, target="input")
    elif tool == "audio_output":
        return await _execute_audio_device(args, settings, target="output")
    elif tool == "vad_sensitivity":
        return await _execute_vad_sensitivity(args, settings)
    elif tool == "mic_gain":
        return await _execute_mic_gain(args, settings)
    elif tool == "volume":
        return await _execute_volume(args, settings)
    elif tool == "run_agent":
        return await _execute_run_agent(args, settings)
    elif tool == "remember":
        return await _execute_remember(args, settings)
    elif tool == "recall":
        return await _execute_recall(args, settings)
    elif tool == "forget":
        return await _execute_forget(args, settings)
    elif tool == "memory_status":
        return await _execute_memory_status(args, settings)
    elif tool == "sidetone":
        return await _execute_sidetone(args, settings)
    else:
        return {
            "success": False,
            "output": "",
            "error": f"Unknown direct tool: {tool}",
        }


async def _execute_bash(args: str, settings: "Settings | None" = None) -> dict:
    """Execute a bash command.

    CCS-05b: spawned with ``start_new_session=True`` so the bash + every
    child it spawns share a single process group. On cancellation the
    process group is signalled (SIGTERM, 2s grace, then SIGKILL) via
    ``os.killpg`` so no orphan child processes survive.

    Daemon-self-protection: refuses commands that would terminate or
    restart the running daemon (``make restart``, ``hearectl stop``,
    ``kill <self>``, etc.). The bash subprocess shares fate with the
    daemon, so a self-targeted restart kills the agent without bringing
    it back up — see ``daemon_control.is_dangerous_self_command`` and
    the ``stop_daemon`` / ``restart_daemon`` native tools instead.
    """
    import subprocess

    from src.daemon.control import is_dangerous_self_command

    if is_dangerous_self_command(args):
        logger.warning(
            "[BASH GUARD] refused self-targeting command: %r",
            (args or "")[:200],
        )
        return {
            "success": False,
            "output": "",
            "error": (
                "self_targeted_restart: this command would kill the "
                "daemon you're running in. Call the native tool "
                "`restart_daemon(user_confirmed=true)` (or "
                "`stop_daemon`) instead — those handle detached "
                "respawn correctly."
            ),
            "spoken": {
                "en": (
                    "I can't run that — it would shut me down without "
                    "starting back up. I'll use the restart tool instead."
                ),
                "uk": (
                    "Не можу виконати — це вимкне мене без перезапуску. "
                    "Скористаюсь нативним інструментом для рестарту."
                ),
            },
        }

    workspace = settings.workspace_dir if settings else Path.cwd()

    try:
        proc = await asyncio.create_subprocess_shell(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workspace,
            start_new_session=True,
        )
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            # CCS-05b: cancel signalled — escalate to the entire process
            # group so child processes (e.g. ``bash -c 'sleep 60'``'s
            # sleep) die alongside the bash parent.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                try:
                    await proc.wait()
                except BaseException:  # noqa: BLE001 — best-effort drain
                    pass
            raise

        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")

        if proc.returncode == 0:
            stripped = stdout_str.strip()
            if stripped and len(stripped) <= 80:
                stdout_short = stripped.replace("\n", " ")
                spoken: dict[str, str] = {
                    "en": f"Done. {stdout_short}",
                    "uk": f"Виконано. {stdout_short}",
                    "ru": f"Готово. {stdout_short}",
                }
            else:
                spoken = {
                    "en": "Done.",
                    "uk": "Виконано.",
                    "ru": "Готово.",
                }
            return {
                "success": True,
                "output": stdout_str,
                "error": stderr_str or None,
                "exit_code": proc.returncode,
                "spoken": spoken,
            }
        else:
            return {
                "success": False,
                "output": stdout_str,
                "error": stderr_str or None,
                "exit_code": proc.returncode,
                "spoken": {
                    "en": "Command failed.",
                    "uk": "Команда не виконалася.",
                    "ru": "Команда не выполнилась.",
                },
            }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "output": "",
            "error": "Command timed out",
            "exit_code": -1,
            "spoken": {
                "en": "Command failed.",
                "uk": "Команда не виконалася.",
                "ru": "Команда не выполнилась.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "exit_code": -1,
            "spoken": {
                "en": "Command failed.",
                "uk": "Команда не виконалася.",
                "ru": "Команда не выполнилась.",
            },
        }


async def _execute_read(args: str, settings: "Settings | None" = None) -> dict:
    """Read a file from the workspace."""

    # Support "path: extra" format - extract only the path part
    filepath = args.split(":", 1)[0] if ":" in args else args
    if settings is not None:
        try:
            path = _resolve_in_workspace(filepath, settings.workspace_dir)
        except _PathOutsideWorkspace as exc:
            return {
                "success": False,
                "output": "",
                "error": str(exc),
                "spoken": {
                    "en": "Path is outside the workspace.",
                    "uk": "Шлях за межами робочої теки.",
                    "ru": "Путь вне рабочей папки.",
                },
            }
    else:
        # Settings-less callers (unit tests, scripts) skip the workspace
        # guard. Production LLM dispatch always supplies settings.
        path = Path(filepath)
        if not path.is_absolute():
            path = Path.cwd() / path

    try:
        content = path.read_text(encoding="utf-8", errors="replace")

        # Truncate large files
        if len(content) > 100_000:
            content = content[:100_000] + "\n... (truncated)"

        n = len(content.splitlines())
        return {
            "success": True,
            "output": content,
            "error": None,
            "spoken": {
                "en": f"Read {n} lines.",
                "uk": f"Прочитав {n} рядків.",
                "ru": f"Прочитал {n} строк.",
            },
        }
    except FileNotFoundError:
        return {
            "success": False,
            "output": "",
            "error": "File not found",
            "spoken": {
                "en": "File not found.",
                "uk": "Файл не знайдено.",
                "ru": "Файл не найден.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Failed to read {args}: {e}",
        }


async def _execute_write(args: str, settings: "Settings | None" = None) -> dict:
    """Write content to a file.

    Args format: "filepath: content"
    """

    if ":" not in args:
        return {
            "success": False,
            "output": "",
            "error": "Write requires 'path: content' format",
        }

    filepath, content = args.split(":", 1)
    content = content.lstrip()  # Remove leading space after colon
    if settings is not None:
        try:
            path = _resolve_in_workspace(filepath, settings.workspace_dir)
        except _PathOutsideWorkspace as exc:
            return {
                "success": False,
                "output": "",
                "error": str(exc),
                "spoken": {
                    "en": "Cannot write outside the workspace.",
                    "uk": "Не можу писати за межами робочої теки.",
                    "ru": "Не могу писать вне рабочей папки.",
                },
            }
    else:
        # Settings-less callers (unit tests, scripts) skip the workspace
        # guard. Production LLM dispatch always supplies settings.
        path = Path(filepath)
        if not path.is_absolute():
            path = Path.cwd() / path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "success": True,
            "output": f"Written to {filepath}",
            "error": None,
            "spoken": {
                "en": "File saved.",
                "uk": "Файл збережено.",
                "ru": "Файл сохранён.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Failed to write {filepath}: {e}",
            "spoken": {
                "en": "Could not save file.",
                "uk": "Не вдалося зберегти файл.",
                "ru": "Не удалось сохранить файл.",
            },
        }


async def _execute_web_fetch(args: str, settings: "Settings | None" = None) -> dict:
    """Fetch a URL and return the content."""
    url = args.strip()
    if not url:
        return {"success": False, "output": "", "error": "No URL provided"}

    # Validate URL
    if not url.startswith(("http://", "https://")):
        return {
            "success": False,
            "output": "",
            "error": "URL must start with http:// or https://",
        }

    _spoken_error: dict[str, str] = {
        "en": "Fetch failed.",
        "uk": "Не вдалося завантажити.",
        "ru": "Не удалось загрузить.",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            # Handle binary responses
            if resp.text is None:
                return {
                    "success": True,
                    "output": f"[Binary response, {len(resp.content)} bytes]",
                    "error": None,
                    "spoken": {
                        "en": "Fetched. Want a summary?",
                        "uk": "Завантажив. Зробити підсумок?",
                        "ru": "Загрузил. Сделать сводку?",
                    },
                }

            # Truncate large responses
            content = resp.text
            if len(content) > 50_000:
                content = content[:50_000] + "\n... (truncated)"

            return {
                "success": True,
                "output": content,
                "error": None,
                "spoken": {
                    "en": "Fetched. Want a summary?",
                    "uk": "Завантажив. Зробити підсумок?",
                    "ru": "Загрузил. Сделать сводку?",
                },
            }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "output": "",
            "error": f"HTTP {e.response.status_code}",
            "spoken": _spoken_error,
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Failed to fetch {url}: {e}",
            "spoken": _spoken_error,
        }


async def _execute_web_search(args: str, settings: "Settings | None" = None) -> dict:
    """Search via DuckDuckGo or Serper.dev and return results."""
    query = args.strip()
    if not query:
        return {"success": False, "output": "", "error": "No query provided"}

    # Get API key from env or settings
    api_key = os.environ.get("SERPER_API_KEY")
    if settings and settings.serper_api_key:
        api_key = settings.serper_api_key

    # Get provider preference (default: auto-detect based on API key)
    provider = "auto"
    if settings:
        provider = getattr(settings, "web_search_provider", "auto")

    # Determine which provider to use
    use_serper = False
    if provider == "serper":
        use_serper = bool(api_key)
    elif provider == "duckduckgo":
        use_serper = False
    else:  # "auto" or any other value
        use_serper = bool(api_key)

    if use_serper:
        return await _search_serper(query, api_key, settings)

    # Fall back to DuckDuckGo
    return await _search_duckduckgo(query, settings)


async def _maybe_append_top_page(
    output: str,
    top_url: str | None,
    settings: "Settings | None",
) -> str:
    """When enabled in settings, fetch the top organic URL and append its
    text to the search output so the agent can answer content-style
    questions (recipe, how-to) directly from prior search results.

    Failure-safe: any exception or non-success returns the input output
    unchanged. Total length is hard-capped at 8000 chars.
    """
    fetch_top = bool(getattr(settings, "web_search_fetch_top", False))
    if not (fetch_top and top_url):
        return _truncate(output, 8000)
    try:
        fetched = await _execute_web_fetch(top_url, settings)
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("web_search top-fetch raised: %s", e)
        return _truncate(output, 8000)
    if not (fetched and fetched.get("success")):
        return _truncate(output, 8000)
    body = (fetched.get("output") or "").strip()
    if not body:
        return _truncate(output, 8000)
    appended = f"{output}\n\nTop page content:\n{body}"
    return _truncate(appended, 8000)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


async def _search_serper(
    query: str, api_key: str, settings: "Settings | None" = None
) -> dict:
    """Search using Serper.dev (Google API).

    Return shape (CCS-02)::

        {
            "success": True,
            "output": "1. Title\\nSnippet\\nURL\\n\\n2. …",  # numbered text
            "items": [
                {"n": 0, "title": "📚 …", "url": "", "snippet": "…",
                 "kind": "answer_box"},   # Serper knowledgeGraph, when present
                {"n": 1, "title": "…", "url": "…", "snippet": "…"},
                ...
            ],
            "error": None,
            "spoken": {...},
        }

    Knowledge-graph (Serper ``knowledgeGraph``) entries are inserted at
    position 0 of ``items`` with ``n=0`` and ``kind="answer_box"``, and
    rendered at the start of ``output`` as ``"📚 {title}: {description}"``.
    Organic hits are numbered ``1..5`` (contiguous). Empty-results path
    returns ``output="No results found"`` and ``items=[]``.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://google.serper.dev/search",
                params={"q": query},
                headers={"X-API-KEY": api_key},
            )
            resp.raise_for_status()
            data = resp.json()

            items: list[dict] = []
            text_blocks: list[str] = []
            top_url: str | None = None

            # Knowledge-graph (answer box) goes to position 0 with n=0.
            if "knowledgeGraph" in data:
                kg = data["knowledgeGraph"]
                kg_title = kg.get("title", "")
                kg_desc = kg.get("description", "")
                if kg_title and kg_desc:
                    items.append(
                        {
                            "n": 0,
                            "title": f"📚 {kg_title}",
                            "url": "",
                            "snippet": kg_desc,
                            "kind": "answer_box",
                        }
                    )
                    text_blocks.append(f"📚 {kg_title}: {kg_desc}")

            n = 1
            for item in data.get("organic", [])[:5]:
                title = item.get("title", "")
                url = item.get("link", "")
                snippet = (item.get("snippet") or "").strip()
                if title and url:
                    if top_url is None:
                        top_url = url
                    items.append(
                        {
                            "n": n,
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                        }
                    )
                    if snippet:
                        text_blocks.append(f"{n}. {title}\n{snippet}\n{url}")
                    else:
                        text_blocks.append(f"{n}. {title}\n{url}")
                    n += 1

            if items:
                spoken: dict[str, str] = {
                    "en": "Searching done.",
                    "uk": "Пошук завершено.",
                    "ru": "Поиск завершён.",
                }
            else:
                spoken = {
                    "en": "No results found.",
                    "uk": "Нічого не знайшов.",
                    "ru": "Ничего не нашёл.",
                }
            output = "\n\n".join(text_blocks) if text_blocks else "No results found"
            output = await _maybe_append_top_page(output, top_url, settings)
            return {
                "success": True,
                "output": output,
                "items": items,
                "error": None,
                "spoken": spoken,
            }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "output": "",
            "items": [],
            "error": f"Serper API error: {e.response.status_code}",
            "spoken": {
                "en": "Search failed.",
                "uk": "Пошук не вдався.",
                "ru": "Поиск не удался.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "items": [],
            "error": f"Serper search failed: {e}",
            "spoken": {
                "en": "Search failed.",
                "uk": "Пошук не вдався.",
                "ru": "Поиск не удался.",
            },
        }


async def _search_duckduckgo(query: str, settings: "Settings | None" = None) -> dict:
    """Search using DuckDuckGo HTML scraping (fallback, no API key needed).

    Return shape (CCS-02)::

        {
            "success": True,
            "output": "1. Title\\nSnippet\\nURL\\n\\n2. …",
            "items": [
                {"n": 1, "title": "…", "url": "…", "snippet": "…"},
                ...
            ],
            "error": None,
            "spoken": {...},
        }

    Items are numbered ``1..5`` (contiguous). DuckDuckGo has no
    knowledge-graph equivalent, so ``n=0`` is unused here. Empty results
    return ``output="No results found"`` and ``items=[]``.
    """
    import re

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # DuckDuckGo HTML version
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            resp.raise_for_status()
            text = resp.text

            items: list[dict] = []
            text_blocks: list[str] = []
            top_url: str | None = None
            link_pattern = r'<a class="result__a" href="([^"]*)">([^<]*)</a>'
            link_iter = list(re.finditer(link_pattern, text))
            tag_strip = re.compile(r"<[^>]+>")
            ws_collapse = re.compile(r"\s+")

            n = 1
            for i, m in enumerate(link_iter[:5]):
                url = m.group(1)
                title = m.group(2).strip()
                if not (url and title):
                    continue
                if top_url is None:
                    top_url = url
                next_pos = (
                    link_iter[i + 1].start() if i + 1 < len(link_iter) else len(text)
                )
                chunk = text[m.end() : next_pos]
                snippet_match = re.search(
                    r'class="result__snippet"[^>]*>(.*?)</a>',
                    chunk,
                    re.DOTALL,
                )
                snippet = ""
                if snippet_match:
                    raw = tag_strip.sub("", snippet_match.group(1))
                    snippet = ws_collapse.sub(" ", raw).strip()
                items.append(
                    {
                        "n": n,
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                    }
                )
                if snippet:
                    text_blocks.append(f"{n}. {title}\n{snippet}\n{url}")
                else:
                    text_blocks.append(f"{n}. {title}\n{url}")
                n += 1

            if items:
                spoken: dict[str, str] = {
                    "en": "Searching done.",
                    "uk": "Пошук завершено.",
                    "ru": "Поиск завершён.",
                }
            else:
                spoken = {
                    "en": "No results found.",
                    "uk": "Нічого не знайшов.",
                    "ru": "Ничего не нашёл.",
                }
            output = "\n\n".join(text_blocks) if text_blocks else "No results found"
            output = await _maybe_append_top_page(output, top_url, settings)
            return {
                "success": True,
                "output": output,
                "items": items,
                "error": None,
                "spoken": spoken,
            }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "items": [],
            "error": f"DuckDuckGo search failed: {e}",
            "spoken": {
                "en": "Search failed.",
                "uk": "Пошук не вдався.",
                "ru": "Поиск не удался.",
            },
        }


async def _execute_create_tool(args: str, settings: "Settings | None" = None) -> dict:
    """Create a new dynamic tool.

    Expects JSON args with: name, description, arguments, implementation_type, implementation
    """
    import json

    from src.agent.tools.registry import (
        ToolDefinition,
        register_dynamic_tool,
        Tool,
        is_static_tool,
    )
    from src.store.storage import TranscriptStore

    try:
        spec = json.loads(args)
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON: {e}",
        }

    definition = ToolDefinition(
        name=spec.get("name", ""),
        description=spec.get("description", ""),
        arguments=spec.get("arguments", {}),
        implementation_type=spec.get("implementation_type", "bash"),
        implementation=spec.get("implementation", ""),
    )

    # Validate
    errors = definition.validate()
    if errors:
        return {
            "success": False,
            "output": "",
            "error": f"Validation failed: {'; '.join(errors)}",
        }

    # Check if tool already exists in static registry
    if is_static_tool(definition.name):
        return {
            "success": False,
            "output": "",
            "error": f"Tool '{definition.name}' is a built-in and cannot be replaced",
        }

    # Create Tool object
    tool = Tool(
        name=definition.name,
        sdk_name=_to_camel_case(definition.name),
        execution="direct",
        description=definition.description,
        enabled=True,
    )

    # Register in runtime registry
    register_dynamic_tool(tool)

    # Persist to database if settings available
    if settings and settings.db_path:
        store = TranscriptStore(settings.db_path)
        try:
            await store.init()
            await store.create_dynamic_tool(
                name=tool.name,
                sdk_name=tool.sdk_name,
                execution_type=definition.implementation_type,
                description=tool.description,
                definition_json=args,
            )
        finally:
            await store.close()

    # Dynamically add to llm_tools registry
    from src.agent.tools import schemas as llm_tools

    llm_tools.register_dynamic_tool_schema(
        name=definition.name,
        schema=definition.arguments,
        impl_type=definition.implementation_type,
        impl=definition.implementation,
    )

    return {
        "success": True,
        "output": f"Tool '{definition.name}' created and ready to use",
        "error": None,
        "spoken": {
            "en": f"Created tool {definition.name}.",
            "uk": f"Створено інструмент {definition.name}.",
            "ru": f"Создан инструмент {definition.name}.",
        },
    }


async def _execute_update_tool(args: str, settings: "Settings | None" = None) -> dict:
    """Update an existing dynamic tool."""
    import json

    from src.agent.tools.registry import is_static_tool, _DYNAMIC_TOOLS
    from src.store.storage import TranscriptStore

    try:
        spec = json.loads(args)
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON: {e}",
        }

    name = spec.get("name")
    if not name:
        return {
            "success": False,
            "output": "",
            "error": "Tool name is required",
        }

    # Check if it's a static tool
    if is_static_tool(name) and name not in _DYNAMIC_TOOLS:
        return {
            "success": False,
            "output": "",
            "error": f"Tool '{name}' is a built-in and cannot be modified",
        }

    # Build updates dict
    updates = {}
    if "description" in spec:
        updates["description"] = spec["description"]
    if "definition_json" in spec:
        updates["definition_json"] = json.dumps(spec["definition_json"])
    elif (
        "arguments" in spec or "implementation_type" in spec or "implementation" in spec
    ):
        # Rebuild definition_json from components
        current_def = json.loads(spec.get("current_definition_json", "{}"))
        if "arguments" in spec:
            current_def["arguments"] = spec["arguments"]
        if "implementation_type" in spec:
            current_def["implementation_type"] = spec["implementation_type"]
        if "implementation" in spec:
            current_def["implementation"] = spec["implementation"]
        updates["definition_json"] = json.dumps(current_def)

    if not updates:
        return {
            "success": False,
            "output": "",
            "error": "No fields to update",
        }

    # Update database
    if settings and settings.db_path:
        store = TranscriptStore(settings.db_path)
        try:
            await store.init()
            updated = await store.update_dynamic_tool(name, **updates)
            if not updated:
                return {
                    "success": False,
                    "output": "",
                    "error": f"Tool '{name}' not found",
                }
        finally:
            await store.close()

    return {
        "success": True,
        "output": f"Tool '{name}' updated",
        "error": None,
        "spoken": {
            "en": f"Updated tool {name}.",
            "uk": f"Оновлено інструмент {name}.",
            "ru": f"Обновлен инструмент {name}.",
        },
    }


async def _execute_delete_tool(args: str, settings: "Settings | None" = None) -> dict:
    """Delete a dynamic tool."""
    from src.agent.tools.registry import unregister_dynamic_tool, is_static_tool
    from src.store.storage import TranscriptStore

    name = args.strip()

    if not name:
        return {
            "success": False,
            "output": "",
            "error": "Tool name is required",
        }

    # Check if it's a static tool
    if is_static_tool(name):
        return {
            "success": False,
            "output": "",
            "error": f"Tool '{name}' is a built-in and cannot be deleted",
        }

    # Unregister
    if unregister_dynamic_tool(name):
        # Delete from database
        if settings and settings.db_path:
            store = TranscriptStore(settings.db_path)
            try:
                await store.init()
                deleted = await store.delete_dynamic_tool(name)
                if not deleted:
                    return {
                        "success": False,
                        "output": "",
                        "error": f"Tool '{name}' not found in database",
                    }
            finally:
                await store.close()

        # Remove from llm_tools registry
        from src.agent.tools import schemas as llm_tools

        llm_tools.unregister_dynamic_tool_schema(name)

        return {
            "success": True,
            "output": f"Tool '{name}' deleted",
            "error": None,
            "spoken": {
                "en": f"Deleted tool {name}.",
                "uk": f"Видалено інструмент {name}.",
                "ru": f"Удален инструмент {name}.",
            },
        }

    return {
        "success": False,
        "output": "",
        "error": f"Tool '{name}' not found",
    }


async def _execute_list_tools(args: str, settings: "Settings | None" = None) -> dict:
    """List all available tools."""
    from src.agent.tools.registry import get_all_tools, is_dynamic_tool

    all_tools = get_all_tools()
    items = []
    for name, tool in sorted(all_tools.items()):
        items.append(
            {
                "name": name,
                "description": tool.description,
                "type": "dynamic" if is_dynamic_tool(name) else "built-in",
            }
        )

    return {
        "success": True,
        "output": f"{len(items)} tools available",
        "error": None,
        "items": items,
        "spoken": {
            "en": f"{len(items)} tools available.",
            "uk": f"{len(items)} інструментів доступно.",
            "ru": f"{len(items)} инструментов доступно.",
        },
    }


# ============================================================================
# Navigation & Browsing Tools
# ============================================================================


def _human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


# ============================================================================
# File Operation Tools
# ============================================================================


async def _execute_create_archive(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Create a tar or zip archive from files/directories."""
    import tarfile
    import zipfile

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "archive_path source1 [source2...] [format=tar.gz] [compression=auto]"
    parts = args.strip().split()
    if not parts:
        return {
            "success": False,
            "output": "",
            "error": "Usage: create_archive <archive_path> <source1> [source2...] [format] [compression]",
            "spoken": {
                "en": "Usage: create_archive <archive_path> <source1> [source2...] [format] [compression]",
                "uk": "Використання: create_archive <archive_path> <source1> [source2...] [format] [compression]",
                "ru": "Использование: create_archive <archive_path> <source1> [source2...] [format] [compression]",
            },
        }

    archive_path_str = parts[0]
    sources = (
        parts[1:-2]
        if len(parts) > 2 and parts[-2] in ("tar.gz", "zip", "tar.bz2")
        else parts[1:]
    )
    archive_format = (
        parts[-2]
        if len(parts) > 2 and parts[-2] in ("tar.gz", "zip", "tar.bz2")
        else "tar.gz"
    )
    compression = (
        parts[-1]
        if len(parts) > 3 and parts[-1] in ("auto", "gzip", "bzip2", "none")
        else "auto"
    )

    # Resolve paths
    archive_path = Path(archive_path_str)
    if not archive_path.is_absolute():
        archive_path = workspace / archive_path_str

    # Ensure archive directory exists
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    # Verify sources exist
    source_paths = []
    for source in sources:
        src_path = Path(source)
        if not src_path.is_absolute():
            src_path = workspace / source
        if not src_path.exists():
            return {
                "success": False,
                "output": "",
                "error": f"Source not found: {source}",
                "spoken": {
                    "en": f"Source not found: {source}",
                    "uk": f"Джерело не знайдено: {source}",
                    "ru": f"Источник не найден: {source}",
                },
            }
        source_paths.append(src_path)

    try:
        # Check archive size limit
        total_size = sum(
            f.stat().st_size for p in source_paths for f in p.rglob("*") if f.is_file()
        )
        if (
            settings
            and hasattr(settings, "file_access_max_archive_size")
            and total_size > settings.file_access_max_archive_size
        ):
            return {
                "success": False,
                "output": "",
                "error": f"Archive would exceed size limit of {settings.file_access_max_archive_size} bytes",
                "spoken": {
                    "en": f"Archive too large: would exceed {settings.file_access_max_archive_size / (1024 * 1024):.1f} MB limit",
                    "uk": f"Архів занадто великий: перевищує ліміт {settings.file_access_max_archive_size / (1024 * 1024):.1f} MB",
                    "ru": f"Архив слишком большой: превышает лимит {settings.file_access_max_archive_size / (1024 * 1024):.1f} MB",
                },
            }

        if archive_format == "zip":
            # Create ZIP archive
            with zipfile.ZipFile(
                archive_path,
                "w",
                zipfile.ZIP_DEFLATED
                if compression == "auto"
                else zipfile.ZIP_STORED
                if compression == "none"
                else zipfile.ZIP_BZIP2
                if compression == "bzip2"
                else zipfile.ZIP_DEFLATED,
            ) as zipf:
                for src in source_paths:
                    for file in src.rglob("*"):
                        if file.is_file():
                            arcname = file.relative_to(src.parent.parent)
                            zipf.write(file, arcname)
        else:
            # Create TAR archive
            if archive_format == "tar.gz":
                mode = "w:gz" if compression == "auto" or compression == "gzip" else "w"
            elif archive_format == "tar.bz2":
                mode = (
                    "w:bz2" if compression == "auto" or compression == "bzip2" else "w"
                )
            else:
                mode = "w"

            with tarfile.open(archive_path, mode) as tar:
                for src in source_paths:
                    tar.add(src, arcname=src.name)

        result = {
            "archive_path": str(archive_path),
            "format": archive_format,
            "sources": [str(s) for s in source_paths],
            "size": archive_path.stat().st_size,
            "human_size": _human_readable_size(archive_path.stat().st_size),
        }

        return {
            "success": True,
            "output": f"Created {archive_format} archive: {archive_path_str} ({result['human_size']})",
            "result": result,
            "error": None,
            "spoken": {
                "en": f"Created {archive_format} archive: {result['human_size']}",
                "uk": f"Створено архів {archive_format}: {result['human_size']}",
                "ru": f"Создан архив {archive_format}: {result['human_size']}",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error creating archive: {str(e)}",
                "uk": f"Помилка при створенні архіву: {str(e)}",
                "ru": f"Ошибка при создании архива: {str(e)}",
            },
        }


async def _execute_extract_archive(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Extract tar or zip archive to a directory."""
    import tarfile
    import zipfile

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "archive_path destination [overwrite=true] [preserve_path=true]"
    parts = args.strip().split()
    if len(parts) < 2:
        return {
            "success": False,
            "output": "",
            "error": "Usage: extract_archive <archive_path> <destination> [overwrite] [preserve_path]",
            "spoken": {
                "en": "Usage: extract_archive <archive_path> <destination> [overwrite] [preserve_path]",
                "uk": "Використання: extract_archive <archive_path> <destination> [overwrite] [preserve_path]",
                "ru": "Использование: extract_archive <archive_path> <destination> [overwrite] [preserve_path]",
            },
        }

    archive_path_str = parts[0]
    destination = parts[1]
    overwrite = len(parts) > 2 and parts[2].lower() in ("true", "1", "yes")
    preserve_path = len(parts) > 3 and parts[3].lower() in ("true", "1", "yes")

    # Resolve paths
    archive_path = Path(archive_path_str)
    if not archive_path.is_absolute():
        archive_path = workspace / archive_path_str

    dest_path = Path(destination)
    if not dest_path.is_absolute():
        dest_path = workspace / destination

    # Ensure destination directory exists
    if overwrite:
        dest_path.mkdir(parents=True, exist_ok=True)
    else:
        if dest_path.exists() and any(dest_path.iterdir()):
            return {
                "success": False,
                "output": "",
                "error": f"Destination directory not empty: {destination}. Use overwrite=true to replace.",
                "spoken": {
                    "en": f"Destination directory not empty: {destination}. Use overwrite=true to replace.",
                    "uk": f"Директорія призначення не порожня: {destination}. Використайте overwrite=true, щоб замінити.",
                    "ru": f"Каталог назначения не пуст: {destination}. Используйте overwrite=true, чтобы заменить.",
                },
            }

    if not archive_path.exists():
        return {
            "success": False,
            "output": "",
            "error": f"Archive not found: {archive_path_str}",
            "spoken": {
                "en": f"Archive not found: {archive_path_str}",
                "uk": f"Архів не знайдено: {archive_path_str}",
                "ru": f"Архив не найден: {archive_path_str}",
            },
        }

    try:
        extracted_files = []
        extracted_dirs = []
        total_size = 0

        # Determine archive format
        if archive_path.suffix == ".zip" or archive_path.name.endswith(".zip"):
            # Extract ZIP
            dest_path.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive_path, "r") as zipf:
                for member in zipf.infolist():
                    if not member.is_dir():
                        file_size = member.file_size
                        total_size += file_size
                        extract_path = (
                            dest_path / member.filename
                            if preserve_path
                            else dest_path / Path(member.filename).name
                        )
                        zipf.extract(member, extract_path.parent)
                        extracted_files.append(str(extract_path))
                    else:
                        dir_path = (
                            dest_path / member.filename if preserve_path else dest_path
                        )
                        if not dir_path.exists():
                            dir_path.mkdir(parents=True, exist_ok=True)
                        extracted_dirs.append(str(dir_path))
        else:
            # Extract TAR
            dest_path.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive_path, "r:*") as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        total_size += member.size
                        extract_path = (
                            dest_path / member.name
                            if preserve_path
                            else dest_path / Path(member.name).name
                        )
                        tar.extract(member, extract_path.parent)
                        extracted_files.append(str(extract_path))
                    elif member.isdir():
                        dir_path = (
                            dest_path / member.name if preserve_path else dest_path
                        )
                        if not dir_path.exists():
                            dir_path.mkdir(parents=True, exist_ok=True)
                        extracted_dirs.append(str(dir_path))

        result = {
            "archive_path": str(archive_path),
            "destination": str(dest_path),
            "files_extracted": len(extracted_files),
            "dirs_extracted": len(extracted_dirs),
            "total_size": total_size,
            "human_size": _human_readable_size(total_size),
        }

        return {
            "success": True,
            "output": f"Extracted {result['files_extracted']} files, {result['dirs_extracted']} directories to {destination}",
            "result": result,
            "error": None,
            "spoken": {
                "en": f"Extracted {result['files_extracted']} files to {destination}",
                "uk": f"Розпаковано {result['files_extracted']} файлів до {destination}",
                "ru": f"Извлечено {result['files_extracted']} файлов в {destination}",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error extracting archive: {str(e)}",
                "uk": f"Помилка при розпаковці архіву: {str(e)}",
                "ru": f"Ошибка при распаковке архива: {str(e)}",
            },
        }


async def _execute_batch_operation(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Perform an operation on multiple files matching a pattern."""
    import os
    import fnmatch
    import shutil
    from datetime import datetime

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "operation pattern [source] [include_subdirs=true] [dry_run=false]"
    parts = args.strip().split()
    if len(parts) < 3:
        return {
            "success": False,
            "output": "",
            "error": "Usage: batch_operation <operation> <pattern> [source] [include_subdirs] [dry_run]",
            "spoken": {
                "en": "Usage: batch_operation <operation> <pattern> [source] [include_subdirs] [dry_run]",
                "uk": "Використання: batch_operation <operation> <pattern> [source] [include_subdirs] [dry_run]",
                "ru": "Использование: batch_operation <operation> <pattern> [source] [include_subdirs] [dry_run]",
            },
        }

    operation = parts[0].lower()
    pattern = parts[1]
    source = parts[2] if len(parts) > 2 else str(workspace)
    include_subdirs = len(parts) > 3 and parts[3].lower() in ("true", "1", "yes")
    dry_run = len(parts) > 4 and parts[4].lower() in ("true", "1", "yes")

    # Resolve source path
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = workspace / source

    if not source_path.exists():
        return {
            "success": False,
            "output": "",
            "error": f"Source path not found: {source}",
            "spoken": {
                "en": f"Source path not found: {source}",
                "uk": f"Джерело не знайдено: {source}",
                "ru": f"Источник не найден: {source}",
            },
        }

    # Validate operation
    valid_ops = ["delete", "copy_to", "move_to", "list_info", "archive"]
    if operation not in valid_ops:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid operation: {operation}. Valid: {', '.join(valid_ops)}",
            "spoken": {
                "en": f"Invalid operation: {operation}. Valid: {', '.join(valid_ops)}",
                "uk": f"Неправильна операція: {operation}. Дозволені: {', '.join(valid_ops)}",
                "ru": f"Неверная операция: {operation}. Разрешенные: {', '.join(valid_ops)}",
            },
        }

    # Find matching files
    matched_files = []
    try:
        if include_subdirs:
            for root, dirs, files in os.walk(source_path):
                for file in fnmatch.filter(files, pattern):
                    full_path = Path(root) / file
                    matched_files.append(full_path)
        else:
            if source_path.is_dir():
                for item in source_path.iterdir():
                    if item.is_file() and fnmatch.fnmatch(item.name, pattern):
                        matched_files.append(item)
            elif source_path.is_file() and fnmatch.fnmatch(source_path.name, pattern):
                matched_files.append(source_path)
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Error finding files: {str(e)}",
            "spoken": {
                "en": f"Error finding files: {str(e)}",
                "uk": f"Помилка при пошуку файлів: {str(e)}",
                "ru": f"Ошибка при поиске файлов: {str(e)}",
            },
        }

    if not matched_files:
        return {
            "success": True,
            "output": f"No files matching pattern '{pattern}' found",
            "matched_count": 0,
            "error": None,
            "spoken": {
                "en": f"No files matching pattern '{pattern}' found",
                "uk": f"Файли зі збігом '{pattern}' не знайдено",
                "ru": f"Файлы по шаблону '{pattern}' не найдены",
            },
        }

    # Execute batch operation
    results = []
    errors = []
    total_size = 0

    if operation == "list_info":
        # Just list info without modifying
        for file_path in matched_files:
            try:
                stat = file_path.stat()
                total_size += stat.st_size
                results.append(
                    {
                        "path": str(file_path),
                        "name": file_path.name,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    }
                )
            except Exception as e:
                errors.append(str(file_path) + ": " + str(e))
    else:
        # Perform actual operation
        for file_path in matched_files:
            try:
                if operation == "delete":
                    if dry_run:
                        results.append(
                            {"action": "would_delete", "path": str(file_path)}
                        )
                    else:
                        file_path.unlink()
                        results.append({"action": "deleted", "path": str(file_path)})
                        total_size += file_path.stat().st_size

                elif operation in ["copy_to", "move_to"]:
                    dest_path = (
                        Path(source_path.parent)
                        / operation.replace("_to", "")
                        / file_path.name
                    )
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    if dry_run:
                        results.append(
                            {
                                "action": operation.replace(
                                    "_to", "would_" + operation
                                ),
                                "source": str(file_path),
                                "destination": str(dest_path),
                            }
                        )
                    else:
                        if operation == "copy_to":
                            shutil.copy2(file_path, dest_path)
                            results.append(
                                {
                                    "action": "copied",
                                    "source": str(file_path),
                                    "destination": str(dest_path),
                                }
                            )
                        elif operation == "move_to":
                            shutil.move(file_path, dest_path)
                            results.append(
                                {
                                    "action": "moved",
                                    "source": str(file_path),
                                    "destination": str(dest_path),
                                }
                            )
                        total_size += (
                            file_path.stat().st_size if file_path.exists() else 0
                        )

                elif operation == "archive":
                    if dry_run:
                        results.append(
                            {"action": "would_archive", "path": str(file_path)}
                        )
                    else:
                        pass  # Actual archiving would need more complex logic
            except Exception as e:
                errors.append(str(file_path) + ": " + str(e))

    result_summary = {
        "operation": operation,
        "pattern": pattern,
        "matched_count": len(matched_files),
        "processed_count": len(results),
        "error_count": len(errors),
        "total_size": total_size,
        "dry_run": dry_run,
        "results": results[:100],  # Limit results
        "errors": errors[:10],  # Limit errors
    }

    return {
        "success": True,
        "output": f"Batch {operation} on {len(results)} files{', dry run' if dry_run else ''}",
        "result": result_summary,
        "error": None if not errors else f"Errors with {len(errors)} files",
        "spoken": {
            "en": f"Batch {operation} completed for {len(results)} files{', dry run' if dry_run else ''}",
            "uk": f"Пакетна {operation} виконана для {len(results)} файлів{', тестовий режим' if dry_run else ''}",
            "ru": f"Пакетная {operation} завершена для {len(results)} файлов{', пробный запуск' if dry_run else ''}",
        },
    }


# ============================================================================
# Profile Management Tools
# ============================================================================


async def _execute_add_favorite(args: str, settings: "Settings | None" = None) -> dict:
    """Add a directory to favorites."""
    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "path [label]"
    parts = args.strip().split(maxsplit=1)
    if not parts:
        return {
            "success": False,
            "output": "",
            "error": "Usage: add_favorite <path> [label]",
            "spoken": {
                "en": "Usage: add_favorite <path> [label]",
                "uk": "Використання: add_favorite <path> [label]",
                "ru": "Использование: add_favorite <path> [label]",
            },
        }

    path_str = parts[0]
    label = parts[1] if len(parts) > 1 else None

    # Resolve path
    path = Path(path_str)
    if not path.is_absolute():
        path = workspace / path_str

    if not path.exists():
        return {
            "success": False,
            "output": "",
            "error": f"Path not found: {path_str}",
            "spoken": {
                "en": f"Path not found: {path_str}",
                "uk": f"Шлях не знайдено: {path_str}",
                "ru": f"Путь не найден: {path_str}",
            },
        }

    try:
        from src.store.user_profile import get_profile_manager

        profile_manager = await get_profile_manager(
            Path.home() / ".heare" / "profile.json"
        )
        await profile_manager.add_favorite(path, label)

        return {
            "success": True,
            "output": f"Added {path.name} to favorites",
            "error": None,
            "spoken": {
                "en": f"Added {path.name} to favorites",
                "uk": f"Додано {path.name} до улюблених",
                "ru": f"Добавлено {path.name} в избранное",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Error adding favorite: {str(e)}",
            "spoken": {
                "en": f"Error adding favorite: {str(e)}",
                "uk": f"Помилка при додаванні до улюблених: {str(e)}",
                "ru": f"Ошибка при добавлении в избранное: {str(e)}",
            },
        }


async def _execute_list_favorites(
    args: str, settings: "Settings | None" = None
) -> dict:
    """List favorite locations."""
    try:
        from src.store.user_profile import get_profile_manager

        profile_manager = await get_profile_manager(
            Path.home() / ".heare" / "profile.json"
        )

        limit = 10
        parts = args.strip().split()
        if parts and parts[0].isdigit():
            limit = int(parts[0])

        favorites = profile_manager.get_favorites(limit)

        if not favorites:
            return {
                "success": True,
                "output": "No favorites yet",
                "error": None,
                "spoken": {
                    "en": "No favorites yet",
                    "uk": "Ще немає улюблених",
                    "ru": "Избранное пока пустое",
                },
            }

        items = []
        for fav in favorites:
            items.append(
                {
                    "path": fav["path"],
                    "label": fav["label"],
                    "access_count": fav.get("access_count", 0),
                    "last_accessed": fav.get("last_accessed", "Never"),
                }
            )

        return {
            "success": True,
            "output": f"Showing {len(items)} favorite locations",
            "items": items,
            "error": None,
            "spoken": {
                "en": f"You have {len(items)} favorite locations",
                "uk": f"У тебе {len(items)} улюблених місць",
                "ru": f"У тебя {len(items)} избранных мест",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Error listing favorites: {str(e)}",
            "spoken": {
                "en": f"Error listing favorites: {str(e)}",
                "uk": f"Помилка при переліку улюблених: {str(e)}",
                "ru": f"Ошибка при показе избранного: {str(e)}",
            },
        }


async def _execute_set_view_preference(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Set a view preference."""
    import json

    # Parse arguments - format: "json_key json_value"
    try:
        spec = json.loads(args)
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON: {e}",
            "spoken": {
                "en": f"Invalid JSON: {e}",
                "uk": f"Недійсний JSON: {e}",
                "ru": f"Неверный JSON: {e}",
            },
        }

    try:
        from src.store.user_profile import get_profile_manager

        profile_manager = await get_profile_manager(
            Path.home() / ".heare" / "profile.json"
        )

        for key, value in spec.items():
            await profile_manager.set_view_preference(key, value)

        return {
            "success": True,
            "output": f"Updated preferences: {list(spec.keys())}",
            "error": None,
            "spoken": {
                "en": f"Updated {len(spec)} preferences",
                "uk": f"Оновлено {len(spec)} налаштувань",
                "ru": f"Обновлено {len(spec)} настроек",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Error setting preferences: {str(e)}",
            "spoken": {
                "en": f"Error setting preferences: {str(e)}",
                "uk": f"Помилка при встановленні налаштувань: {str(e)}",
                "ru": f"Ошибка при установке настроек: {str(e)}",
            },
        }


async def _execute_show_profile(args: str, settings: "Settings | None" = None) -> dict:
    """Show current user profile settings."""
    try:
        from src.store.user_profile import get_profile_manager

        profile_manager = await get_profile_manager(
            Path.home() / ".heare" / "profile.json"
        )

        profile = profile_manager.profile
        if not profile:
            return {
                "success": True,
                "output": "No profile found",
                "error": None,
                "spoken": {
                    "en": "No profile found",
                    "uk": "Профіль не знайдено",
                    "ru": "Профиль не найден",
                },
            }

        # Build profile summary
        summary = {
            "allowed_directories": len(profile.allowed_directories),
            "favorite_locations": len(profile.favorite_locations),
            "ignored_patterns": len(profile.ignored_patterns),
            "view_preferences": profile.view_preferences,
            "search_history_count": len(profile.search_history),
            "access_log_count": len(profile.access_log),
        }

        return {
            "success": True,
            "output": json.dumps(summary, indent=2),
            "profile": summary,
            "error": None,
            "spoken": {
                "en": "Profile loaded with settings",
                "uk": "Профіль завантажено з налаштуваннями",
                "ru": "Профиль загружен с настройками",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Error showing profile: {str(e)}",
            "spoken": {
                "en": f"Error showing profile: {str(e)}",
                "uk": f"Помилка при показі профілю: {str(e)}",
                "ru": f"Ошибка при показе профиля: {str(e)}",
            },
        }


def _to_camel_case(name: str) -> str:
    """Convert lowercase_name to CamelCase."""
    return "".join(word.capitalize() for word in name.split("_"))


# ============================================================================
# Agent Skills handlers
# ============================================================================


async def _execute_list_skills(args: str, settings: "Settings | None" = None) -> dict:
    """List available Agent Skills."""
    try:
        from src.skills.agent_skills import get_skills_loader

        loader = get_skills_loader(settings)
        skills_meta = loader.discover()

        if not skills_meta:
            return {
                "success": True,
                "output": "No skills installed.",
                "spoken": {"en": "No skills available."},
            }

        output_lines = [f"- {s.name}: {s.description}" for s in skills_meta]
        output = "\n".join(output_lines)

        return {
            "success": True,
            "output": output,
            "spoken": {"en": f"Found {len(skills_meta)} skills available."},
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Error listing skills: {str(e)}",
            "spoken": {"en": "Failed to list skills."},
        }


async def _execute_run_skill(args: str, settings: "Settings | None" = None) -> dict:
    """Execute a skill by name with context dict.

    Args is JSON: ``{"name": "<skill>", "context": {...}}``. Both fields are
    required by the tool schema.
    """
    try:
        from src.skills.agent_skills import get_skills_loader

        try:
            payload = json.loads(args) if args else {}
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "error": f"Invalid run_skill JSON args: {e}",
                "spoken": {"en": "Invalid skill parameters."},
            }

        if not isinstance(payload, dict):
            return {
                "success": False,
                "error": "run_skill args must be a JSON object",
                "spoken": {"en": "Invalid skill parameters."},
            }

        name_part = str(payload.get("name", "")).strip()
        if not name_part:
            return {
                "success": False,
                "error": "run_skill requires 'name'",
                "spoken": {"en": "Missing skill name."},
            }
        context = payload.get("context") or {}
        if not isinstance(context, dict):
            return {
                "success": False,
                "error": "run_skill 'context' must be an object",
                "spoken": {"en": "Invalid skill context."},
            }

        loader = get_skills_loader(settings)

        try:
            instructions = loader.load_instructions(name_part)
        except KeyError:
            available = ", ".join(loader.get_skill_names())
            return {
                "success": False,
                "error": f"Skill '{name_part}' not found. Available skills: {available}",
                "spoken": {"en": f"Skill {name_part} not found."},
            }

        # Execute skill: internal dispatch to constituent heare tools
        action_timeout = settings.action_timeout_seconds if settings else 120
        result = await _execute_skill_internal(
            name_part,
            instructions,
            context,
            settings,
            action_timeout_seconds=action_timeout,
        )
        return result

    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Error running skill: {str(e)}",
            "spoken": {"en": "Skill execution failed."},
        }


async def _execute_skill_internal(
    skill_name: str,
    instructions: str,
    context: dict,
    settings: "Settings | None" = None,
    action_timeout_seconds: int = 120,
) -> dict:
    """Deliver the skill's SKILL.md body to the LLM as a tool result.

    Per the agentskills.io spec, SKILL.md is a *prompt* for the LLM to
    read and follow using its existing tools — not a DSL with embedded
    tool calls. This function substitutes any ``{var}`` placeholders
    from ``context`` and returns the body in ``output`` so the LLM
    consumes it on the next turn and acts on it via bash/read/etc.
    """
    body = instructions
    skill_dir = ""
    try:
        from src.skills.agent_skills import get_skills_loader

        loader = get_skills_loader(settings)
        for meta in loader.discover():
            if meta.name == skill_name:
                skill_dir = str(meta.path)
                break
    except Exception:  # noqa: BLE001
        logger.warning("[RUN_SKILL] could not resolve skill_dir for %s", skill_name)

    if skill_dir:
        body = body.replace("${CLAUDE_SKILL_DIR}", skill_dir)

    if context:
        for key, value in context.items():
            body = body.replace(f"{{{key}}}", str(value))

    header = f"You are now executing skill '{skill_name}'. Follow the instructions below using your existing tools (bash, read, web_search, etc.). Do not call run_skill again for this skill."
    if context:
        header += f" Context provided: {json.dumps(context)}"
    if skill_dir:
        header += f" Skill directory: {skill_dir}"

    output = f"{header}\n\n--- SKILL.md ---\n{body}"

    return {
        "success": True,
        "output": output,
        "skill": skill_name,
        "skill_dir": skill_dir,
        "spoken": {
            "en": f"Loaded {skill_name}.",
            "uk": f"Завантажив {skill_name}.",
        },
    }


async def _execute_set_provider(args: str, settings: "Settings | None" = None) -> dict:
    """Switch the active LLM provider via the State API.

    Args:
        args: Provider name (deepseek or zai)
        settings: heare Settings (unused, kept for signature compat)

    Returns:
        dict with success status and message
    """
    try:
        provider = args.strip().lower()

        if provider not in PROVIDERS:
            return {
                "success": False,
                "output": "",
                "error": f"Invalid provider: {provider}. Must be 'deepseek', 'zai', or 'opencode'",
                "spoken": {
                    "en": f"Invalid provider {provider}. Use 'deepseek', 'zai', or 'opencode'.",
                },
            }

        # Persist via State API
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    "http://127.0.0.1:9778/provider",
                    json={"provider": provider},
                    timeout=5,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "State API returned %s for provider", resp.status_code
                    )
            except httpx.RequestError as api_err:
                logger.warning("State API unavailable for provider: %s", api_err)

        return {
            "success": True,
            "output": f"LLM provider set to {provider}",
            "spoken": {
                "en": f"Switched to {provider}.",
                "uk": f"Перейшов на {provider}.",
            },
        }
    except Exception as e:
        logger.exception("_execute_set_provider failed")
        return {
            "success": False,
            "output": "",
            "error": f"Failed to set provider: {str(e)}",
            "spoken": {"en": "Failed to switch provider."},
        }


async def _execute_set_mode(args: str, settings: "Settings | None" = None) -> dict:
    """Switch the agent behavior mode at runtime.

    Flushes any half-spoken turn (so the switch does not eat the user's
    current sentence), persists via the State API so a daemon restart
    keeps the chosen mode, and flips the live SessionState so timing /
    sound / prompt / tool-gating all follow the new profile on the next
    turn. Always callable (exempt from gating).
    """
    try:
        from src.agent.modes import VALID_MODES
        from src.pipeline.session_state import get_active_session_state

        mode = args.strip().lower()
        if mode not in VALID_MODES:
            valid = ", ".join(VALID_MODES)
            return {
                "success": False,
                "output": "",
                "error": f"Invalid mode: {mode!r}. Valid modes: {valid}",
                "spoken": {
                    "en": f"I don't have a {mode} mode. Try: {valid}.",
                    "uk": f"Немає режиму {mode}. Доступні: {valid}.",
                },
            }

        # Persist via State API
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    "http://127.0.0.1:9778/mode",
                    json={"mode": mode},
                    timeout=5,
                )
                if resp.status_code != 200:
                    logger.warning("State API returned %s for mode", resp.status_code)
            except httpx.RequestError as api_err:
                logger.warning("State API unavailable for mode: %s", api_err)

        # Flush BEFORE flipping so a mid-sentence utterance is finalised
        # under the old mode rather than silently dropped.
        ss = get_active_session_state()
        if ss is not None:
            ss.flush_pending()
            ss.set_mode(mode)

        return {
            "success": True,
            "output": f"Mode set to {mode}",
            "spoken": {
                "en": f"Switched to {mode} mode.",
                "uk": f"Перейшов у режим {mode}.",
            },
        }
    except Exception as e:
        logger.exception("_execute_set_mode failed")
        return {
            "success": False,
            "output": "",
            "error": f"Failed to set mode: {str(e)}",
            "spoken": {"en": "Failed to switch mode."},
        }


_DISPLAY_FORMATS = {"text", "code", "ascii", "table", "markdown", "html"}


async def _execute_read_display(args: str, settings: "Settings | None" = None) -> dict:
    """Read what is currently on the display/canvas panel.

    Pull-on-demand: the screen contents used to be dumped into *every* system
    prompt (hundreds of chars of raw markup — pure noise). Now the prompt only
    notes that a panel exists; the agent calls this when it actually needs to
    see or reference the contents. Returns the raw content so the agent can
    reason about or reproduce the exact markup.
    """
    try:
        if settings is None:
            from src.config import load_settings

            settings = load_settings()
        if not settings.db_path:
            return {"success": False, "output": "", "error": "no db_path configured"}
        from src.store.storage import TranscriptStore

        store = TranscriptStore(settings.db_path)
        try:
            await store.init()
            disp = await store.latest_display()
        finally:
            await store.close()

        if not disp or not disp.get("content"):
            return {
                "success": True,
                "output": "The screen panel is empty.",
                "spoken": {
                    "en": "Nothing is on the screen.",
                    "uk": "На екрані нічого немає.",
                },
            }
        fmt = disp.get("format") or "text"
        title = (disp.get("title") or "").strip()
        header = f"Screen panel (format={fmt}"
        header += f', title="{title}")' if title else ")"
        return {
            "success": True,
            "output": f"{header}:\n{disp['content']}",
            "spoken": {"en": "Read the screen.", "uk": "Прочитав екран."},
        }
    except Exception as e:
        logger.exception("_execute_read_display failed")
        return {
            "success": False,
            "output": "",
            "error": f"Failed to read display: {e}",
            "spoken": {"en": "Could not read the screen."},
        }


async def _execute_show_display(args: str, settings: "Settings | None" = None) -> dict:
    """Render a rich block on the watch dashboard display panel.

    Args is a JSON blob {content, format, title?}. Persists to the
    displays table (latest-only channel); the dashboard renders the
    newest row. Speech is unaffected — the agent should speak a short
    pointer and put the full block here.
    """
    try:
        try:
            payload = json.loads(args) if args else {}
        except json.JSONDecodeError:
            payload = {}
        content = str(payload.get("content", "")).strip()
        fmt = str(payload.get("format", "text")).strip().lower()
        title = payload.get("title")
        title = str(title).strip() if title else None
        if not content:
            return {
                "success": False,
                "output": "",
                "error": "show_display requires non-empty content",
                "spoken": {"en": "Nothing to display."},
            }
        if fmt not in _DISPLAY_FORMATS:
            fmt = "text"

        if settings is None:
            from src.config import load_settings

            settings = load_settings()
        if not settings.db_path:
            return {
                "success": False,
                "output": "",
                "error": "no db_path configured",
            }
        from src.store.storage import TranscriptStore

        store = TranscriptStore(settings.db_path)
        try:
            await store.init()
            await store.log_display(content, fmt, title=title)
        finally:
            await store.close()

        return {
            "success": True,
            "output": f"Displayed {fmt} block ({len(content)} chars)"
            + (f": {title}" if title else ""),
            "spoken": {
                "en": "Showing it on the screen.",
                "uk": "Показую на екрані.",
            },
        }
    except Exception as e:
        logger.exception("_execute_show_display failed")
        return {
            "success": False,
            "output": "",
            "error": f"Failed to show display: {str(e)}",
            "spoken": {"en": "Could not show that."},
        }


# ============================================================================
# Capability discovery tools (US-007)
# ============================================================================

_capability_index_singleton = None
_LAST_DISCOVERY: dict = {}


def set_capability_index(index) -> None:
    """Wire a CapabilityIndex from the pipeline so the tool handlers can use it."""
    global _capability_index_singleton
    _capability_index_singleton = index


def _get_or_build_capability_index(settings):
    """Return the wired index, or build a fresh one from settings."""
    global _capability_index_singleton
    if _capability_index_singleton is not None:
        return _capability_index_singleton
    from src.agent.tools.capability_index import build_capability_index

    workspace = (
        settings.workspace_dir if settings else Path.home() / ".heare" / "workspace"
    )
    _capability_index_singleton = build_capability_index(settings, workspace)
    return _capability_index_singleton


def _entry_to_summary(entry) -> dict:
    return {
        "name": entry.name,
        "source": entry.source,
        "description": entry.description,
        "install_url": entry.install_url,
    }


async def _execute_discover_capability(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Args is JSON: {"intent": "..."}. Returns top-3 candidates from local + remote."""
    import time as _time

    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON args: {e}",
            "spoken": {"en": "Bad arguments."},
        }
    intent = str(payload.get("intent", "")).strip()
    prefer_remote = bool(payload.get("prefer_remote", False))
    if not intent:
        return {
            "success": False,
            "output": "",
            "error": "intent is required",
            "spoken": {"en": "Missing intent."},
        }

    from src.skills import discovery

    started = _time.monotonic()
    index = _get_or_build_capability_index(settings)

    if prefer_remote:
        try:
            entries = await discovery.discover_capability_remote(
                intent, settings=settings
            )
            source = "remote"
        except Exception as e:  # noqa: BLE001
            logger.warning("[CAPABILITY DISCOVERY] remote failed: %s", e)
            entries = []
    else:
        local_entries = await discovery.discover_capability_local(
            intent, index, top_k=3
        )
        entries = local_entries
        source = "local"
        if not entries:
            try:
                entries = await discovery.discover_capability_remote(
                    intent, settings=settings
                )
                source = "remote"
            except Exception as e:  # noqa: BLE001
                logger.warning("[CAPABILITY DISCOVERY] remote failed: %s", e)
                entries = []

    latency_ms = int((_time.monotonic() - started) * 1000)
    logger.info(
        "[CAPABILITY DISCOVERY] intent=%r results=%d source=%s latency_ms=%d",
        intent,
        len(entries),
        source,
        latency_ms,
    )

    if not entries:
        return {
            "success": True,
            "output": "",
            "results": [],
            "spoken": {
                "en": "I don't have a tool for that. Want me to look one up?",
                "uk": "Не маю інструменту для цього. Хочеш, я пошукаю?",
            },
        }

    top = entries[:3]
    for e in top:
        _LAST_DISCOVERY[e.name] = e
    summaries = [_entry_to_summary(e) for e in top]
    lines = [f"- {s['name']} ({s['source']}): {s['description']}" for s in summaries]
    output = "\n".join(lines)
    first = summaries[0]
    return {
        "success": True,
        "output": output,
        "results": summaries,
        "source": source,
        "spoken": {
            "en": f"Found {first['name']}. Install it?",
            "uk": f"Знайшов {first['name']}. Встановити?",
        },
    }


def _find_entry_by_slug(index, slug: str):
    for e in index.entries:
        if e.name == slug:
            return e
    return None


async def _execute_install_skill_tool(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Args is JSON: {"slug": "...", "user_confirmed": true, "replace": false}."""
    import time as _time
    from src.skills import installer as _installer

    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON args: {e}",
            "spoken": {"en": "Bad arguments."},
        }

    slug = str(payload.get("slug", "")).strip()
    user_confirmed = bool(payload.get("user_confirmed", False))
    replace = bool(payload.get("replace", False))
    if not slug:
        return {
            "success": False,
            "error": "slug is required",
            "spoken": {"en": "Missing slug."},
        }

    index = _get_or_build_capability_index(settings)
    entry = _find_entry_by_slug(index, slug) or _LAST_DISCOVERY.get(slug)
    if entry is None:
        return {
            "success": False,
            "output": "",
            "error": f"Unknown slug: {slug}. Run discover_capability first.",
            "spoken": {"en": f"I don't know {slug}. Search for it first."},
        }

    started = _time.monotonic()
    try:
        result = await _installer.install_skill(
            entry,
            settings=settings,
            capability_index=index,
            user_confirmed=user_confirmed,
            replace=replace,
        )
    except _installer.InstallRefused as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        logger.info(
            "[CAPABILITY INSTALL] slug=%s source=skill success=False latency_ms=%d reason=%s",
            slug,
            latency_ms,
            exc,
        )
        return {
            "success": False,
            "output": "",
            "error": f"refused: {exc}",
            "spoken": {"en": "Install refused."},
        }
    except _installer.InstallFailed as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        logger.info(
            "[CAPABILITY INSTALL] slug=%s source=skill success=False latency_ms=%d reason=%s",
            slug,
            latency_ms,
            exc,
        )
        return {
            "success": False,
            "output": "",
            "error": f"failed: {exc}",
            "spoken": {"en": "Install failed."},
        }

    latency_ms = int((_time.monotonic() - started) * 1000)
    logger.info(
        "[CAPABILITY INSTALL] slug=%s source=skill success=%s latency_ms=%d",
        slug,
        result.success,
        latency_ms,
    )
    return {
        "success": result.success,
        "output": result.message_en,
        "slug": result.slug,
        "requires_restart": result.requires_restart,
        "error_code": result.error_code,
        "spoken": {"en": result.message_en, "uk": result.message_uk},
    }


async def _execute_create_skill(args: str, settings: "Settings | None" = None) -> dict:
    """Args is JSON: {"name": "...", "description": "...", "body": "...",
    "user_confirmed": true, "replace": false}.

    Authors a user-supplied skill at ``~/.heare/skills/<name>/``. Same
    consent gate as install_skill — refuses without ``user_confirmed=true``
    and a configured consent method (speaker-ID or passphrase).
    """
    import time as _time
    from src.skills import installer as _installer

    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON args: {e}",
            "spoken": {"en": "Bad arguments."},
        }

    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "")).strip()
    body = payload.get("body", "")
    if not isinstance(body, str):
        body = str(body)
    user_confirmed = bool(payload.get("user_confirmed", False))
    replace = bool(payload.get("replace", False))

    index = _get_or_build_capability_index(settings)
    started = _time.monotonic()
    try:
        result = await _installer.create_skill(
            name=name,
            description=description,
            body=body,
            settings=settings,
            capability_index=index,
            user_confirmed=user_confirmed,
            replace=replace,
        )
    except _installer.InstallRefused as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        logger.info(
            "[CAPABILITY CREATE] slug=%s source=skill success=False latency_ms=%d reason=%s",
            name,
            latency_ms,
            exc,
        )
        return {
            "success": False,
            "output": "",
            "error": f"refused: {exc}",
            "error_code": str(exc),
            "spoken": {"en": "Create refused."},
        }
    except _installer.InstallFailed as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        logger.info(
            "[CAPABILITY CREATE] slug=%s source=skill success=False latency_ms=%d reason=%s",
            name,
            latency_ms,
            exc,
        )
        return {
            "success": False,
            "output": "",
            "error": f"failed: {exc}",
            "error_code": str(exc),
            "spoken": {"en": "Create failed."},
        }

    return {
        "success": result.success,
        "output": result.message_en,
        "slug": result.slug,
        "requires_restart": result.requires_restart,
        "error_code": result.error_code,
        "spoken": {"en": result.message_en, "uk": result.message_uk},
    }


async def _execute_stop_daemon(args: str, settings: "Settings | None" = None) -> dict:
    """Args is JSON: {"user_confirmed": true}.

    Schedules a SIGTERM to ``os.getpid()`` after a short delay so any
    in-flight TTS finishes playing. Does NOT block — returns
    immediately so the LLM's "shutting down" reply can finish
    streaming and reach TTS before the daemon actually exits.
    """
    from src.daemon import control as daemon_control

    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON args: {e}",
            "spoken": {"en": "Bad arguments."},
        }

    if not bool(payload.get("user_confirmed", False)):
        return {
            "success": False,
            "output": "",
            "error": "user_not_confirmed",
            "error_code": "user_not_confirmed",
            "spoken": {
                "en": "I need explicit confirmation to stop.",
                "uk": "Потрібне явне підтвердження для зупинки.",
            },
        }

    delay_s = float(payload.get("delay_s", 4.0))
    safe_task(daemon_control.schedule_stop(delay_s=delay_s), name="daemon-stop-exit")
    hosted = daemon_control.has_host_hooks()
    logger.info(
        "[CAPABILITY DAEMON] stop scheduled delay=%.2fs scope=%s",
        delay_s,
        "pipeline" if hosted else "process",
    )

    return {
        "success": True,
        "output": "stop scheduled",
        "spoken": {
            "en": (
                "Going quiet. Start me again from the menu bar."
                if hosted
                else "Shutting down now. Goodbye."
            ),
            "uk": (
                "Замовкаю. Запусти мене знову з меню-бара."
                if hosted
                else "Завершую роботу. До зустрічі."
            ),
        },
    }


async def _execute_restart_daemon(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Args is JSON: {"user_confirmed": true}.

    Two-step:
    1. Spawn a detached child that waits ``respawn_delay_s`` then
       runs ``hearectl start``. Detachment via ``start_new_session``
       means the child outlives this process.
    2. Schedule a SIGTERM to self after ``self_exit_delay_s`` so TTS
       has time to read the spoken-result message.

    If the detached spawn fails (e.g. ``hearectl`` missing), nothing
    is killed — we report failure and let the conversation continue.
    """
    from src.daemon import control as daemon_control

    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON args: {e}",
            "spoken": {"en": "Bad arguments."},
        }

    if not bool(payload.get("user_confirmed", False)):
        return {
            "success": False,
            "output": "",
            "error": "user_not_confirmed",
            "error_code": "user_not_confirmed",
            "spoken": {
                "en": "I need explicit confirmation to restart.",
                "uk": "Потрібне явне підтвердження для перезапуску.",
            },
        }

    self_exit_delay_s = float(payload.get("self_exit_delay_s", 4.0))

    # Hosted (menubar): the host restarts the pipeline in place — no detached
    # respawner, and the menu bar icon survives the cycle.
    if daemon_control.has_host_hooks():
        safe_task(
            daemon_control.schedule_restart(delay_s=self_exit_delay_s),
            name="daemon-restart-pipeline",
        )
        logger.info(
            "[CAPABILITY DAEMON] restart scheduled in-place delay=%.2fs",
            self_exit_delay_s,
        )
        return {
            "success": True,
            "output": "restart scheduled (in-place)",
            "spoken": {
                "en": "Restarting now. Be right back.",
                "uk": "Перезапускаюся. За мить повернуся.",
            },
        }

    # Respawn delay must be >= self_exit_delay_s so the child waits
    # until after this process is gone before calling cmd_start (which
    # otherwise refuses with "already running").
    respawn_delay_s = float(payload.get("respawn_delay_s", self_exit_delay_s + 1.5))

    try:
        respawner_pid = daemon_control.spawn_detached_respawn(delay_s=respawn_delay_s)
    except FileNotFoundError as exc:
        logger.warning("[CAPABILITY DAEMON] respawn spawn failed: %s", exc)
        return {
            "success": False,
            "output": "",
            "error": f"respawn_failed: {exc}",
            "error_code": "respawn_failed",
            "spoken": {
                "en": "Couldn't find the launcher script — staying up.",
                "uk": "Не знайшов скрипт запуску — лишаюсь онлайн.",
            },
        }

    safe_task(daemon_control.schedule_self_exit(delay_s=self_exit_delay_s), name="daemon-restart-exit")
    logger.info(
        "[CAPABILITY DAEMON] restart scheduled respawner_pid=%d "
        "respawn_delay=%.2fs self_exit_delay=%.2fs",
        respawner_pid,
        respawn_delay_s,
        self_exit_delay_s,
    )

    return {
        "success": True,
        "output": f"restart scheduled (respawner pid={respawner_pid})",
        "spoken": {
            "en": "Restarting now. Be right back.",
            "uk": "Перезапускаюся. За мить повернуся.",
        },
    }


async def _execute_install_mcp_server_tool(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Args is JSON: {"slug": "...", "user_confirmed": true, "replace": false}."""
    import time as _time
    from src.skills import installer as _installer

    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON args: {e}",
            "spoken": {"en": "Bad arguments."},
        }

    slug = str(payload.get("slug", "")).strip()
    user_confirmed = bool(payload.get("user_confirmed", False))
    replace = bool(payload.get("replace", False))
    if not slug:
        return {
            "success": False,
            "error": "slug is required",
            "spoken": {"en": "Missing slug."},
        }

    index = _get_or_build_capability_index(settings)
    entry = _find_entry_by_slug(index, slug) or _LAST_DISCOVERY.get(slug)
    if entry is None:
        return {
            "success": False,
            "output": "",
            "error": f"Unknown slug: {slug}. Run discover_capability first.",
            "spoken": {"en": f"I don't know {slug}. Search for it first."},
        }

    started = _time.monotonic()
    try:
        result = await _installer.install_mcp_server(
            entry,
            settings=settings,
            capability_index=index,
            user_confirmed=user_confirmed,
            replace=replace,
        )
    except _installer.InstallRefused as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        logger.info(
            "[CAPABILITY INSTALL] slug=%s source=mcp success=False latency_ms=%d reason=%s",
            slug,
            latency_ms,
            exc,
        )
        return {
            "success": False,
            "output": "",
            "error": f"refused: {exc}",
            "spoken": {"en": "Install refused."},
        }
    except _installer.InstallFailed as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        logger.info(
            "[CAPABILITY INSTALL] slug=%s source=mcp success=False latency_ms=%d reason=%s",
            slug,
            latency_ms,
            exc,
        )
        return {
            "success": False,
            "output": "",
            "error": f"failed: {exc}",
            "spoken": {"en": "Install failed."},
        }

    latency_ms = int((_time.monotonic() - started) * 1000)
    logger.info(
        "[CAPABILITY INSTALL] slug=%s source=mcp success=%s latency_ms=%d",
        slug,
        result.success,
        latency_ms,
    )
    return {
        "success": result.success,
        "output": result.message_en,
        "slug": result.slug,
        "requires_restart": result.requires_restart,
        "error_code": result.error_code,
        "spoken": {"en": result.message_en, "uk": result.message_uk},
    }


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


async def _execute_register_mcp_server(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Args is JSON: {slug, description, command, args, env?, source_url?, user_confirmed, replace?}.

    Builds an IndexEntry on the fly from user-supplied launch info and routes
    through the same install_mcp_server path as catalog/registry entries —
    same consent gate, same .mcp.json write, same restart prompt.
    """
    import time as _time
    from src.skills import installer as _installer
    from src.skills import marketplace as _market
    from src.agent.tools.capability_index import IndexEntry

    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON args: {e}",
            "spoken": {"en": "Bad arguments."},
        }

    slug = str(payload.get("slug", "")).strip().lower()
    description = str(payload.get("description", "")).strip()
    source_url = payload.get("source_url")
    user_confirmed = bool(payload.get("user_confirmed", False))
    replace = bool(payload.get("replace", False))

    if not slug or not _SLUG_RE.match(slug):
        return {
            "success": False,
            "error": "slug must match [a-z0-9][a-z0-9-]*",
            "spoken": {"en": "Bad slug."},
        }
    if not description:
        return {
            "success": False,
            "error": "description is required",
            "spoken": {"en": "Missing description."},
        }

    raw_launch = {"command": payload.get("command"), "args": payload.get("args")}
    if payload.get("env") is not None:
        raw_launch["env"] = payload.get("env")
    try:
        launch = _market._coerce_launch(raw_launch)
    except ValueError as exc:
        return {
            "success": False,
            "error": str(exc),
            "spoken": {"en": "Bad launch info."},
        }

    install_url = (
        str(source_url).strip()
        if isinstance(source_url, str) and source_url.strip()
        else None
    )

    entry = IndexEntry(
        source="mcp",
        name=slug,
        description=description,
        install_url=install_url,
        launch=launch,
    )

    index = _get_or_build_capability_index(settings)
    started = _time.monotonic()
    try:
        result = await _installer.install_mcp_server(
            entry,
            settings=settings,
            capability_index=index,
            user_confirmed=user_confirmed,
            replace=replace,
        )
    except _installer.InstallRefused as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        logger.info(
            "[CAPABILITY REGISTER] slug=%s success=False latency_ms=%d reason=%s",
            slug,
            latency_ms,
            exc,
        )
        return {
            "success": False,
            "output": "",
            "error": f"refused: {exc}",
            "spoken": {"en": "Install refused."},
        }
    except _installer.InstallFailed as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        logger.info(
            "[CAPABILITY REGISTER] slug=%s success=False latency_ms=%d reason=%s",
            slug,
            latency_ms,
            exc,
        )
        return {
            "success": False,
            "output": "",
            "error": f"failed: {exc}",
            "spoken": {"en": "Install failed."},
        }

    latency_ms = int((_time.monotonic() - started) * 1000)
    logger.info(
        "[CAPABILITY REGISTER] slug=%s success=%s latency_ms=%d",
        slug,
        result.success,
        latency_ms,
    )
    return {
        "success": result.success,
        "output": result.message_en,
        "slug": result.slug,
        "requires_restart": result.requires_restart,
        "error_code": result.error_code,
        "spoken": {"en": result.message_en, "uk": result.message_uk},
    }


async def _execute_revoke_capability(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Args is JSON: {"slug": "..."}.

    Removes a marketplace-installed skill or MCP server. Refuses if the
    skill lacks a `.install.json` sidecar (user-authored skills are protected).
    """
    import shutil as _shutil

    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "output": "",
            "error": f"Invalid JSON args: {e}",
            "spoken": {"en": "Bad arguments."},
        }

    slug = str(payload.get("slug", "")).strip()
    if not slug:
        return {
            "success": False,
            "error": "slug is required",
            "spoken": {"en": "Missing slug."},
        }

    skill_dir = Path.home() / ".heare" / "skills" / "_marketplace" / slug
    workspace = (
        settings.workspace_dir if settings else Path.home() / ".heare" / "workspace"
    )
    mcp_sidecar = Path(workspace) / ".mcp_install" / f"{slug}.json"

    removed_skill = False
    removed_mcp = False
    error_msg: str | None = None

    if skill_dir.exists():
        if not (skill_dir / ".install.json").exists():
            logger.info(
                "[CAPABILITY REVOKE] slug=%s success=False reason=no_sidecar", slug
            )
            return {
                "success": False,
                "output": "",
                "error": "user_authored_skill_protected",
                "spoken": {
                    "en": f"{slug} was not installed via discovery — refusing to remove.",
                    "uk": f"{slug} не встановлено через відкриття — не видаляю.",
                },
            }
        try:
            _shutil.rmtree(skill_dir)
            removed_skill = True
        except OSError as exc:
            error_msg = f"skill rmtree failed: {exc}"

    if mcp_sidecar.exists():
        try:
            from src.skills.mcp_utils import read_mcp_servers, write_mcp_servers

            servers = read_mcp_servers(Path(workspace))
            if slug in servers:
                del servers[slug]
                write_mcp_servers(Path(workspace), servers)
            try:
                mcp_sidecar.unlink()
            except OSError:
                pass
            removed_mcp = True
        except Exception as exc:  # noqa: BLE001
            error_msg = f"mcp removal failed: {exc}"

    if not removed_skill and not removed_mcp:
        logger.info("[CAPABILITY REVOKE] slug=%s success=False reason=not_found", slug)
        return {
            "success": False,
            "output": "",
            "error": "not_found",
            "spoken": {
                "en": f"I don't have {slug} installed.",
                "uk": f"У мене немає встановленого {slug}.",
            },
        }

    if error_msg is not None:
        logger.info(
            "[CAPABILITY REVOKE] slug=%s success=False reason=%s", slug, error_msg
        )
        return {
            "success": False,
            "output": "",
            "error": error_msg,
            "spoken": {"en": "Revoke failed."},
        }

    # Refresh in-process state so the change is visible mid-session.
    try:
        from src.skills.agent_skills import get_skills_loader

        loader = get_skills_loader(settings)
        loader.invalidate()
    except Exception:  # noqa: BLE001
        logger.warning("revoke: SkillsLoader.invalidate failed", exc_info=True)

    try:
        index = _get_or_build_capability_index(settings)
        index.rebuild()
    except Exception:  # noqa: BLE001
        logger.warning("revoke: capability_index.rebuild failed", exc_info=True)

    logger.info("[CAPABILITY REVOKE] slug=%s success=True", slug)
    return {
        "success": True,
        "output": f"Removed {slug}.",
        "slug": slug,
        "spoken": {"en": f"Removed {slug}.", "uk": f"Видалено {slug}."},
    }


_CATEGORY_ALIASES = {
    "built_in": "built_in",
    "built-in": "built_in",
    "builtin": "built_in",
    "tool": "built_in",
    "tools": "built_in",
    "skill": "skills",
    "skills": "skills",
    "mcp": "mcps",
    "mcps": "mcps",
    "mcp_server": "mcps",
    "mcp_servers": "mcps",
    "mcp-server": "mcps",
}


def _list_built_in_tools() -> list[dict]:
    """Enumerate every enabled tool from the static + dynamic registries.

    These are the code-backed functions the LLM can call directly via
    function-calling — bash, read, write, list_skills, run_skill, etc.
    Disabled tools are skipped so the LLM doesn't pitch a tool it can't
    actually invoke."""
    out: list[dict] = []
    try:
        from src.agent.tools.registry import get_all_tools

        for tool in get_all_tools().values():
            if not tool.enabled:
                continue
            out.append({"name": tool.name, "description": tool.description})
    except Exception:  # noqa: BLE001
        logger.warning("list_capabilities: built-in scan failed", exc_info=True)
    out.sort(key=lambda i: i["name"])
    return out


def _list_skills(settings: "Settings | None") -> list[dict]:
    """Every skill the loader can see — bundled and discovery-installed.

    Each row carries an ``installed_via_discovery`` flag so the LLM (and
    the user) can tell apart skills that shipped with the install from
    skills the user pulled at runtime."""
    out: list[dict] = []
    try:
        from src.skills.agent_skills import get_skills_loader

        loader = get_skills_loader(settings)
        for meta in loader.discover():
            out.append(
                {
                    "name": meta.name,
                    "description": meta.description,
                    "installed_via_discovery": bool(
                        getattr(meta, "installed_via_discovery", False)
                    ),
                }
            )
    except Exception:  # noqa: BLE001
        logger.warning("list_capabilities: skills discovery failed", exc_info=True)
    out.sort(key=lambda i: i["name"])
    return out


def _list_mcp_servers(settings: "Settings | None") -> list[dict]:
    """All MCP servers from the workspace ``.mcp.json`` config.

    This is the same file the daemon's MCP client reads on startup, so
    every server here is one the LLM can address via the
    ``mcp__<slug>__*`` family of tools (assuming it isn't disabled)."""
    out: list[dict] = []
    try:
        from src.skills.mcp_utils import read_mcp_servers

        workspace = (
            settings.workspace_dir
            if settings is not None
            else Path.home() / ".heare" / "workspace"
        )
        servers = read_mcp_servers(Path(workspace))
        for slug, entry in servers.items():
            desc = ""
            disabled = False
            if isinstance(entry, dict):
                desc = (entry.get("description") or "").strip()
                disabled = bool(entry.get("disabled"))
                if not desc:
                    cmd = entry.get("command") or ""
                    args_list = entry.get("args") or []
                    if cmd:
                        joined = " ".join(str(a) for a in args_list)
                        desc = f"{cmd} {joined}".strip()
            if not desc:
                desc = f"MCP server: {slug}"
            if disabled:
                desc = f"{desc} (disabled)"
            out.append({"name": slug, "description": desc, "disabled": disabled})
    except Exception:  # noqa: BLE001
        logger.warning("list_capabilities: mcp scan failed", exc_info=True)
    out.sort(key=lambda i: i["name"])
    return out


async def _execute_list_capabilities(
    args: str, settings: "Settings | None" = None
) -> dict:
    """List everything the agent can call, grouped into three buckets:
      * ``built_in`` — code-backed tools from :mod:`tool_registry`
      * ``skills``  — markdown procedures from the skills loader
      * ``mcps``    — external MCP servers from the workspace ``.mcp.json``

    Args is JSON: ``{"category": "..."}`` or ``{}``. ``category`` filters
    to one bucket and accepts friendly aliases (``tool``/``tools``,
    ``built-in``, ``skill``/``skills``, ``mcp``/``mcps``)."""
    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError:
        payload = {}
    raw_cat = str(payload.get("category", "")).strip().lower()
    category = _CATEGORY_ALIASES.get(raw_cat) if raw_cat else None

    built_in = _list_built_in_tools()
    skills = _list_skills(settings)
    mcps = _list_mcp_servers(settings)

    if category == "built_in":
        skills, mcps = [], []
    elif category == "skills":
        built_in, mcps = [], []
    elif category == "mcps":
        built_in, skills = [], []

    totals = {
        "built_in": len(built_in),
        "skills": len(skills),
        "mcps": len(mcps),
        "all": len(built_in) + len(skills) + len(mcps),
    }
    items = (
        [
            {"name": i["name"], "source": "built_in", "description": i["description"]}
            for i in built_in
        ]
        + [
            {"name": i["name"], "source": "skill", "description": i["description"]}
            for i in skills
        ]
        + [
            {"name": i["name"], "source": "mcp", "description": i["description"]}
            for i in mcps
        ]
    )

    # Human-readable text the LLM can echo if the user wants the long form.
    sections: list[str] = []
    if built_in:
        names = ", ".join(i["name"] for i in built_in[:8])
        more = f" +{len(built_in) - 8} more" if len(built_in) > 8 else ""
        sections.append(f"Built-in ({len(built_in)}): {names}{more}")
    if skills:
        names = ", ".join(i["name"] for i in skills[:8])
        more = f" +{len(skills) - 8} more" if len(skills) > 8 else ""
        sections.append(f"Skills ({len(skills)}): {names}{more}")
    if mcps:
        sections.append(f"MCP ({len(mcps)}): " + ", ".join(i["name"] for i in mcps))
    output = "\n".join(sections) if sections else "No capabilities."

    spoken_en = (
        f"I have {totals['built_in']} built-in tools, "
        f"{totals['skills']} skills, and {totals['mcps']} MCP servers."
    )
    spoken_uk = (
        f"У мене {totals['built_in']} вбудованих інструментів, "
        f"{totals['skills']} скілів і {totals['mcps']} MCP серверів."
    )

    return {
        "success": True,
        "output": output,
        # ``summary`` stays for backward-compat: True when the flat list
        # is large enough that the LLM should ask before reading aloud.
        "summary": totals["all"] > 12,
        "count": totals["all"],
        "totals": totals,
        "categories": {
            "built_in": built_in,
            "skills": skills,
            "mcps": mcps,
        },
        "items": items,
        "spoken": {"en": spoken_en, "uk": spoken_uk},
    }


# ── Browser bridge tools ──────────────────────────────────────────────────────

_BROWSER_NOT_CONNECTED = {
    "success": False,
    "error": "Browser not connected. Install the Heare Bridge extension from "
    "extensions/heare-bridge/ via chrome://extensions.",
    "retryable": False,
}


def _get_bridge_or_none():
    from src.agent.browser_bridge import _get_bridge

    return _get_bridge()


async def _execute_read_browser_page(
    args: str, settings: "Settings | None" = None
) -> dict:
    bridge = _get_bridge_or_none()
    if bridge is None:
        return dict(_BROWSER_NOT_CONNECTED)
    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    params: dict = {}
    if parsed.get("tab_id") is not None:
        params["tab_id"] = parsed["tab_id"]
    return await bridge.call("read_page", params)


async def _execute_list_browser_tabs(
    args: str, settings: "Settings | None" = None
) -> dict:
    bridge = _get_bridge_or_none()
    if bridge is None:
        return dict(_BROWSER_NOT_CONNECTED)
    return await bridge.list_tabs()


async def _execute_click_in_browser(
    args: str, settings: "Settings | None" = None
) -> dict:
    bridge = _get_bridge_or_none()
    if bridge is None:
        return dict(_BROWSER_NOT_CONNECTED)
    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    params: dict = {"selector": parsed.get("selector", "")}
    if parsed.get("tab_id") is not None:
        params["tab_id"] = parsed["tab_id"]
    return await bridge.call("click", params)


async def _execute_fill_in_browser(
    args: str, settings: "Settings | None" = None
) -> dict:
    bridge = _get_bridge_or_none()
    if bridge is None:
        return dict(_BROWSER_NOT_CONNECTED)
    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    params: dict = {
        "selector": parsed.get("selector", ""),
        "value": parsed.get("value", ""),
    }
    if parsed.get("tab_id") is not None:
        params["tab_id"] = parsed["tab_id"]
    return await bridge.call("fill", params)


async def _execute_navigate_browser(
    args: str, settings: "Settings | None" = None
) -> dict:
    bridge = _get_bridge_or_none()
    if bridge is None:
        return dict(_BROWSER_NOT_CONNECTED)
    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    params: dict = {"url": parsed.get("url", "")}
    if parsed.get("tab_id") is not None:
        params["tab_id"] = parsed["tab_id"]
    return await bridge.call("navigate", params)


async def _execute_extract_in_browser(
    args: str, settings: "Settings | None" = None
) -> dict:
    bridge = _get_bridge_or_none()
    if bridge is None:
        return dict(_BROWSER_NOT_CONNECTED)
    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    params: dict = {"selector": parsed.get("selector", "")}
    if parsed.get("tab_id") is not None:
        params["tab_id"] = parsed["tab_id"]
    return await bridge.call("extract", params)


async def _execute_open_browser_tab(
    args: str, settings: "Settings | None" = None
) -> dict:
    bridge = _get_bridge_or_none()
    if bridge is None:
        return dict(_BROWSER_NOT_CONNECTED)
    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    return await bridge.call("open_tab", {"url": parsed.get("url", "")})


async def _execute_activate_browser_tab(
    args: str, settings: "Settings | None" = None
) -> dict:
    bridge = _get_bridge_or_none()
    if bridge is None:
        return dict(_BROWSER_NOT_CONNECTED)
    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    params: dict = {}
    if parsed.get("tab_id") is not None:
        params["tab_id"] = parsed["tab_id"]
    return await bridge.call("activate_tab", params)


# ============================================================================
# Audio & mute tools
# ============================================================================


async def _execute_mute_bot(args: str, settings: "Settings | None" = None) -> dict:
    """Mute/unmute bot via HTTP API."""
    import json

    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    muted = bool(parsed.get("muted", True))
    target = "bot"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "http://127.0.0.1:9778/mute",
                json={"target": target},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning("State API returned %s for mute", resp.status_code)
        except httpx.RequestError as api_err:
            logger.warning("State API unavailable for mute: %s", api_err)
    return {
        "success": True,
        "output": f"Bot {'muted' if muted else 'unmuted'}",
        "spoken": {
            "en": f"Bot {'muted' if muted else 'unmuted'}.",
            "uk": f"Бот {'заглушений' if muted else 'ввімкнений'}.",
        },
    }


async def _execute_mute_mic(args: str, settings: "Settings | None" = None) -> dict:
    """Mute/unmute mic via HTTP API."""
    import json

    try:
        json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        pass
    target = "mic"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "http://127.0.0.1:9778/mute",
                json={"target": target},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning("State API returned %s for mute", resp.status_code)
        except httpx.RequestError as api_err:
            logger.warning("State API unavailable for mute: %s", api_err)
    return {
        "success": True,
        "output": "Mic toggled",
        "spoken": {
            "en": "Mic toggled.",
            "uk": "Мікрофон перемкнено.",
        },
    }


async def _execute_sidetone(args: str, settings: "Settings | None" = None) -> dict:
    """Enable or disable sidetone (monitor mic input through speakers)."""
    import json

    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        return {
            "success": False,
            "output": "",
            "error": "invalid arguments",
            "spoken": {"en": "Invalid sidetone arguments.", "uk": "Неправильні аргументи."},
        }
    enabled = bool(parsed.get("enabled", False))

    state_value = "1" if enabled else "0"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "http://127.0.0.1:9778/state",
                json={"key": "sidetone", "value": state_value},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning("State API returned %s for sidetone", resp.status_code)
        except httpx.RequestError as api_err:
            logger.warning("State API unavailable for sidetone: %s", api_err)

    if settings is not None:
        flag_path = settings.sidetone_file
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        if enabled:
            flag_path.touch(exist_ok=True)
        else:
            try:
                flag_path.unlink()
            except FileNotFoundError:
                pass

    return {
        "success": True,
        "output": f"Sidetone {'on' if enabled else 'off'}",
        "spoken": {
            "en": f"Sidetone {'enabled' if enabled else 'disabled'}.",
            "uk": f"Моніторинг мікрофону {'увімкнено' if enabled else 'вимкнено'}.",
        },
    }


async def _execute_audio_device(
    args: str,
    settings: "Settings | None" = None,
    target: str = "input",
) -> dict:
    """Switch audio input or output device."""
    import json

    try:
        parsed = json.loads(args) if args.strip() else {}
    except (ValueError, TypeError):
        parsed = {}
    name = str(parsed.get("name", "")).strip()
    if not name:
        return {
            "success": False,
            "output": "",
            "error": "Device name is required",
            "spoken": {"en": "Device name is required."},
        }
    if settings is not None:
        if target == "output" and hasattr(settings, "audio_output_device_file"):
            settings.audio_output_device_file.write_text(name)
        elif hasattr(settings, "audio_input_device_file"):
            settings.audio_input_device_file.write_text(name)
    return {
        "success": True,
        "output": f"Audio {target} device set to {name}",
        "spoken": {
            "en": f"Audio {target} device set to {name}.",
            "uk": f"Аудіо пристрій {target} встановлено на {name}.",
        },
    }


async def _execute_vad_sensitivity(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Adjust VAD sensitivity (confidence threshold)."""
    import json as _json

    try:
        params = _json.loads(args) if args.strip() else {}
    except (_json.JSONDecodeError, TypeError):
        params = {}
    level = float(params.get("level", 0.5))
    if not (0.0 <= level <= 1.0):
        return {
            "success": False,
            "output": "",
            "error": f"level must be between 0.0 and 1.0, got {level}",
            "spoken": {
                "en": "Sensitivity must be between 0.0 and 1.0.",
                "uk": "Чутливість має бути від 0.0 до 1.0.",
            },
        }
    pct = int(level * 100)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "http://127.0.0.1:9778/state",
                json={"key": "vad_sensitivity", "value": str(level)},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning(
                    "State API returned %s for vad_sensitivity", resp.status_code
                )
        except httpx.RequestError as api_err:
            logger.warning("State API unavailable for vad_sensitivity: %s", api_err)
    return {
        "success": True,
        "output": f"VAD sensitivity set to {pct}%",
        "spoken": {
            "en": f"VAD sensitivity set to {pct} percent.",
            "uk": f"Чутливість VAD встановлена на {pct} відсотків.",
        },
    }


async def _execute_mic_gain(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Adjust microphone input gain."""
    import json as _json

    try:
        params = _json.loads(args) if args.strip() else {}
    except (_json.JSONDecodeError, TypeError):
        params = {}
    gain = float(params.get("gain", 1.0))
    if not (0.0 <= gain <= 5.0):
        return {
            "success": False,
            "output": "",
            "error": f"gain must be between 0.0 and 5.0, got {gain}",
            "spoken": {
                "en": "Gain must be between 0.0 and 5.0.",
                "uk": "Підсилення має бути від 0.0 до 5.0.",
            },
        }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "http://127.0.0.1:9778/state",
                json={"key": "input_gain", "value": str(gain)},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning("State API returned %s for mic_gain", resp.status_code)
        except httpx.RequestError as api_err:
            logger.warning("State API unavailable for mic_gain: %s", api_err)
    return {
        "success": True,
        "output": f"Mic gain set to {gain:.1f}",
        "spoken": {
            "en": f"Microphone gain set to {gain:.1f}.",
            "uk": f"Підсилення мікрофона встановлено на {gain:.1f}.",
        },
    }


async def _execute_volume(
    args: str, settings: "Settings | None" = None
) -> dict:
    """Adjust speaker output volume."""
    import json as _json

    try:
        params = _json.loads(args) if args.strip() else {}
    except (_json.JSONDecodeError, TypeError):
        params = {}
    level = float(params.get("level", 1.0))
    if not (0.0 <= level <= 5.0):
        return {
            "success": False,
            "output": "",
            "error": f"level must be between 0.0 and 5.0, got {level}",
            "spoken": {
                "en": "Volume must be between 0.0 and 5.0.",
                "uk": "Гучність має бути від 0.0 до 5.0.",
            },
        }
    level_pct = int(level * 100)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "http://127.0.0.1:9778/state",
                json={"key": "output_volume", "value": str(level)},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning("State API returned %s for volume", resp.status_code)
        except httpx.RequestError as api_err:
            logger.warning("State API unavailable for volume: %s", api_err)
    return {
        "success": True,
        "output": f"Speaker volume set to {level_pct}%",
        "spoken": {
            "en": f"Speaker volume set to {level_pct} percent.",
            "uk": f"Гучність динаміка встановлена на {level_pct} відсотків.",
        },
    }


async def _execute_run_agent(args: str, settings: "Settings | None" = None) -> dict:
    """Execute a task via OpenCode sub-agent."""
    import json as _json

    try:
        params = _json.loads(args)
    except (_json.JSONDecodeError, TypeError):
        return {
            "success": False,
            "output": "",
            "error": "Invalid JSON arguments for run_agent",
            "spoken": {
                "en": "Could not parse sub-agent arguments.",
                "uk": "Не можу розібрати аргументи.",
            },
        }

    prompt = str(params.get("prompt", "")).strip()
    if not prompt:
        return {
            "success": False,
            "output": "",
            "error": "prompt is required for run_agent",
            "spoken": {
                "en": "I need a task description to delegate.",
                "uk": "Потрібен опис завдання.",
            },
        }

    cwd = params.get("cwd") or None
    model = params.get("model") or None
    session_id = params.get("session_id") or None

    if settings is not None:
        if model is None:
            model = settings.opencode_default_model
        timeout = settings.opencode_default_timeout
        binary = settings.opencode_binary
        max_chars = settings.opencode_max_output_chars
    else:
        timeout = 120.0
        binary = "opencode"
        max_chars = 8000

    result = await run_opencode(
        prompt=prompt,
        cwd=cwd,
        model=model,
        session_id=session_id,
        timeout=timeout,
        binary=binary,
    )

    # Truncate massive output
    output = result.get("output", "")
    if isinstance(output, str) and len(output) > max_chars:
        result["output"] = output[:max_chars] + "\n... (truncated)"

    # Build spoken summary
    if result.get("success"):
        cost = result.get("cost")
        tools = result.get("tool_calls", 0)
        parts = []
        if tools:
            parts.append(f"{tools} tool calls")
        if cost is not None:
            parts.append(f"${cost:.4f}")
        summary = ", ".join(parts) if parts else "done"
        result["spoken"] = {
            "en": f"Sub-agent finished ({summary}).",
            "uk": f"Суб-агент завершив ({summary}).",
        }
    else:
        result["spoken"] = {
            "en": "Sub-agent failed.",
            "uk": "Суб-агент не виконав завдання.",
        }

    return result


# ---------------------------------------------------------------------------
# Agent sub-agent handlers (multi-agent background system)
# ---------------------------------------------------------------------------


async def _execute_agent_start(args: str, settings: "Settings | None" = None) -> dict:
    import json as _json
    from src.agent.subagent_manager import get_agent_manager

    mgr = get_agent_manager()
    if mgr is None:
        return {
            "success": False,
            "error": "Agent manager not initialized",
            "spoken": {
                "en": "Sub-agent system not available.",
                "uk": "Система суб-агентів недоступна.",
            },
        }
    try:
        params = _json.loads(args)
    except Exception:
        return {
            "success": False,
            "error": "Invalid JSON",
            "spoken": {"en": "Invalid arguments.", "uk": "Неправильні аргументи."},
        }
    prompt = str(params.get("prompt", "")).strip()
    if not prompt:
        return {
            "success": False,
            "error": "prompt required",
            "spoken": {
                "en": "I need a task description.",
                "uk": "Потрібен опис завдання.",
            },
        }
    cwd = params.get("cwd") or None
    try:
        state = await mgr.start(prompt, cwd=cwd)
        return {
            "success": True,
            "session_id": state.session_id,
            "status": state.status,
            "port": state.port,
            "spoken": {"en": "Agent started.", "uk": "Агент запущений."},
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "spoken": {
                "en": "Failed to start agent.",
                "uk": "Не вдалося запустити агента.",
            },
        }


async def _execute_agent_status(args: str, settings: "Settings | None" = None) -> dict:
    import json as _json
    from src.agent.subagent_manager import get_agent_manager

    mgr = get_agent_manager()
    if mgr is None:
        return {"success": False, "error": "Agent manager not initialized"}
    params = _json.loads(args)
    sid = str(params.get("session_id", "")).strip()
    if not sid:
        return {"success": False, "error": "session_id required"}
    result = mgr.status(sid)
    result["success"] = "error" not in result
    return result


async def _execute_agent_result(args: str, settings: "Settings | None" = None) -> dict:
    import json as _json
    from src.agent.subagent_manager import get_agent_manager

    mgr = get_agent_manager()
    if mgr is None:
        return {"success": False, "error": "Agent manager not initialized"}
    params = _json.loads(args)
    sid = str(params.get("session_id", "")).strip()
    if not sid:
        return {"success": False, "error": "session_id required"}
    return mgr.result(sid)


async def _execute_agent_message(args: str, settings: "Settings | None" = None) -> dict:
    import json as _json
    from src.agent.subagent_manager import get_agent_manager

    mgr = get_agent_manager()
    if mgr is None:
        return {"success": False, "error": "Agent manager not initialized"}
    params = _json.loads(args)
    sid = str(params.get("session_id", "")).strip()
    prompt = str(params.get("prompt", "")).strip()
    if not sid or not prompt:
        return {"success": False, "error": "session_id and prompt required"}
    result = await mgr.message(sid, prompt)
    if result.get("success"):
        result["spoken"] = {
            "en": "Continuing agent session.",
            "uk": "Продовжую сесію агента.",
        }
    else:
        result["spoken"] = {"en": f"Cannot continue: {result.get('error', '')}"}
    return result


async def _execute_agent_cancel(args: str, settings: "Settings | None" = None) -> dict:
    import json as _json
    from src.agent.subagent_manager import get_agent_manager

    mgr = get_agent_manager()
    if mgr is None:
        return {"success": False, "error": "Agent manager not initialized"}
    params = _json.loads(args)
    sid = str(params.get("session_id", "")).strip()
    if not sid:
        return {"success": False, "error": "session_id required"}
    result = await mgr.cancel(sid)
    if result.get("cancelled"):
        result["spoken"] = {"en": "Agent cancelled.", "uk": "Агента скасовано."}
    else:
        result["spoken"] = {"en": f"Cancel failed: {result.get('error', '')}"}
    return result


async def _execute_agent_list(args: str, settings: "Settings | None" = None) -> dict:
    from src.agent.subagent_manager import get_agent_manager

    mgr = get_agent_manager()
    if mgr is None:
        return {"success": False, "error": "Agent manager not initialized"}
    agents = mgr.list_all()
    running = sum(
        1 for a in agents if a["status"] in ("running", "starting", "waiting_for_input")
    )
    return {
        "success": True,
        "agents": agents,
        "count": len(agents),
        "running": running,
        "max_concurrent": mgr._max_concurrent,
        "spoken": {
            "en": f"{len(agents)} agents ({running} running).",
            "uk": f"{len(agents)} агентів ({running} активних).",
        },
    }


async def _execute_agent_approve(args: str, settings: "Settings | None" = None) -> dict:
    import json as _json
    from src.agent.subagent_manager import get_agent_manager

    mgr = get_agent_manager()
    if mgr is None:
        return {"success": False, "error": "Agent manager not initialized"}
    params = _json.loads(args)
    sid = str(params.get("session_id", "")).strip()
    if not sid:
        return {"success": False, "error": "session_id required"}
    result = await mgr.approve(sid)
    if result.get("approved"):
        result["spoken"] = {"en": "Approved.", "uk": "Підтверджено."}
    else:
        result["spoken"] = {"en": result.get("error", "Approve failed.")}
    return result


async def _execute_agent_deny(args: str, settings: "Settings | None" = None) -> dict:
    import json as _json
    from src.agent.subagent_manager import get_agent_manager

    mgr = get_agent_manager()
    if mgr is None:
        return {"success": False, "error": "Agent manager not initialized"}
    params = _json.loads(args)
    sid = str(params.get("session_id", "")).strip()
    reason = str(params.get("reason", "")).strip() or None
    if not sid:
        return {"success": False, "error": "session_id required"}
    result = await mgr.deny(sid, reason=reason)
    result["spoken"] = {
        "en": "Denied." + (" Sent correction." if reason else ""),
        "uk": "Відхилено.",
    }
    return result


# ============================================================================
# Memory tool handlers
# ============================================================================


async def _execute_remember(args: str, settings: "Settings | None" = None) -> dict:
    if _memory_backend is None:
        return {
            "success": False,
            "error": "Memory backend not available",
            "spoken": {"en": "Memory system is not available."},
        }
    from src.memory.tools import remember

    return await remember(_memory_backend, args)


async def _execute_recall(args: str, settings: "Settings | None" = None) -> dict:
    if _memory_backend is None:
        return {"success": False, "error": "Memory backend not available"}
    from src.memory.tools import recall

    return await recall(_memory_backend, args)


async def _execute_forget(args: str, settings: "Settings | None" = None) -> dict:
    if _memory_backend is None:
        return {"success": False, "error": "Memory backend not available"}
    from src.memory.tools import forget

    return await forget(_memory_backend, args)


async def _execute_memory_status(args: str, settings: "Settings | None" = None) -> dict:
    if _memory_backend is None:
        return {"success": False, "error": "Memory backend not available"}
    from src.memory.tools import memory_status

    return await memory_status(_memory_backend, args)
