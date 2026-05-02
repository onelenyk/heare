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
import datetime as dt
import json
import logging
import os
import re
import signal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger("heare.direct_tools")

# Import tool definitions from central registry
from .tool_registry import get_direct_tools, get_claude_tools, is_mcp_tool  # noqa: E402

SIMPLE_TOOLS = get_direct_tools()
COMPLEX_TOOLS = get_claude_tools()


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
        tool: Tool name (bash, read, write, web_fetch, web_search, re_enroll, delete_profile, rename_profile)
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
    elif tool == "re_enroll":
        return await _execute_re_enroll(args, settings)
    elif tool == "list_profiles":
        return await _execute_list_profiles(args, settings)
    elif tool == "create_profile":
        return await _execute_create_profile(args, settings)
    elif tool == "rename_profile":
        return await _execute_rename_profile(args, settings)
    elif tool == "create_tool":
        return await _execute_create_tool(args, settings)
    elif tool == "update_tool":
        return await _execute_update_tool(args, settings)
    elif tool == "delete_tool":
        return await _execute_delete_tool(args, settings)
    elif tool == "list_tools":
        return await _execute_list_tools(args, settings)
    elif tool == "delete_profile":
        return await _execute_delete_profile(args, settings)
    elif tool == "list_directory":
        return await _execute_list_directory(args, settings)
    elif tool == "find_files":
        return await _execute_find_files(args, settings)
    elif tool == "get_tree_view":
        return await _execute_get_tree_view(args, settings)
    elif tool == "get_current_directory":
        return await _execute_get_current_directory(args, settings)
    elif tool == "get_file_info":
        return await _execute_get_file_info(args, settings)
    elif tool == "get_disk_usage":
        return await _execute_get_disk_usage(args, settings)
    elif tool == "get_file_hash":
        return await _execute_get_file_hash(args, settings)
    elif tool == "copy_file":
        return await _execute_copy_file(args, settings)
    elif tool == "move_file":
        return await _execute_move_file(args, settings)
    elif tool == "delete_file":
        return await _execute_delete_file(args, settings)
    elif tool == "create_directory":
        return await _execute_create_directory(args, settings)
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
    elif tool == "discover_capability":
        return await _execute_discover_capability(args, settings)
    elif tool == "install_skill_tool":
        return await _execute_install_skill_tool(args, settings)
    elif tool == "install_mcp_server_tool":
        return await _execute_install_mcp_server_tool(args, settings)
    elif tool == "revoke_capability":
        return await _execute_revoke_capability(args, settings)
    elif tool == "list_capabilities":
        return await _execute_list_capabilities(args, settings)
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
    """
    import subprocess

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

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Support "path: extra" format - extract only the path part
    filepath = args.split(":", 1)[0] if ":" in args else args
    path = Path(filepath)
    if not path.is_absolute():
        path = workspace / path

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

    workspace = settings.workspace_dir if settings else Path.cwd()
    filepath, content = args.split(":", 1)
    content = content.lstrip()  # Remove leading space after colon
    path = Path(filepath)
    if not path.is_absolute():
        path = workspace / path

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
        return {"success": False, "output": "", "error": "URL must start with http:// or https://"}

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
                    items.append({
                        "n": 0,
                        "title": f"📚 {kg_title}",
                        "url": "",
                        "snippet": kg_desc,
                        "kind": "answer_box",
                    })
                    text_blocks.append(f"📚 {kg_title}: {kg_desc}")

            n = 1
            for item in data.get("organic", [])[:5]:
                title = item.get("title", "")
                url = item.get("link", "")
                snippet = (item.get("snippet") or "").strip()
                if title and url:
                    if top_url is None:
                        top_url = url
                    items.append({
                        "n": n,
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                    })
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


async def _search_duckduckgo(
    query: str, settings: "Settings | None" = None
) -> dict:
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
                    link_iter[i + 1].start()
                    if i + 1 < len(link_iter)
                    else len(text)
                )
                chunk = text[m.end():next_pos]
                snippet_match = re.search(
                    r'class="result__snippet"[^>]*>(.*?)</a>',
                    chunk,
                    re.DOTALL,
                )
                snippet = ""
                if snippet_match:
                    raw = tag_strip.sub("", snippet_match.group(1))
                    snippet = ws_collapse.sub(" ", raw).strip()
                items.append({
                    "n": n,
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                })
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


def _join_names(names: list[str], lang: str) -> str:
    """Join a list of names with commas, using language-appropriate 'and' for the last item."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    conjunction = {"en": " and ", "uk": " і ", "ru": " и "}.get(lang, " and ")
    return ", ".join(names[:-1]) + conjunction + names[-1]


def _end_uk(n: int) -> str:
    """Ukrainian noun ending for 'профіл...' based on count."""
    if n % 10 == 1 and n % 100 != 11:
        return "ь"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "і"
    return "ів"


def _end_ru(n: int) -> str:
    """Russian noun ending for 'профил...' based on count."""
    if n % 10 == 1 and n % 100 != 11:
        return "ь"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "я"
    return "ей"


