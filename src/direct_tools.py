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
import logging
import os
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
    elif tool == "delete_profile":
        return await _execute_delete_profile(args, settings)
    else:
        return {
            "success": False,
            "output": "",
            "error": f"Unknown direct tool: {tool}",
        }


async def _execute_bash(args: str, settings: "Settings | None" = None) -> dict:
    """Execute a bash command."""
    import subprocess

    workspace = settings.workspace_dir if settings else Path.cwd()

    try:
        proc = await asyncio.create_subprocess_shell(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workspace,
        )
        stdout, stderr = await proc.communicate()

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
    """Search using Serper.dev (Google API)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://google.serper.dev/search",
                params={"q": query},
                headers={"X-API-KEY": api_key},
            )
            resp.raise_for_status()
            data = resp.json()

            results: list[str] = []
            top_url: str | None = None
            for item in data.get("organic", [])[:5]:
                title = item.get("title", "")
                url = item.get("link", "")
                snippet = (item.get("snippet") or "").strip()
                if title and url:
                    if top_url is None:
                        top_url = url
                    if snippet:
                        results.append(f"{title}\n{snippet}\n{url}")
                    else:
                        results.append(f"{title}\n{url}")

            # Answer box if available
            if "knowledgeGraph" in data:
                kg = data["knowledgeGraph"]
                kg_title = kg.get("title", "")
                kg_desc = kg.get("description", "")
                if kg_title and kg_desc:
                    results.insert(0, f"📚 {kg_title}: {kg_desc}")

            n = len(results)
            if n >= 1:
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
            output = "\n\n".join(results) if results else "No results found"
            output = await _maybe_append_top_page(output, top_url, settings)
            return {
                "success": True,
                "output": output,
                "error": None,
                "spoken": spoken,
            }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "output": "",
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
    """Search using DuckDuckGo HTML scraping (fallback, no API key needed)."""
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

            results: list[str] = []
            top_url: str | None = None
            link_pattern = r'<a class="result__a" href="([^"]*)">([^<]*)</a>'
            link_iter = list(re.finditer(link_pattern, text))
            tag_strip = re.compile(r"<[^>]+>")
            ws_collapse = re.compile(r"\s+")

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
                if snippet:
                    results.append(f"{title}\n{snippet}\n{url}")
                else:
                    results.append(f"{title}\n{url}")

            n = len(results)
            if n >= 1:
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
            output = "\n\n".join(results) if results else "No results found"
            output = await _maybe_append_top_page(output, top_url, settings)
            return {
                "success": True,
                "output": output,
                "error": None,
                "spoken": spoken,
            }
    except Exception as e:
        return {
            "success": False,
            "output": "",
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
