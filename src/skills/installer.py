"""Installer for skills + MCP servers sourced from discovery (US-006).

Security-critical. All installs require a hard consent gate (configured
passphrase) and an explicit ``user_confirmed=True`` flag from the LLM tool
call before any filesystem mutation occurs.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import shutil
import tarfile
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import httpx

from src.agent.tools.capability_index import CapabilityIndex, IndexEntry
from src.skills.marketplace import (
    DEFAULT_HOSTNAME_ALLOWLIST,
    _validate_url,
    _verify_checksum,
)
from src.skills.mcp_utils import read_mcp_servers, write_mcp_servers

logger = logging.getLogger("heare.installer")


# Hard wall-clock ceiling for any single download attempt. Higher than the
# httpx per-op timeout so the per-op fires first on a clean read stall, but
# bounds the worst case (slow trickle below the read window) to a value that
# won't freeze the bot for minutes.
_DOWNLOAD_OVERALL_TIMEOUT_S = 30.0

# Max body size accepted from a tarball download. Codeload tarballs for
# whole repos can be tens of MB; we still want a ceiling so a runaway
# stream cannot exhaust memory or the event loop.
_DOWNLOAD_MAX_BYTES = 64 * 1024 * 1024


MSG_NO_CONSENT_EN = "Speaker ID or passphrase is required to install tools. Please configure one in settings."
MSG_NO_CONSENT_UK = "Для встановлення інструментів потрібен Speaker ID або кодова фраза. Налаштуй у параметрах."

MSG_INSTALLED_SKILL_EN = "Installed {slug}. You can use it now."
MSG_INSTALLED_SKILL_UK = "Встановив {slug}. Можеш користуватися."

MSG_CREATED_SKILL_EN = "Created skill {slug}. You can run it now."
MSG_CREATED_SKILL_UK = "Створив навичку {slug}. Можеш користуватися."

MSG_INSTALLED_MCP_EN = "Installed MCP server {slug}. Restart required to use it."
MSG_INSTALLED_MCP_UK = (
    "Встановив MCP сервер {slug}. Потрібен перезапуск, щоб використати."
)

MSG_SLUG_COLLISION_EN = (
    "A skill named {slug} is already installed. Say 'replace' to overwrite."
)
MSG_SLUG_COLLISION_UK = (
    "Інструмент {slug} вже встановлено. Скажи 'замінити', щоб перезаписати."
)

MSG_CHECKSUM_FAILED_EN = "Download failed integrity check. Install aborted."
MSG_CHECKSUM_FAILED_UK = "Завантаження не пройшло перевірку. Встановлення скасовано."


@dataclass(frozen=True)
class InstallResult:
    success: bool
    slug: str
    message_en: str
    message_uk: str
    requires_restart: bool
    error_code: str | None = None


class InstallRefused(Exception):
    """Raised when consent or policy refuses an install (no filesystem effect)."""


class InstallFailed(Exception):
    """Raised when install began but could not complete (best-effort rollback ran)."""


def _consent_available(settings) -> tuple[bool, str]:
    phrase = getattr(settings, "confirmation_passphrase", None)
    if isinstance(phrase, str) and phrase.strip():
        return True, "passphrase"
    return False, "no_consent_method"


def _check_consent(settings, user_confirmed: bool) -> None:
    ok, reason = _consent_available(settings)
    if not ok:
        raise InstallRefused(reason)
    if not user_confirmed:
        raise InstallRefused("user_not_confirmed")


def _slug_from_entry(entry: IndexEntry) -> str:
    return entry.name


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _source_marketplace(install_url: str | None) -> str:
    if not install_url:
        return ""
    try:
        host = urllib.parse.urlparse(install_url).hostname or ""
    except ValueError:
        return ""
    return host.lower()


async def _download(
    url: str,
    *,
    timeout: float = 2.5,
    overall_timeout: float = _DOWNLOAD_OVERALL_TIMEOUT_S,
    max_bytes: int = _DOWNLOAD_MAX_BYTES,
) -> bytes:
    """GET ``url`` with a hard wall-clock ceiling and a body-size cap.

    ``timeout`` is the per-op httpx timeout (connect/read/write/pool).
    ``overall_timeout`` is the total wall time after which the request is
    aborted, regardless of per-chunk progress — guards against slow-trickle
    streams that the per-op read timeout cannot detect.

    Every exit path logs a single ``[INSTALL] ←`` line so a hung install
    can never disappear silently.
    """
    started = time.monotonic()
    logger.info(
        "[INSTALL] → GET %s (timeout=%.1fs, overall=%.1fs, max=%dMB)",
        url,
        timeout,
        overall_timeout,
        max_bytes // (1024 * 1024),
    )

    async def _do_get() -> bytes:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise InstallFailed(
                            f"download_too_large:{len(buf)}>{max_bytes}"
                        )
                logger.info(
                    "[INSTALL] ← status=%d bytes=%d latency_ms=%d",
                    resp.status_code,
                    len(buf),
                    int((time.monotonic() - started) * 1000),
                )
                return bytes(buf)

    try:
        return await asyncio.wait_for(_do_get(), timeout=overall_timeout)
    except asyncio.TimeoutError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "[INSTALL] ← TIMEOUT url=%s overall=%.1fs latency_ms=%d",
            url,
            overall_timeout,
            latency_ms,
        )
        raise InstallFailed("download_timeout") from exc
    except asyncio.CancelledError:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "[INSTALL] ← CANCELLED url=%s latency_ms=%d",
            url,
            latency_ms,
        )
        raise
    except httpx.HTTPError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "[INSTALL] ← HTTPError url=%s err=%s latency_ms=%d",
            url,
            exc,
            latency_ms,
        )
        raise
    except InstallFailed as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "[INSTALL] ← FAILED url=%s err=%s latency_ms=%d",
            url,
            exc,
            latency_ms,
        )
        raise
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.exception(
            "[INSTALL] ← UNEXPECTED url=%s latency_ms=%d",
            url,
            latency_ms,
        )
        raise InstallFailed("download_unexpected") from exc


def _check_member_safety(member: tarfile.TarInfo) -> None:
    """Raise InstallFailed for members that could escape the destination directory.

    Path-traversal in member name is fatal (real attack). Unsafe symlink/hardlink
    targets are NOT raised here — the extraction loop skips those members instead,
    so a single weird link inside an otherwise-valid repo doesn't abort the install.
    """
    if member.name.startswith("/") or ".." in Path(member.name).parts:
        raise InstallFailed("unsafe_archive_path")


def _is_unsafe_link(member: tarfile.TarInfo) -> bool:
    if not (member.issym() or member.islnk()):
        return False
    target = Path(member.linkname)
    return target.is_absolute() or ".." in target.parts


def _parse_github_tree_url(url: str) -> tuple[str, str] | None:
    """Parse a GitHub tree URL into (tarball_url, subpath).

    Accepts ``https://github.com/{owner}/{repo}/tree/{branch}/{subpath...}``
    and returns the codeload tarball URL plus subpath inside the archive.
    Returns None for any other URL shape.
    """
    parts_info = _parse_github_tree_parts(url)
    if parts_info is None:
        return None
    owner, repo, branch, subpath = parts_info
    tarball_url = (
        f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{branch}"
    )
    return tarball_url, subpath


def _parse_github_tree_parts(url: str) -> tuple[str, str, str, str] | None:
    """Return (owner, repo, branch, subpath) or None for non-tree URLs."""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname != "github.com":
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 4 or parts[2] != "tree":
        return None
    owner, repo, _, branch, *rest = parts
    if not owner or not repo or not branch:
        return None
    return owner, repo, branch, "/".join(rest).strip("/")


def _raw_url_for(
    owner: str, repo: str, branch: str, subpath: str, filename: str
) -> str:
    sub = (subpath + "/") if subpath else ""
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{sub}{filename}"


_MAX_SKILL_MD_BYTES = 1 << 20

_ASSET_REFERENCE_PATTERNS = (
    re.compile(r"\$\{CLAUDE_SKILL_DIR\}", re.IGNORECASE),
    re.compile(
        r"!\s*`[^`]*\b(?:scripts|assets|reference|examples|templates)/", re.IGNORECASE
    ),
    re.compile(r"\]\((?:scripts|assets|reference|examples|templates)/", re.IGNORECASE),
    re.compile(
        r"\[(?:scripts|assets|reference|examples|templates)/[^\]]*\]\([^)]*\)",
        re.IGNORECASE,
    ),
)


def _skill_md_references_local_assets(text: str) -> bool:
    """Detect whether a SKILL.md body references sibling files that the fast path
    cannot fetch (scripts/, assets/, etc.). Conservative — false positives just
    trigger a tarball fallback, not a wrong install."""
    return any(p.search(text) for p in _ASSET_REFERENCE_PATTERNS)


_FAST_PATH_ALLOWED_SIBLINGS = frozenset(
    {
        "SKILL.md",
        "README.md",
        "README",
        "LICENSE",
        "LICENSE.md",
        ".gitignore",
    }
)


async def _list_github_dir(
    owner: str,
    repo: str,
    branch: str,
    subpath: str,
    *,
    timeout: float = 4.0,
) -> list[dict] | None:
    """Return GitHub Contents API listing for the skill directory, or None on failure."""
    api_url = (
        f"https://api.github.com/repos/{owner}/{repo}/contents/{subpath}?ref={branch}"
    )
    started = time.monotonic()
    logger.info("[INSTALL] → GET %s (dir listing)", api_url)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                api_url, headers={"Accept": "application/vnd.github+json"}
            )
    except httpx.HTTPError as exc:
        logger.warning("[INSTALL] dir listing failed: %s", exc)
        return None
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[INSTALL] ← status=%d latency_ms=%d",
        resp.status_code,
        latency_ms,
    )
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, list) else None


async def _try_raw_fast_path(
    install_url: str,
    *,
    timeout: float = 5.0,
) -> tuple[str, list[tuple[str, bytes]]] | None:
    """Try to install a skill by raw-fetching just SKILL.md when the skill dir
    contains no sibling files that the LLM would need.

    Returns ``(skill_md_text, [])`` on success, or ``None`` to signal the caller
    should fall back to the tarball path. We list the skill dir via the GitHub
    Contents API first — if anything beyond SKILL.md/README.md/LICENSE exists
    (scripts, executors, sub-directories), we skip the fast path so the user
    gets the full payload via tarball.
    """
    parts = _parse_github_tree_parts(install_url)
    if parts is None:
        return None
    owner, repo, branch, subpath = parts

    listing = await _list_github_dir(owner, repo, branch, subpath)
    if listing is None:
        logger.info("[INSTALL] dir listing unavailable — falling back to tarball")
        return None
    has_skill_md = any(
        item.get("name") == "SKILL.md" and item.get("type") == "file"
        for item in listing
    )
    if not has_skill_md:
        logger.info("[INSTALL] no SKILL.md at %s — tarball will fail too", subpath)
        return None
    extras = [
        item for item in listing if item.get("name") not in _FAST_PATH_ALLOWED_SIBLINGS
    ]
    if extras:
        names = ", ".join(item.get("name", "?") for item in extras[:5])
        logger.info(
            "[INSTALL] %d sibling file(s) beyond SKILL.md (%s) — using tarball",
            len(extras),
            names,
        )
        return None

    skill_md_url = _raw_url_for(owner, repo, branch, subpath, "SKILL.md")
    started = time.monotonic()
    logger.info("[INSTALL] → GET %s (raw fast path)", skill_md_url)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(skill_md_url)
    except httpx.HTTPError as exc:
        logger.warning("[INSTALL] raw fast path failed: %s", exc)
        return None
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[INSTALL] ← status=%d bytes=%d latency_ms=%d",
        resp.status_code,
        len(resp.content),
        latency_ms,
    )
    if resp.status_code != 200:
        return None
    if len(resp.content) > _MAX_SKILL_MD_BYTES:
        logger.warning(
            "[INSTALL] raw SKILL.md exceeds %d bytes; falling back to tarball",
            _MAX_SKILL_MD_BYTES,
        )
        return None
    return resp.text, []


def _extract_tarball(content: bytes, dest: Path, subpath: str = "") -> None:
    """Extract the tarball into dest. Strips a single top-level directory if present.

    When ``subpath`` is provided, only members under ``{top}/{subpath}/`` are
    extracted, with that prefix stripped — used for GitHub repo tarballs that
    contain the skill in a subdirectory.

    Raises tarfile.TarError on bad archives.
    Raises InstallFailed on path-traversal or unsafe symlinks.
    """
    dest.mkdir(parents=True, exist_ok=True)
    sub = subpath.strip("/")
    with tarfile.open(fileobj=BytesIO(content), mode="r:*") as tf:
        members = tf.getmembers()
        top_levels = {
            m.name.split("/", 1)[0]
            for m in members
            if m.name and not m.name.startswith("/")
        }
        strip_prefix = ""
        if len(top_levels) == 1:
            sole = next(iter(top_levels))
            if any(m.name != sole and m.name.startswith(sole + "/") for m in members):
                strip_prefix = sole + "/"
        full_prefix = strip_prefix + (sub + "/" if sub else "")
        any_match = False
        for m in members:
            if full_prefix:
                if not m.name.startswith(full_prefix):
                    continue
                target_name = m.name[len(full_prefix) :]
            else:
                target_name = m.name
            if not target_name:
                continue
            _check_member_safety(m)
            if _is_unsafe_link(m):
                logger.warning(
                    "[INSTALL] skipping unsafe link %s → %s",
                    m.name,
                    m.linkname,
                )
                continue
            any_match = True
            target_path = dest / target_name
            if m.isdir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            f = tf.extractfile(m)
            if f is None:
                continue
            target_path.write_bytes(f.read())
        if sub and not any_match:
            raise InstallFailed("subpath_not_found")


def _write_sidecar(
    dest: Path,
    entry: IndexEntry,
    *,
    user_confirmed: bool,
    sidecar_name: str = ".install.json",
) -> None:
    sidecar = {
        "source_url": entry.install_url or "",
        "install_url": entry.install_url or "",
        "version": "",
        "install_timestamp": _now_iso(),
        "signature_verified": False,
        "user_confirmed": user_confirmed,
        "source_marketplace": _source_marketplace(entry.install_url),
        "checksum_sha256": entry.checksum or "",
    }
    dest.write_text(json.dumps(sidecar, indent=2))


def _marketplace_root() -> Path:
    return Path.home() / ".heare" / "skills" / "_marketplace"


async def install_skill(
    entry: IndexEntry,
    *,
    settings,
    capability_index: CapabilityIndex | None = None,
    user_confirmed: bool = False,
    replace: bool = False,
) -> InstallResult:
    slug = _slug_from_entry(entry)
    started = time.monotonic()

    try:
        _check_consent(settings, user_confirmed)
    except InstallRefused as exc:
        if str(exc) == "no_consent_method":
            return InstallResult(
                success=False,
                slug=slug,
                message_en=MSG_NO_CONSENT_EN,
                message_uk=MSG_NO_CONSENT_UK,
                requires_restart=False,
                error_code="no_consent_method",
            )
        raise

    target = _marketplace_root() / slug
    if target.exists():
        if not replace:
            raise InstallRefused("slug_collision")
        shutil.rmtree(target)

    install_url = entry.install_url or ""
    if not install_url or not _validate_url(install_url, DEFAULT_HOSTNAME_ALLOWLIST):
        raise InstallFailed("bad_install_url")

    if (
        getattr(settings, "installation_signature_required", False)
        and not entry.checksum
    ):
        raise InstallRefused("signature_required")

    target.parent.mkdir(parents=True, exist_ok=True)

    fast_path = None
    if entry.checksum is None:
        fast_path = await _try_raw_fast_path(install_url)

    if fast_path is not None:
        skill_md_text, _extras = fast_path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / slug
            tmp_path.mkdir(parents=True, exist_ok=True)
            (tmp_path / "SKILL.md").write_text(skill_md_text)
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(tmp_path), str(target))
        logger.info("[INSTALL] fast path used for %s", slug)
    else:
        tree_info = _parse_github_tree_url(install_url)
        if tree_info is not None:
            download_url, subpath = tree_info
            if not _validate_url(download_url, DEFAULT_HOSTNAME_ALLOWLIST):
                raise InstallFailed("bad_install_url")
        else:
            download_url, subpath = install_url, ""

        try:
            content = await _download(download_url, timeout=15.0)
        except InstallFailed:
            # _download already logged the specific failure (timeout, too
            # large, unexpected). Propagate the original reason code so the
            # tool result tells the LLM why we stopped.
            raise
        except httpx.HTTPError as exc:
            logger.warning("download failed for %s: %s", download_url, exc)
            raise InstallFailed("download_failed") from exc

        if entry.checksum and not _verify_checksum(content, entry.checksum):
            raise InstallFailed("checksum_failed")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / slug
            extract_started = time.monotonic()
            try:
                # Tarball extraction is CPU/IO-bound and synchronous; run
                # it off the event loop so TTS, transcription, and other
                # async stages keep flowing while a multi-MB archive is
                # being unpacked.
                await asyncio.to_thread(_extract_tarball, content, tmp_path, subpath)
            except tarfile.TarError as exc:
                raise InstallFailed("invalid_archive") from exc
            logger.info(
                "[INSTALL] extracted %d bytes in %dms",
                len(content),
                int((time.monotonic() - extract_started) * 1000),
            )

            if not (tmp_path / "SKILL.md").exists():
                raise InstallFailed("invalid_archive")

            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(tmp_path), str(target))

    _write_sidecar(target / ".install.json", entry, user_confirmed=user_confirmed)

    try:
        from src.skills.agent_skills import get_skills_loader

        loader = get_skills_loader(settings)
        loader.invalidate()
    except Exception:
        logger.warning("SkillsLoader.invalidate() failed", exc_info=True)

    if capability_index is not None:
        try:
            capability_index.rebuild()
        except Exception:
            logger.warning("capability_index.rebuild() failed", exc_info=True)

    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[CAPABILITY INSTALL] slug=%s source=skill success=True latency_ms=%d",
        slug,
        latency_ms,
    )

    return InstallResult(
        success=True,
        slug=slug,
        message_en=MSG_INSTALLED_SKILL_EN.format(slug=slug),
        message_uk=MSG_INSTALLED_SKILL_UK.format(slug=slug),
        requires_restart=False,
    )


# Skill name validation mirrors agent_skills.SkillsLoader._parse_skill_metadata
# (lowercase-hyphenated). Keeping the regex local avoids a circular import.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_SKILL_NAME_MAX = 64
_SKILL_DESC_MAX = 200
_SKILL_BODY_MAX = _MAX_SKILL_MD_BYTES  # 1 MB — enforced by the loader too


def _author_skill_md(name: str, description: str, body: str) -> str:
    """Render a SKILL.md from user-supplied parts.

    The frontmatter must satisfy the loader's parser exactly: ``name`` and
    ``description`` keys, dash-fenced. We trim the body and ensure a single
    trailing newline so the file is byte-stable across re-creates.
    """
    safe_desc = description.replace("\n", " ").strip()
    safe_body = body.strip()
    return f"---\nname: {name}\ndescription: {safe_desc}\n---\n{safe_body}\n"


async def create_skill(
    *,
    name: str,
    description: str,
    body: str,
    settings,
    capability_index: CapabilityIndex | None = None,
    user_confirmed: bool = False,
    replace: bool = False,
) -> InstallResult:
    """Author a user-supplied skill at ``~/.heare/skills/<name>/``.

    Mirrors ``install_skill`` for consent gating, slug-collision handling,
    loader/index refresh, and result shape — but the body comes from the
    conversation rather than the marketplace, so there is no download,
    no checksum, and no signature requirement.

    Raises:
        InstallRefused: when consent is missing, ``user_confirmed`` is
            False, or the slug already exists and ``replace`` is False.
        InstallFailed: when the input fails validation (bad name,
            missing description, oversize body).
    """
    started = time.monotonic()
    slug = (name or "").strip()

    # Validation goes BEFORE consent so a malformed call doesn't waste a
    # confirmation event on the user.
    if not slug:
        raise InstallFailed("name_required")
    if len(slug) > _SKILL_NAME_MAX:
        raise InstallFailed("name_too_long")
    if not _SKILL_NAME_RE.match(slug):
        raise InstallFailed("name_invalid_format")

    desc = (description or "").strip()
    if not desc:
        raise InstallFailed("description_required")
    if len(desc) > _SKILL_DESC_MAX:
        raise InstallFailed("description_too_long")

    body_text = body or ""
    if not body_text.strip():
        raise InstallFailed("body_required")
    if len(body_text.encode("utf-8")) > _SKILL_BODY_MAX:
        raise InstallFailed("body_too_large")

    try:
        _check_consent(settings, user_confirmed)
    except InstallRefused as exc:
        if str(exc) == "no_consent_method":
            return InstallResult(
                success=False,
                slug=slug,
                message_en=MSG_NO_CONSENT_EN,
                message_uk=MSG_NO_CONSENT_UK,
                requires_restart=False,
                error_code="no_consent_method",
            )
        raise

    # User-authored skills live at ~/.heare/skills/<slug>/, NOT under
    # _marketplace/, so the loader treats them as user-authored (no
    # ``installed_via_discovery`` flag) and ``revoke_capability`` won't
    # delete them — see SkillsLoader and revoke_capability.
    target = Path.home() / ".heare" / "skills" / slug
    if target.exists():
        if not replace:
            raise InstallRefused("slug_collision")
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    skill_md = _author_skill_md(slug, desc, body_text)

    # Atomic write: build in a tempdir, then move into place — same pattern
    # ``install_skill`` uses so a failure mid-write cannot leave a partially
    # created skill folder on disk.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / slug
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "SKILL.md").write_text(skill_md, encoding="utf-8")
        shutil.move(str(tmp_path), str(target))

    # Sidecar marks provenance. ``source_marketplace`` deliberately uses
    # the literal "user_authored" so test_user_authored_skill_no_sidecar_no_flag
    # and downstream telemetry can distinguish authored from installed.
    sidecar = {
        "source_url": "",
        "install_url": "",
        "version": "",
        "install_timestamp": _now_iso(),
        "signature_verified": False,
        "user_confirmed": user_confirmed,
        "source_marketplace": "user_authored",
        "checksum_sha256": "",
    }
    (target / ".install.json").write_text(json.dumps(sidecar, indent=2))

    # Refresh runtime caches so the new skill is callable on the next turn.
    try:
        from src.skills.agent_skills import get_skills_loader

        get_skills_loader(settings).invalidate()
    except Exception:
        logger.warning("SkillsLoader.invalidate() failed", exc_info=True)

    if capability_index is not None:
        try:
            capability_index.rebuild()
        except Exception:
            logger.warning("capability_index.rebuild() failed", exc_info=True)

    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[CAPABILITY CREATE] slug=%s source=skill success=True latency_ms=%d",
        slug,
        latency_ms,
    )

    return InstallResult(
        success=True,
        slug=slug,
        message_en=MSG_CREATED_SKILL_EN.format(slug=slug),
        message_uk=MSG_CREATED_SKILL_UK.format(slug=slug),
        requires_restart=False,
    )


async def install_mcp_server(
    entry: IndexEntry,
    *,
    settings,
    capability_index: CapabilityIndex | None = None,
    user_confirmed: bool = False,
    replace: bool = False,
) -> InstallResult:
    slug = _slug_from_entry(entry)
    started = time.monotonic()

    try:
        _check_consent(settings, user_confirmed)
    except InstallRefused as exc:
        if str(exc) == "no_consent_method":
            return InstallResult(
                success=False,
                slug=slug,
                message_en=MSG_NO_CONSENT_EN,
                message_uk=MSG_NO_CONSENT_UK,
                requires_restart=True,
                error_code="no_consent_method",
            )
        raise

    mcp_dir = Path(getattr(settings, "mcp_dir"))
    servers = read_mcp_servers(mcp_dir)
    if slug in servers and not replace:
        raise InstallRefused("slug_collision")

    install_url = entry.install_url or ""
    if install_url and not _validate_url(install_url, DEFAULT_HOSTNAME_ALLOWLIST):
        raise InstallFailed("bad_install_url")

    if (
        getattr(settings, "installation_signature_required", False)
        and not entry.checksum
    ):
        raise InstallRefused("signature_required")

    if entry.launch is None:
        # An MCP entry without launch info would write a useless bookmark — the
        # SDK has no command to spawn, so the server never comes up. Refuse
        # rather than persist a broken `.mcp.json`.
        raise InstallFailed("launch_required")

    server_entry: dict = {"description": entry.description}
    if install_url:
        server_entry["install_url"] = install_url
    server_entry["command"] = entry.launch["command"]
    server_entry["args"] = list(entry.launch.get("args", []))
    if entry.launch.get("env"):
        server_entry["env"] = dict(entry.launch["env"])
    servers[slug] = server_entry
    write_mcp_servers(mcp_dir, servers)

    sidecar_dir = mcp_dir / ".mcp_install"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    _write_sidecar(sidecar_dir / f"{slug}.json", entry, user_confirmed=user_confirmed)

    if capability_index is not None:
        try:
            capability_index.rebuild()
        except Exception:
            logger.warning("capability_index.rebuild() failed", exc_info=True)

    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[CAPABILITY INSTALL] slug=%s source=mcp success=True latency_ms=%d",
        slug,
        latency_ms,
    )

    return InstallResult(
        success=True,
        slug=slug,
        message_en=MSG_INSTALLED_MCP_EN.format(slug=slug),
        message_uk=MSG_INSTALLED_MCP_UK.format(slug=slug),
        requires_restart=True,
    )


__all__ = [
    "InstallResult",
    "InstallRefused",
    "InstallFailed",
    "install_skill",
    "install_mcp_server",
    "MSG_NO_CONSENT_EN",
    "MSG_NO_CONSENT_UK",
    "MSG_INSTALLED_SKILL_EN",
    "MSG_INSTALLED_SKILL_UK",
    "MSG_INSTALLED_MCP_EN",
    "MSG_INSTALLED_MCP_UK",
    "MSG_SLUG_COLLISION_EN",
    "MSG_SLUG_COLLISION_UK",
    "MSG_CHECKSUM_FAILED_EN",
    "MSG_CHECKSUM_FAILED_UK",
]