async def _execute_re_enroll(args: str, settings: "Settings | None" = None) -> dict:
    """Re-enroll the owner's voice.

    Records 15 seconds of audio and updates the owner embedding.
    Use this when recognition accuracy drops.

    Usage in conversation:
        - "heare, learn my voice"
        - "re-enroll my voice"
        - "update voice profile"

    Args:
        args: Optional duration in seconds (default: 15)
        settings: heare Settings (not used directly, loads own config)

    Returns:
        dict with success, output, error
    """
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        return {
            "success": False,
            "output": "",
            "error": "sounddevice required: pip install sounddevice",
        }

    from . import speaker_id as speaker_id_mod
    from .config import load_settings
    from .speaker_gallery import SpeakerGallery

    from .indication import IndicationKind, get_indication

    try:
        cfg = load_settings()
        duration = 15
        if args:
            try:
                duration = max(5, min(30, int(args)))
            except ValueError:
                pass

        sample_rate = 16000

        ind = get_indication()
        if ind is not None:
            # Audible 3-2-1 descending-tone countdown so the user knows when the
            # recording window opens. The countdown PCM is ~2.5s long; we sleep
            # 2.6s before opening the mic so the countdown audio plays out
            # through the speaker FIRST and does not leak into the recorded
            # samples (mic and speaker share the same room).
            ind.notify(IndicationKind.REENROLL_COUNTDOWN)
            await asyncio.sleep(2.6)
            ind.notify(
                IndicationKind.REENROLL_RECORDING_START,
                body=f"Recording {duration}s for re-enrollment — keep talking",
            )

        # sd.rec() returns immediately (PortAudio runs on its own thread),
        # but sd.wait() blocks the calling thread until the buffer is full.
        # Running both inside asyncio.to_thread keeps the daemon event loop
        # free so TTS frames, indication writes, and the edge-tts WSS
        # warmup can drain in parallel with the recording window.
        def _record() -> "np.ndarray":
            buf = sd.rec(
                int(duration * sample_rate),
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
            )
            sd.wait()
            return buf

        recording = await asyncio.to_thread(_record)

        if ind is not None:
            ind.notify(IndicationKind.REENROLL_RECORDING_END)

        # Convert to float32 and normalize
        audio = recording.astype(np.float32) / 32768.0

        # Get embedding
        model = speaker_id_mod.load_model()
        vector = speaker_id_mod.embed(audio.tobytes(), sample_rate, model)

        # Update gallery - preserve existing label
        gallery = SpeakerGallery.load(cfg.speakers_file)
        existing_label = gallery.get_label("owner") or "owner"
        gallery.enroll_owner(vector, label=existing_label)

        return {
            "success": True,
            "output": "Voice profile updated. I can now recognize you better.",
            "error": None,
            "spoken": {
                "en": "Voice profile updated.",
                "uk": "Голосовий профіль оновлено.",
                "ru": "Голосовой профиль обновлён.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Re-enroll failed: {e}",
        }


async def _execute_list_profiles(args: str, settings: "Settings | None" = None) -> dict:
    """List all voice profiles with details.

    Usage in conversation:
        - "show my profiles"
        - "list voice profiles"

    Args:
        args: ignored
        settings: heare Settings (not used directly, loads own config)

    Returns:
        dict with success, output (formatted table), error
    """
    from .config import load_settings
    from .speaker_gallery import SpeakerGallery

    try:
        cfg = load_settings()
        gallery = SpeakerGallery.load(cfg.speakers_file)

        speakers = gallery.list_speakers()
        if not speakers:
            return {
                "success": True,
                "output": "No voice profiles found.",
                "error": None,
                "spoken": {
                    "en": "No voice profiles yet.",
                    "uk": "Профілів поки немає.",
                    "ru": "Профилей пока нет.",
                },
            }

        lines = ["ID       | Label      | Refs | Created", "---------+------------+------+---------"]
        labels: list[str] = []
        for sid in speakers:
            entry = gallery.get_entry(sid)
            if entry:
                label = entry.get("label", sid)[:10]
                labels.append(entry.get("label", sid))
                refs = len(entry.get("embeddings", []))
                created = (entry.get("created_at", "")[:10] or "unknown")
                lines.append(f"{sid[:8]:<8} | {label:<10} | {refs:<4} | {created}")

        n = len(labels)
        names_en = _join_names(labels, "en")
        names_uk = _join_names(labels, "uk")
        names_ru = _join_names(labels, "ru")

        return {
            "success": True,
            "output": "\n".join(lines),
            "error": None,
            "spoken": {
                "en": f"You have {n} profile{'s' if n != 1 else ''}: {names_en}.",
                "uk": f"У тебе {n} профіл{_end_uk(n)}: {names_uk}.",
                "ru": f"У тебя {n} профил{_end_ru(n)}: {names_ru}.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"List profiles failed: {e}",
        }


async def _execute_create_profile(args: str, settings: "Settings | None" = None) -> dict:
    """Create a new voice profile (placeholder without voice data).

    Usage in conversation:
        - "create profile for Mom"
        - "add new profile Alice"

    Args:
        args: optional label for the profile
        settings: heare Settings (not used directly, loads own config)

    Returns:
        dict with success, output, error
    """
    from .config import load_settings
    from .speaker_gallery import SpeakerGallery, _iso_now
    from .indication import IndicationKind, get_indication

    try:
        cfg = load_settings()
        gallery = SpeakerGallery.load(cfg.speakers_file)
        ind = get_indication()

        label = args.strip() or None

        existing = [sid for sid in gallery._speakers if sid.startswith("guest_")]
        if len(existing) >= 99:
            return {
                "success": False,
                "output": "",
                "error": "Max 99 guest profiles allowed",
                "spoken": {
                    "en": "Cannot create more than 99 guest profiles.",
                    "uk": "Не можу створити більше 99 гостьових профілів.",
                    "ru": "Не могу создать больше 99 гостевых профилей.",
                },
            }

        next_n = 1
        taken = set()
        for sid in existing:
            try:
                taken.add(int(sid.split("_", 1)[1]))
            except (ValueError, IndexError):
                continue
        while next_n in taken:
            next_n += 1
        guest_id = f"guest_{next_n:02d}"
        now = _iso_now()
        gallery._speakers[guest_id] = {
            "label": label or guest_id,
            "embeddings": [],
            "created_at": now,
            "updated_at": now,
            "turn_count": 0,
        }
        gallery.save()

        if ind is not None:
            ind.notify(IndicationKind.PROFILE_CREATED)

        name = label or guest_id
        return {
            "success": True,
            "output": f"Created profile '{guest_id}' (placeholder — will learn voice automatically)",
            "error": None,
            "spoken": {
                "en": f"Created profile {name}.",
                "uk": f"Створив профіль {name}.",
                "ru": f"Создал профиль {name}.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Create profile failed: {e}",
        }


async def _execute_delete_profile(args: str, settings: "Settings | None" = None) -> dict:
    """Delete a speaker profile.

    Usage in conversation:
        - "delete profile guest_01"
        - "remove guest_01"

    Args:
        args: speaker_id to delete
        settings: heare Settings (not used directly, loads own config)

    Returns:
        dict with success, output, error
    """
    from .config import load_settings
    from .speaker_gallery import SpeakerGallery
    from .indication import IndicationKind, get_indication

    try:
        cfg = load_settings()
        speaker_id = args.strip()

        if not speaker_id:
            return {
                "success": False,
                "output": "",
                "error": "Speaker ID required",
            }

        if speaker_id == "owner":
            return {
                "success": False,
                "output": "",
                "error": "Cannot delete owner profile",
                "spoken": {
                    "en": "Cannot delete the owner profile.",
                    "uk": "Не можу видалити профіль власника.",
                    "ru": "Не могу удалить профиль владельца.",
                },
            }

        gallery = SpeakerGallery.load(cfg.speakers_file)
        removed = gallery.remove_speaker(speaker_id)

        if removed:
            ind = get_indication()
            if ind is not None:
                ind.notify(IndicationKind.PROFILE_DELETED)

            return {
                "success": True,
                "output": f"Profile '{speaker_id}' deleted",
                "error": None,
                "spoken": {
                    "en": f"Deleted profile {speaker_id}.",
                    "uk": f"Видалив профіль {speaker_id}.",
                    "ru": f"Удалил профиль {speaker_id}.",
                },
            }
        else:
            return {
                "success": False,
                "output": "",
                "error": f"Profile '{speaker_id}' not found",
                "spoken": {
                    "en": f"Profile {speaker_id} not found.",
                    "uk": f"Профіль {speaker_id} не знайдено.",
                    "ru": f"Профиль {speaker_id} не найден.",
                },
            }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Delete profile failed: {e}",
        }


async def _execute_rename_profile(args: str, settings: "Settings | None" = None) -> dict:
    """Rename a speaker profile.

    Usage in conversation:
        - "rename owner to Alice"
        - "rename profile guest_01 to Bob"

    Args:
        args: "<speaker_id> <new_label>"
        settings: heare Settings (not used directly, loads own config)

    Returns:
        dict with success, output, error
    """
    from .config import load_settings
    from .speaker_gallery import SpeakerGallery
    from .indication import IndicationKind, get_indication

    try:
        cfg = load_settings()

        # Parse args: split on whitespace, max 2 parts
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            return {
                "success": False,
                "output": "",
                "error": "Usage: rename_profile <id> <new_label>",
            }

        speaker_id, new_label = parts[0], parts[1]

        # Load gallery and rename
        gallery = SpeakerGallery.load(cfg.speakers_file)
        success = gallery.rename_speaker(speaker_id, new_label)

        if success:
            ind = get_indication()
            if ind is not None:
                ind.notify(IndicationKind.PROFILE_RENAMED)
            return {
                "success": True,
                "output": f"Profile '{speaker_id}' renamed to '{new_label}'",
                "error": None,
                "spoken": {
                    "en": f"Renamed {speaker_id} to {new_label}.",
                    "uk": f"Перейменував {speaker_id} на {new_label}.",
                    "ru": f"Переименовал {speaker_id} в {new_label}.",
                },
            }
        else:
            return {
                "success": False,
                "output": "",
                "error": f"Profile '{speaker_id}' not found",
                "spoken": {
                    "en": f"Profile {speaker_id} not found.",
                    "uk": f"Профіль {speaker_id} не знайдено.",
                    "ru": f"Профиль {speaker_id} не найден.",
                },
            }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Rename failed: {e}",
        }


async def _execute_create_tool(args: str, settings: "Settings | None" = None) -> dict:
    """Create a new dynamic tool.

    Expects JSON args with: name, description, arguments, implementation_type, implementation
    """
    import json

    from .tool_registry import ToolDefinition, register_dynamic_tool, Tool, is_static_tool
    from .storage import TranscriptStore

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
    from . import llm_tools
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

    from .tool_registry import is_static_tool, _DYNAMIC_TOOLS
    from .storage import TranscriptStore

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
    elif "arguments" in spec or "implementation_type" in spec or "implementation" in spec:
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
    from .tool_registry import unregister_dynamic_tool, is_static_tool
    from .storage import TranscriptStore

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
        from . import llm_tools
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
    from .tool_registry import get_all_tools, is_dynamic_tool

    all_tools = get_all_tools()
    items = []
    for name, tool in sorted(all_tools.items()):
        items.append({
            "name": name,
            "description": tool.description,
            "type": "dynamic" if is_dynamic_tool(name) else "built-in",
        })

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

async def _execute_list_directory(args: str, settings: "Settings | None" = None) -> dict:
    """List contents of a directory with optional details."""

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "path [show_hidden=bool] [detail=level]"
    parts = args.strip().split()
    if not parts:
        path = workspace
        show_hidden = False
        detail = "standard"
    else:
        path = parts[0]
        show_hidden = len(parts) > 1 and parts[1].lower() in ("true", "1", "yes")
        detail = parts[2] if len(parts) > 2 else "standard"

    # Resolve path
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = workspace / file_path

    if not file_path.exists():
        return {
            "success": False,
            "output": "",
            "error": f"Directory not found: {path}",
            "spoken": {
                "en": f"Directory not found: {path}",
                "uk": f"Директорію не знайдено: {path}",
                "ru": f"Директория не найдена: {path}",
            },
        }

    if not file_path.is_dir():
        return {
            "success": False,
            "output": "",
            "error": f"Not a directory: {path}",
            "spoken": {
                "en": f"Not a directory: {path}",
                "uk": f"Не є директорією: {path}",
                "ru": f"Не является каталогом: {path}",
            },
        }

    try:
        items = []
        for item in sorted(file_path.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue

            item_info = {"name": item.name, "path": str(item)}

            if detail in ["standard", "detailed"]:
                item_info["type"] = "directory" if item.is_dir() else "file"
                item_info["size"] = item.stat().st_size if item.is_file() else 0

                if detail == "detailed":
                    stat = item.stat()
                    item_info["modified"] = dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    item_info["permissions"] = oct(stat.st_mode)[-3:]

        return {
            "success": True,
            "output": f"Directory contains {len(items)} items",
            "items": items,
            "error": None,
            "spoken": {
                "en": f"Directory contains {len(items)} items.",
                "uk": f"Директорія містить {len(items)} елементів.",
                "ru": f"Каталог содержит {len(items)} элементов.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error listing directory: {str(e)}",
                "uk": f"Помилка при переліку директорії: {str(e)}",
                "ru": f"Ошибка при просмотре каталога: {str(e)}",
            },
        }


async def _execute_find_files(args: str, settings: "Settings | None" = None) -> dict:
    """Find files by pattern (name, extension, recursive)."""
    import os
    import fnmatch

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "path pattern [recursive=bool]"
    parts = args.strip().split()
    if len(parts) < 2:
        return {
            "success": False,
            "output": "",
            "error": "Usage: find_files <path> <pattern> [recursive]",
            "spoken": {
                "en": "Usage: find_files <path> <pattern> [recursive]",
                "uk": "Використання: find_files <path> <pattern> [recursive]",
                "ru": "Использование: find_files <path> <pattern> [recursive]",
            },
        }

    path = parts[0]
    pattern = parts[1]
    recursive = len(parts) > 2 and parts[2].lower() in ("true", "1", "yes")

    # Resolve path
    search_path = Path(path)
    if not search_path.is_absolute():
        search_path = workspace / search_path

    if not search_path.exists():
        return {
            "success": False,
            "output": "",
            "error": f"Path not found: {path}",
            "spoken": {
                "en": f"Path not found: {path}",
                "uk": f"Шлях не знайдено: {path}",
                "ru": f"Путь не найден: {path}",
            },
        }

    found_files = []

    try:
        if recursive:
            for root, dirs, files in os.walk(search_path):
                for file in fnmatch.filter(files, pattern):
                    full_path = Path(root) / file
                    found_files.append({
                        "path": str(full_path),
                        "name": file,
                        "directory": str(full_path.parent),
                    })
        else:
            if search_path.is_dir():
                for item in search_path.iterdir():
                    if item.is_file() and fnmatch.fnmatch(item.name, pattern):
                        found_files.append({
                            "path": str(item),
                            "name": item.name,
                            "directory": str(item.parent),
                        })

        return {
            "success": True,
            "output": f"Found {len(found_files)} matching files",
            "items": found_files[:100],  # Limit to 100 results
            "error": None,
            "spoken": {
                "en": f"Found {len(found_files)} matching files.",
                "uk": f"Знайдено {len(found_files)} відповідних файлів.",
                "ru": f"Найдено {len(found_files)} совпадающих файлов.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error finding files: {str(e)}",
                "uk": f"Помилка при пошуку файлів: {str(e)}",
                "ru": f"Ошибка при поиске файлов: {str(e)}",
            },
        }


async def _execute_get_tree_view(args: str, settings: "Settings | None" = None) -> dict:
    """Get recursive directory tree structure."""

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "path [max_depth=5] [show_hidden=false]"
    parts = args.strip().split()
    if not parts:
        path = workspace
        max_depth = 5
        show_hidden = False
    else:
        path = parts[0]
        max_depth = int(parts[1]) if len(parts) > 1 else 5
        show_hidden = len(parts) > 2 and parts[2].lower() in ("true", "1", "yes")

    # Resolve path
    tree_path = Path(path)
    if not tree_path.is_absolute():
        tree_path = workspace / tree_path

    if not tree_path.exists():
        return {
            "success": False,
            "output": "",
            "error": f"Path not found: {path}",
            "spoken": {
                "en": f"Path not found: {path}",
                "uk": f"Шлях не знайдено: {path}",
                "ru": f"Путь не найден: {path}",
            },
        }

    def build_tree(current_path, current_depth=0):
        if current_depth >= max_depth:
            return []

        items = []
        try:
            for item in sorted(current_path.iterdir()):
                if not show_hidden and item.name.startswith("."):
                    continue

                indent = "  " * current_depth
                if item.is_dir():
                    items.append(f"{indent}{item.name}/")
                    subtree = build_tree(item, current_depth + 1)
                    items.extend(subtree)
                else:
                    size = item.stat().st_size
                    items.append(f"{indent}{item.name} ({size} bytes)")
        except PermissionError:
            items.append(f"{indent}[Permission Denied]")
        except Exception as e:
            items.append(f"{indent}[Error: {str(e)}]")

        return items

    try:
        tree_lines = build_tree(tree_path)
        return {
            "success": True,
            "output": "\n".join(tree_lines),
            "error": None,
            "spoken": {
                "en": f"Directory tree with {len(tree_lines)} lines.",
                "uk": f"Дерево директорій з {len(tree_lines)} рядків.",
                "ru": f"Дерево каталогов из {len(tree_lines)} строк.",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error building tree: {str(e)}",
                "uk": f"Помилка при побудові дерева: {str(e)}",
                "ru": f"Ошибка при построении дерева: {str(e)}",
            },
        }


async def _execute_get_current_directory(args: str, settings: "Settings | None" = None) -> dict:
    """Get current working directory path."""
    import os

    try:
        cwd = os.getcwd()
        return {
            "success": True,
            "output": cwd,
            "error": None,
            "spoken": {
                "en": f"Current directory: {cwd}",
                "uk": f"Поточна директорія: {cwd}",
                "ru": f"Текущий каталог: {cwd}",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error getting current directory: {str(e)}",
                "uk": f"Помилка при отриманні поточної директорії: {str(e)}",
                "ru": f"Ошибка при получении текущего каталога: {str(e)}",
            },
        }


# ============================================================================
# File Metadata Tools
# ============================================================================

async def _execute_get_file_info(args: str, settings: "Settings | None" = None) -> dict:
    """Get detailed information about a file or directory."""
    import os
    import stat

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "path"
    path_str = args.strip()
    if not path_str:
        return {
            "success": False,
            "output": "",
            "error": "Usage: get_file_info <path>",
            "spoken": {
                "en": "Usage: get_file_info <path>",
                "uk": "Використання: get_file_info <path>",
                "ru": "Использование: get_file_info <path>",
            },
        }

    # Resolve path
    file_path = Path(path_str)
    if not file_path.is_absolute():
        file_path = workspace / file_path

    if not file_path.exists():
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
        stat_info = file_path.stat()
        is_dir = file_path.is_dir()
        is_file = file_path.is_file()

        info = {
            "name": file_path.name,
            "path": str(file_path),
            "type": "directory" if is_dir else "file",
            "size": stat_info.st_size,
            "created": dt.datetime.fromtimestamp(stat_info.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
            "modified": dt.datetime.fromtimestamp(stat_info.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "accessed": dt.datetime.fromtimestamp(stat_info.st_atime).strftime("%Y-%m-%d %H:%M:%S"),
        }

        if is_file:
            # File-specific info
            info["human_size"] = _human_readable_size(stat_info.st_size)
        else:
            # Directory-specific info - get total size recursively
            total_size = sum(f.stat().st_size for f in file_path.rglob('*') if f.is_file())
            info["total_size"] = total_size
            info["item_count"] = sum(1 for _ in file_path.rglob('*') if _.is_file())
            info["subdirectories"] = sum(1 for _ in file_path.rglob('*') if _.is_dir())

        # Permissions
        mode = stat_info.st_mode
        info["permissions"] = {
            "octal": oct(mode)[-3:],
            "readable": os.access(file_path, os.R_OK),
            "writable": os.access(file_path, os.W_OK),
            "executable": os.access(file_path, os.X_OK),
            "user_read": bool(mode & stat.S_IRUSR),
            "user_write": bool(mode & stat.S_IWUSR),
            "user_execute": bool(mode & stat.S_IXUSR),
        }

        return {
            "success": True,
            "output": str(info),
            "info": info,
            "error": None,
            "spoken": {
                "en": f"Info for {info['name']}: {info['type']} ({info['human_size'] if 'human_size' in info else _human_readable_size(info['total_size'])})",
                "uk": f"Інформація для {info['name']}: {info['type']} ({info['human_size'] if 'human_size' in info else _human_readable_size(info['total_size'])})",
                "ru": f"Информация о {info['name']}: {info['type']} ({info['human_size'] if 'human_size' in info else _human_readable_size(info['total_size'])})",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error getting file info: {str(e)}",
                "uk": f"Помилка при отриманні інформації: {str(e)}",
                "ru": f"Ошибка при получении информации: {str(e)}",
            },
        }


async def _execute_get_disk_usage(args: str, settings: "Settings | None" = None) -> dict:
    """Get disk usage for a path (total, used, free)."""
    import shutil

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "path"
    path_str = args.strip()
    if not path_str:
        path = workspace
    else:
        # Resolve path
        path = Path(path_str)
        if not path.is_absolute():
            path = workspace / path

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
        total, used, free = shutil.disk_usage(path)
        usage = {
            "path": str(path),
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "total": _human_readable_size(total),
            "used": _human_readable_size(used),
            "free": _human_readable_size(free),
            "usage_percent": round((used / total) * 100, 1) if total > 0 else 0,
            "file_count": sum(1 for _ in path.rglob('*') if _.is_file()) if path.is_dir() else 0,
            "dir_count": sum(1 for _ in path.rglob('*') if _.is_dir()) if path.is_dir() else 0,
        }

        return {
            "success": True,
            "output": str(usage),
            "usage": usage,
            "error": None,
            "spoken": {
                "en": f"Disk usage: {usage['used']} of {usage['total']} used ({usage['usage_percent']}%)",
                "uk": f"Використання диска: {usage['used']} з {usage['total']} використано ({usage['usage_percent']}%)",
                "ru": f"Использование диска: {usage['used']} из {usage['total']} занято ({usage['usage_percent']}%)",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error getting disk usage: {str(e)}",
                "uk": f"Помилка при отриманні використання диска: {str(e)}",
                "ru": f"Ошибка при получении использования диска: {str(e)}",
            },
        }


async def _execute_get_file_hash(args: str, settings: "Settings | None" = None) -> dict:
    """Calculate MD5 or SHA256 hash of a file."""
    import hashlib

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "path [algorithm=md5]"
    parts = args.strip().split()
    if not parts:
        return {
            "success": False,
            "output": "",
            "error": "Usage: get_file_hash <path> [algorithm]",
            "spoken": {
                "en": "Usage: get_file_hash <path> [algorithm]",
                "uk": "Використання: get_file_hash <path> [algorithm]",
                "ru": "Использование: get_file_hash <path> [algorithm]",
            },
        }

    path_str = parts[0]
    algorithm = parts[1].lower() if len(parts) > 1 else "md5"

    if algorithm not in ("md5", "sha1", "sha256", "sha512"):
        return {
            "success": False,
            "output": "",
            "error": f"Unsupported algorithm: {algorithm}. Supported: md5, sha1, sha256, sha512",
            "spoken": {
                "en": f"Unsupported algorithm: {algorithm}",
                "uk": f"Непідтримуваний алгоритм: {algorithm}",
                "ru": f"Неподдерживаемый алгоритм: {algorithm}",
            },
        }

    # Resolve path
    file_path = Path(path_str)
    if not file_path.is_absolute():
        file_path = workspace / file_path

    if not file_path.exists():
        return {
            "success": False,
            "output": "",
            "error": f"File not found: {path_str}",
            "spoken": {
                "en": f"File not found: {path_str}",
                "uk": f"Файл не знайдено: {path_str}",
                "ru": f"Файл не найден: {path_str}",
            },
        }

    if not file_path.is_file():
        return {
            "success": False,
            "output": "",
            "error": f"Not a file: {path_str}",
            "spoken": {
                "en": f"Not a file: {path_str}",
                "uk": f"Не є файлом: {path_str}",
                "ru": f"Не является файлом: {path_str}",
            },
        }

    try:
        hash_func = getattr(hashlib, algorithm)()
        buffer_size = 65536  # 64KB chunks
        file_size = file_path.stat().st_size

        with open(file_path, 'rb') as f:
            while chunk := f.read(buffer_size):
                hash_func.update(chunk)

        hash_result = {
            "path": str(file_path),
            "name": file_path.name,
            "size": file_size,
            "human_size": _human_readable_size(file_size),
            "algorithm": algorithm.upper(),
            "hash": hash_func.hexdigest(),
        }

        return {
            "success": True,
            "output": f"{algorithm.upper()}: {hash_result['hash']}",
            "hash": hash_result,
            "error": None,
            "spoken": {
                "en": f"{algorithm.upper()} hash: {hash_result['hash']}",
                "uk": f"Хеш {algorithm.upper()}: {hash_result['hash']}",
                "ru": f"Хеш {algorithm.upper()}: {hash_result['hash']}",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error calculating hash: {str(e)}",
                "uk": f"Помилка при розрахунку хешу: {str(e)}",
                "ru": f"Ошибка при расчете хеша: {str(e)}",
            },
        }


def _human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


# ============================================================================
# File Operation Tools
# ============================================================================

async def _execute_copy_file(args: str, settings: "Settings | None" = None) -> dict:
    """Copy a file or directory with progress reporting."""
    import shutil

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "source destination [overwrite=true]"
    parts = args.strip().split()
    if len(parts) < 2:
        return {
            "success": False,
            "output": "",
            "error": "Usage: copy_file <source> <destination> [overwrite]",
            "spoken": {
                "en": "Usage: copy_file <source> <destination> [overwrite]",
                "uk": "Використання: copy_file <source> <destination> [overwrite]",
                "ru": "Использование: copy_file <source> <destination> [overwrite]",
            },
        }

    source = parts[0]
    destination = parts[1]
    overwrite = len(parts) > 2 and parts[2].lower() in ("true", "1", "yes")

    # Resolve paths
    src_path = Path(source)
    if not src_path.is_absolute():
        src_path = workspace / source

    dest_path = Path(destination)
    if not dest_path.is_absolute():
        dest_path = workspace / destination

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

    if dest_path.exists() and not overwrite:
        return {
            "success": False,
            "output": "",
            "error": f"Destination already exists: {destination}. Use overwrite=true to replace.",
            "spoken": {
                "en": f"Destination already exists: {destination}. Use overwrite=true to replace.",
                "uk": f"Призначення вже існує: {destination}. Використайте overwrite=true, щоб замінити.",
                "ru": f"Назначение уже существует: {destination}. Используйте overwrite=true, чтобы заменить.",
            },
        }

    try:
        if src_path.is_file():
            # Copy file
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)
            result = {
                "source": str(src_path),
                "destination": str(dest_path),
                "type": "file",
                "size": src_path.stat().st_size,
            }
        else:
            # Copy directory
            shutil.copytree(src_path, dest_path, dirs_exist_ok=overwrite)
            result = {
                "source": str(src_path),
                "destination": str(dest_path),
                "type": "directory",
                "items": sum(1 for _ in dest_path.rglob('*') if _.is_file()),
            }

        return {
            "success": True,
            "output": f"Copied {result['type']}: {source} -> {destination}",
            "result": result,
            "error": None,
            "spoken": {
                "en": f"Copied {result['type']} to {destination}",
                "uk": f"Скопійовано {result['type']} до {destination}",
                "ru": f"Скопировано {result['type']} в {destination}",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error copying: {str(e)}",
                "uk": f"Помилка при копіюванні: {str(e)}",
                "ru": f"Ошибка при копировании: {str(e)}",
            },
        }


async def _execute_move_file(args: str, settings: "Settings | None" = None) -> dict:
    """Move/rename a file or directory."""
    import shutil

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "source destination [overwrite=true]"
    parts = args.strip().split()
    if len(parts) < 2:
        return {
            "success": False,
            "output": "",
            "error": "Usage: move_file <source> <destination> [overwrite]",
            "spoken": {
                "en": "Usage: move_file <source> <destination> [overwrite]",
                "uk": "Використання: move_file <source> <destination> [overwrite]",
                "ru": "Использование: move_file <source> <destination> [overwrite]",
            },
        }

    source = parts[0]
    destination = parts[1]
    overwrite = len(parts) > 2 and parts[2].lower() in ("true", "1", "yes")

    # Resolve paths
    src_path = Path(source)
    if not src_path.is_absolute():
        src_path = workspace / source

    dest_path = Path(destination)
    if not dest_path.is_absolute():
        dest_path = workspace / destination

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

    if dest_path.exists() and not overwrite:
        return {
            "success": False,
            "output": "",
            "error": f"Destination already exists: {destination}. Use overwrite=true to replace.",
            "spoken": {
                "en": f"Destination already exists: {destination}. Use overwrite=true to replace.",
                "uk": f"Призначення вже існує: {destination}. Використайте overwrite=true, щоб замінити.",
                "ru": f"Назначение уже существует: {destination}. Используйте overwrite=true, чтобы заменить.",
            },
        }

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dest_path))
        result = {
            "source": str(src_path),
            "destination": str(dest_path),
            "type": "directory" if src_path.is_dir() else "file",
        }

        return {
            "success": True,
            "output": f"Moved {result['type']}: {source} -> {destination}",
            "result": result,
            "error": None,
            "spoken": {
                "en": f"Moved {result['type']} to {destination}",
                "uk": f"Переміщено {result['type']} до {destination}",
                "ru": f"Перемещено {result['type']} в {destination}",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error moving: {str(e)}",
                "uk": f"Помилка при переміщенні: {str(e)}",
                "ru": f"Ошибка при перемещении: {str(e)}",
            },
        }


async def _execute_delete_file(args: str, settings: "Settings | None" = None) -> dict:
    """Delete a file or directory with confirmation."""
    import shutil

    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "path [recursive=true]"
    parts = args.strip().split()
    if not parts:
        return {
            "success": False,
            "output": "",
            "error": "Usage: delete_file <path> [recursive]",
            "spoken": {
                "en": "Usage: delete_file <path> [recursive]",
                "uk": "Використання: delete_file <path> [recursive]",
                "ru": "Использование: delete_file <path> [recursive]",
            },
        }

    path_str = parts[0]
    recursive = len(parts) > 1 and parts[1].lower() in ("true", "1", "yes")

    # Resolve path
    file_path = Path(path_str)
    if not file_path.is_absolute():
        file_path = workspace / path_str

    if not file_path.exists():
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

    # Check if it's a git repository or important file
    important_files = [".git", "README.md", "package.json", "requirements.txt"]
    if file_path.name in important_files or (file_path == workspace):
        return {
            "success": False,
            "output": "",
            "error": f"Cannot delete important file or directory: {path_str}",
            "spoken": {
                "en": f"Cannot delete important file or directory: {path_str}",
                "uk": f"Не можна видалити важливий файл чи директорію: {path_str}",
                "ru": f"Нельзя удалить важный файл или каталог: {path_str}",
            },
        }

    try:
        if file_path.is_file():
            file_path.unlink()
            result = {
                "path": str(file_path),
                "type": "file",
                "size": file_path.stat().st_size if file_path.exists() else 0,
            }
        else:
            if not recursive:
                # List contents first to warn user
                contents = list(file_path.iterdir())
                if contents:
                    return {
                        "success": False,
                        "output": "",
                        "error": f"Directory not empty: {path_str}. Use recursive=true to delete all contents.",
                        "spoken": {
                            "en": f"Directory not empty: {path_str}. Use recursive=true to delete all contents.",
                            "uk": f"Директорія не порожня: {path_str}. Використайте recursive=true, щоб видалити все вміст.",
                            "ru": f"Каталог не пуст: {path_str}. Используйте recursive=true, чтобы удалить все содержимое.",
                        },
                    }
            shutil.rmtree(file_path)
            result = {
                "path": str(file_path),
                "type": "directory",
                "items": sum(1 for _ in file_path.rglob('*')) if file_path.exists() else 0,
            }

        return {
            "success": True,
            "output": f"Deleted {result['type']}: {path_str}",
            "result": result,
            "error": None,
            "spoken": {
                "en": f"Deleted {result['type']}: {path_str}",
                "uk": f"Видалено {result['type']}: {path_str}",
                "ru": f"Удалено {result['type']}: {path_str}",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error deleting: {str(e)}",
                "uk": f"Помилка при видаленні: {str(e)}",
                "ru": f"Ошибка при удалении: {str(e)}",
            },
        }


async def _execute_create_directory(args: str, settings: "Settings | None" = None) -> dict:
    """Create a directory with parents."""
    workspace = settings.workspace_dir if settings else Path.cwd()

    # Parse arguments - format: "path"
    path_str = args.strip()
    if not path_str:
        return {
            "success": False,
            "output": "",
            "error": "Usage: create_directory <path>",
            "spoken": {
                "en": "Usage: create_directory <path>",
                "uk": "Використання: create_directory <path>",
                "ru": "Использование: create_directory <path>",
            },
        }

    # Resolve path
    dir_path = Path(path_str)
    if not dir_path.is_absolute():
        dir_path = workspace / path_str

    if dir_path.exists():
        if dir_path.is_dir():
            return {
                "success": False,
                "output": "",
                "error": f"Directory already exists: {path_str}",
                "spoken": {
                    "en": f"Directory already exists: {path_str}",
                    "uk": f"Директорія вже існує: {path_str}",
                    "ru": f"Каталог уже существует: {path_str}",
                },
            }
        else:
            return {
                "success": False,
                "output": "",
                "error": f"Path exists but is not a directory: {path_str}",
                "spoken": {
                    "en": f"Path exists but is not a directory: {path_str}",
                    "uk": f"Шлях існує, але це не директорія: {path_str}",
                    "ru": f"Путь существует, но это не каталог: {path_str}",
                },
            }

    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        result = {
            "path": str(dir_path),
            "parent": str(dir_path.parent),
            "depth": len(dir_path.parts) - len(workspace.parts),
        }

        return {
            "success": True,
            "output": f"Created directory: {path_str}",
            "result": result,
            "error": None,
            "spoken": {
                "en": f"Created directory: {path_str}",
                "uk": f"Створено директорію: {path_str}",
                "ru": f"Создан каталог: {path_str}",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "spoken": {
                "en": f"Error creating directory: {str(e)}",
                "uk": f"Помилка при створенні директорії: {str(e)}",
                "ru": f"Ошибка при создании каталога: {str(e)}",
            },
        }


# ============================================================================
# Archive & Batch Tools
# ============================================================================

async def _execute_create_archive(args: str, settings: "Settings | None" = None) -> dict:
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
    sources = parts[1:-2] if len(parts) > 2 and parts[-2] in ("tar.gz", "zip", "tar.bz2") else parts[1:]
    archive_format = parts[-2] if len(parts) > 2 and parts[-2] in ("tar.gz", "zip", "tar.bz2") else "tar.gz"
    compression = parts[-1] if len(parts) > 3 and parts[-1] in ("auto", "gzip", "bzip2", "none") else "auto"

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
        total_size = sum(f.stat().st_size for p in source_paths for f in p.rglob('*') if f.is_file())
        if settings and hasattr(settings, 'file_access_max_archive_size') and total_size > settings.file_access_max_archive_size:
            return {
                "success": False,
                "output": "",
                "error": f"Archive would exceed size limit of {settings.file_access_max_archive_size} bytes",
                "spoken": {
                    "en": f"Archive too large: would exceed {settings.file_access_max_archive_size / (1024*1024):.1f} MB limit",
                    "uk": f"Архів занадто великий: перевищує ліміт {settings.file_access_max_archive_size / (1024*1024):.1f} MB",
                    "ru": f"Архив слишком большой: превышает лимит {settings.file_access_max_archive_size / (1024*1024):.1f} MB",
                },
            }

        if archive_format == "zip":
            # Create ZIP archive
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED if compression == "auto" else
                               zipfile.ZIP_STORED if compression == "none" else
                               zipfile.ZIP_BZIP2 if compression == "bzip2" else zipfile.ZIP_DEFLATED) as zipf:
                for src in source_paths:
                    for file in src.rglob('*'):
                        if file.is_file():
                            arcname = file.relative_to(src.parent.parent)
                            zipf.write(file, arcname)
        else:
            # Create TAR archive
            if archive_format == "tar.gz":
                mode = 'w:gz' if compression == "auto" or compression == "gzip" else 'w'
            elif archive_format == "tar.bz2":
                mode = 'w:bz2' if compression == "auto" or compression == "bzip2" else 'w'
            else:
                mode = 'w'

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


async def _execute_extract_archive(args: str, settings: "Settings | None" = None) -> dict:
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
            with zipfile.ZipFile(archive_path, 'r') as zipf:
                for member in zipf.infolist():
                    if not member.is_dir():
                        file_size = member.file_size
                        total_size += file_size
                        extract_path = dest_path / member.filename if preserve_path else dest_path / Path(member.filename).name
                        zipf.extract(member, extract_path.parent)
                        extracted_files.append(str(extract_path))
                    else:
                        dir_path = dest_path / member.filename if preserve_path else dest_path
                        if not dir_path.exists():
                            dir_path.mkdir(parents=True, exist_ok=True)
                        extracted_dirs.append(str(dir_path))
        else:
            # Extract TAR
            dest_path.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive_path, 'r:*') as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        total_size += member.size
                        extract_path = dest_path / member.name if preserve_path else dest_path / Path(member.name).name
                        tar.extract(member, extract_path.parent)
                        extracted_files.append(str(extract_path))
                    elif member.isdir():
                        dir_path = dest_path / member.name if preserve_path else dest_path
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


async def _execute_batch_operation(args: str, settings: "Settings | None" = None) -> dict:
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
                results.append({
                    "path": str(file_path),
                    "name": file_path.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
            except Exception as e:
                errors.append(str(file_path) + ": " + str(e))
    else:
        # Perform actual operation
        for file_path in matched_files:
            try:
                if operation == "delete":
                    if dry_run:
                        results.append({"action": "would_delete", "path": str(file_path)})
                    else:
                        file_path.unlink()
                        results.append({"action": "deleted", "path": str(file_path)})
                        total_size += file_path.stat().st_size

                elif operation in ["copy_to", "move_to"]:
                    dest_path = Path(source_path.parent) / operation.replace("_to", "") / file_path.name
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    if dry_run:
                        results.append({
                            "action": operation.replace("_to", "would_" + operation),
                            "source": str(file_path),
                            "destination": str(dest_path),
                        })
                    else:
                        if operation == "copy_to":
                            shutil.copy2(file_path, dest_path)
                            results.append({"action": "copied", "source": str(file_path), "destination": str(dest_path)})
                        else:
                            shutil.move(file_path, dest_path)
                            results.append({"action": "moved", "source": str(file_path), "destination": str(dest_path)})
                        total_size += file_path.stat().st_size if file_path.exists() else 0

                elif operation == "archive":
                    if dry_run:
                        results.append({"action": "would_archive", "path": str(file_path)})
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
        "errors": errors[:10],     # Limit errors
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
        from .user_profile import get_profile_manager
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


async def _execute_list_favorites(args: str, settings: "Settings | None" = None) -> dict:
    """List favorite locations."""
    try:
        from .user_profile import get_profile_manager
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
            items.append({
                "path": fav["path"],
                "label": fav["label"],
                "access_count": fav.get("access_count", 0),
                "last_accessed": fav.get("last_accessed", "Never"),
            })

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


async def _execute_set_view_preference(args: str, settings: "Settings | None" = None) -> dict:
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
        from .user_profile import get_profile_manager
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
        from .user_profile import get_profile_manager
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
        from .agent_skills import get_skills_loader

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
        from .agent_skills import get_skills_loader

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
            name_part, instructions, context, settings, action_timeout_seconds=action_timeout
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
        from .agent_skills import get_skills_loader

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


def _extract_tool_calls_from_instructions(instructions: str, context: dict) -> list[tuple[str, str]]:
    """Extract tool calls from skill instructions.

    Phase 1 (MVP): Simple regex pattern matching with allowlist validation.
    Pattern: `bash(command)`, `read(path)`, `web_search(query)`, etc.
    Returns list of (tool_name, args_string) tuples for known tools only.

    Unknown tool names are skipped with debug-level logging to avoid
    false positives from prose (e.g., "use function(args)" in documentation).
    """
    # Get the set of enabled tools for validation
    from .tool_registry import get_enabled_tools
    enabled_tools = get_enabled_tools()

    # Regex: match "tool_name(args)" patterns
    pattern = r"(\w+)\(([^)]+)\)"
    matches = re.findall(pattern, instructions)

    result = []
    for tool_name, args_str in matches:
        # Skip if tool_name is not a known enabled tool
        if tool_name not in enabled_tools:
            logger.debug(f"Skill instruction references unknown tool: {tool_name}")
            continue

        # Substitute context vars: {variable_name} -> context['variable_name']
        substituted_args = args_str
        for key, value in context.items():
            substituted_args = substituted_args.replace(f"{{{key}}}", str(value))

        result.append((tool_name, substituted_args))

    return result


async def _execute_set_provider(args: str, settings: "Settings | None" = None) -> dict:
    """Switch the active LLM provider (openrouter or zai).

    Args:
        args: Provider name (openrouter or zai)
        settings: heare Settings

    Returns:
        dict with success status and message
    """
    try:
        provider = args.strip().lower()

        if provider not in ("openrouter", "zai"):
            return {
                "success": False,
                "output": "",
                "error": f"Invalid provider: {provider}. Must be 'openrouter' or 'zai'",
                "spoken": {"en": f"Invalid provider {provider}. Use openrouter or zai."},
            }

        if settings is None:
            from .config import load_settings

            settings = load_settings()

        settings.provider_file.parent.mkdir(parents=True, exist_ok=True)
        settings.provider_file.write_text(provider)

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
    from .capability_index import build_capability_index

    workspace = settings.workspace_dir if settings else Path.home() / ".heare" / "workspace"
    _capability_index_singleton = build_capability_index(settings, workspace)
    return _capability_index_singleton


def _entry_to_summary(entry) -> dict:
    return {
        "name": entry.name,
        "source": entry.source,
        "description": entry.description,
        "install_url": entry.install_url,
    }


async def _execute_discover_capability(args: str, settings: "Settings | None" = None) -> dict:
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

    from . import discovery

    started = _time.monotonic()
    index = _get_or_build_capability_index(settings)

    if prefer_remote:
        try:
            entries = await discovery.discover_capability_remote(intent, settings=settings)
            source = "remote"
        except Exception as e:  # noqa: BLE001
            logger.warning("[CAPABILITY DISCOVERY] remote failed: %s", e)
            entries = []
    else:
        local_entries = await discovery.discover_capability_local(intent, index, top_k=3)
        entries = local_entries
        source = "local"
        if not entries:
            try:
                entries = await discovery.discover_capability_remote(intent, settings=settings)
                source = "remote"
            except Exception as e:  # noqa: BLE001
                logger.warning("[CAPABILITY DISCOVERY] remote failed: %s", e)
                entries = []

    latency_ms = int((_time.monotonic() - started) * 1000)
    logger.info(
        "[CAPABILITY DISCOVERY] intent=%r results=%d source=%s latency_ms=%d",
        intent, len(entries), source, latency_ms,
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


async def _execute_install_skill_tool(args: str, settings: "Settings | None" = None) -> dict:
    """Args is JSON: {"slug": "...", "user_confirmed": true, "replace": false}."""
    import time as _time
    from . import installer as _installer

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
        return {"success": False, "error": "slug is required", "spoken": {"en": "Missing slug."}}

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
            slug, latency_ms, exc,
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
            slug, latency_ms, exc,
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
        slug, result.success, latency_ms,
    )
    return {
        "success": result.success,
        "output": result.message_en,
        "slug": result.slug,
        "requires_restart": result.requires_restart,
        "error_code": result.error_code,
        "spoken": {"en": result.message_en, "uk": result.message_uk},
    }


async def _execute_install_mcp_server_tool(args: str, settings: "Settings | None" = None) -> dict:
    """Args is JSON: {"slug": "...", "user_confirmed": true, "replace": false}."""
    import time as _time
    from . import installer as _installer

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
        return {"success": False, "error": "slug is required", "spoken": {"en": "Missing slug."}}

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
            slug, latency_ms, exc,
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
            slug, latency_ms, exc,
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
        slug, result.success, latency_ms,
    )
    return {
        "success": result.success,
        "output": result.message_en,
        "slug": result.slug,
        "requires_restart": result.requires_restart,
        "error_code": result.error_code,
        "spoken": {"en": result.message_en, "uk": result.message_uk},
    }


async def _execute_revoke_capability(args: str, settings: "Settings | None" = None) -> dict:
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
        return {"success": False, "error": "slug is required", "spoken": {"en": "Missing slug."}}

    skill_dir = Path.home() / ".heare" / "skills" / "_marketplace" / slug
    workspace = settings.workspace_dir if settings else Path.home() / ".heare" / "workspace"
    mcp_sidecar = Path(workspace) / ".mcp_install" / f"{slug}.json"

    removed_skill = False
    removed_mcp = False
    error_msg: str | None = None

    if skill_dir.exists():
        if not (skill_dir / ".install.json").exists():
            logger.info("[CAPABILITY REVOKE] slug=%s success=False reason=no_sidecar", slug)
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
            from .mcp_utils import read_mcp_servers, write_mcp_servers

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
        logger.info("[CAPABILITY REVOKE] slug=%s success=False reason=%s", slug, error_msg)
        return {
            "success": False,
            "output": "",
            "error": error_msg,
            "spoken": {"en": "Revoke failed."},
        }

    # Refresh in-process state so the change is visible mid-session.
    try:
        from .agent_skills import get_skills_loader

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


async def _execute_list_capabilities(args: str, settings: "Settings | None" = None) -> dict:
    """Args is JSON: {"category": "..."} or {}. Lists installed-via-discovery items."""
    try:
        payload = json.loads(args) if args else {}
    except json.JSONDecodeError:
        payload = {}
    category = str(payload.get("category", "")).strip().lower() or None

    items: list[dict] = []

    try:
        from .agent_skills import get_skills_loader

        loader = get_skills_loader(settings)
        for meta in loader.discover():
            if not getattr(meta, "installed_via_discovery", False):
                continue
            items.append({
                "name": meta.name,
                "source": "skill",
                "description": meta.description,
            })
    except Exception:  # noqa: BLE001
        logger.warning("list_capabilities: skills discovery failed", exc_info=True)

    workspace = settings.workspace_dir if settings else Path.home() / ".heare" / "workspace"
    sidecar_dir = Path(workspace) / ".mcp_install"
    if sidecar_dir.is_dir():
        try:
            from .mcp_utils import read_mcp_servers

            servers = read_mcp_servers(Path(workspace))
            for sidecar in sidecar_dir.glob("*.json"):
                slug = sidecar.stem
                desc = ""
                entry = servers.get(slug)
                if isinstance(entry, dict):
                    desc = entry.get("description", "") or ""
                items.append({
                    "name": slug,
                    "source": "mcp",
                    "description": desc or f"MCP server: {slug}",
                })
        except Exception:  # noqa: BLE001
            logger.warning("list_capabilities: mcp scan failed", exc_info=True)

    if category:
        items = [i for i in items if i["source"] == category]

    count = len(items)
    if count > 5:
        names_short = ", ".join(i["name"] for i in items[:3])
        return {
            "success": True,
            "output": f"{count} installed: {names_short} ...",
            "summary": True,
            "count": count,
            "items": items,
            "spoken": {
                "en": f"I have {count} installed tools. Want me to name them all, or filter by category?",
                "uk": f"У мене {count} встановлених інструментів. Назвати всі, чи відфільтрувати за категорією?",
            },
        }

    if count == 0:
        return {
            "success": True,
            "output": "No installed capabilities.",
            "summary": False,
            "count": 0,
            "items": [],
            "spoken": {
                "en": "Nothing installed via discovery yet.",
                "uk": "Поки нічого не встановлено через відкриття.",
            },
        }

    lines = [f"- {i['name']} ({i['source']}): {i['description']}" for i in items]
    return {
        "success": True,
        "output": "\n".join(lines),
        "summary": False,
        "count": count,
        "items": items,
        "spoken": {
            "en": f"I have {count} installed: {', '.join(i['name'] for i in items)}.",
            "uk": f"У мене {count} встановлених: {', '.join(i['name'] for i in items)}.",
        },
    }
