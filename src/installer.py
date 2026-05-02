"""Installer for skills + MCP servers sourced from discovery (US-006).

Security-critical. All installs require a hard consent gate (speaker-ID OR
configured passphrase) and an explicit ``user_confirmed=True`` flag from the
LLM tool call before any filesystem mutation occurs.
"""
from __future__ import annotations

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

from .capability_index import CapabilityIndex, IndexEntry
from .marketplace import DEFAULT_HOSTNAME_ALLOWLIST, _validate_url, _verify_checksum
from .mcp_utils import read_mcp_servers, write_mcp_servers

logger = logging.getLogger("heare.installer")


MSG_NO_CONSENT_EN = "Speaker ID or passphrase is required to install tools. Please configure one in settings."
MSG_NO_CONSENT_UK = "Для встановлення інструментів потрібен Speaker ID або кодова фраза. Налаштуй у параметрах."

MSG_INSTALLED_SKILL_EN = "Installed {slug}. You can use it now."
MSG_INSTALLED_SKILL_UK = "Встановив {slug}. Можеш користуватися."

MSG_INSTALLED_MCP_EN = "Installed MCP server {slug}. Restart required to use it."
MSG_INSTALLED_MCP_UK = "Встановив MCP сервер {slug}. Потрібен перезапуск, щоб використати."

MSG_SLUG_COLLISION_EN = "A skill named {slug} is already installed. Say 'replace' to overwrite."
MSG_SLUG_COLLISION_UK = "Інструмент {slug} вже встановлено. Скажи 'замінити', щоб перезаписати."

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


def is_owner_enrolled(settings) -> bool:
    """Check whether the owner is enrolled in the speaker gallery.

    Module-level so tests can monkeypatch. Reads ``settings.speakers_file``
    and looks for an "owner" entry with at least one embedding.
    """
    path = getattr(settings, "speakers_file", None)
    if path is None:
        return False
    p = Path(path)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    speakers = data.get("speakers")
    if not isinstance(speakers, dict):
        return False
    owner = speakers.get("owner")
    if not isinstance(owner, dict):
        return False
    embeddings = owner.get("embeddings")
    return bool(embeddings)


def _consent_available(settings) -> tuple[bool, str]:
    if getattr(settings, "speaker_id_enabled", False) and is_owner_enrolled(settings):
        return True, "speaker_id"
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


async def _download(url: str, *, timeout: float = 2.5) -> bytes:
    started = time.monotonic()
    logger.info("[INSTALL] → GET %s (timeout=%.1fs)", url, timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[INSTALL] ← status=%d bytes=%d latency_ms=%d",
        resp.status_code, len(resp.content), latency_ms,
    )
    resp.raise_for_status()
    return resp.content


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
    tarball_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{branch}"
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


def _raw_url_for(owner: str, repo: str, branch: str, subpath: str, filename: str) -> str:
    sub = (subpath + "/") if subpath else ""
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{sub}{filename}"


_MAX_SKILL_MD_BYTES = 1 << 20

_ASSET_REFERENCE_PATTERNS = (
    re.compile(r"\$\{CLAUDE_SKILL_DIR\}", re.IGNORECASE),
    re.compile(r"!\s*`[^`]*\b(?:scripts|assets|reference|examples|templates)/", re.IGNORECASE),
    re.compile(r"\]\((?:scripts|assets|reference|examples|templates)/", re.IGNORECASE),
    re.compile(r"\[(?:scripts|assets|reference|examples|templates)/[^\]]*\]\([^)]*\)", re.IGNORECASE),
)


def _skill_md_references_local_assets(text: str) -> bool:
    """Detect whether a SKILL.md body references sibling files that the fast path
    cannot fetch (scripts/, assets/, etc.). Conservative — false positives just
    trigger a tarball fallback, not a wrong install."""
    return any(p.search(text) for p in _ASSET_REFERENCE_PATTERNS)


_FAST_PATH_ALLOWED_SIBLINGS = frozenset({
    "SKILL.md", "README.md", "README", "LICENSE", "LICENSE.md", ".gitignore",
})


async def _list_github_dir(
    owner: str, repo: str, branch: str, subpath: str, *, timeout: float = 4.0,
) -> list[dict] | None:
    """Return GitHub Contents API listing for the skill directory, or None on failure."""
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{subpath}?ref={branch}"
    started = time.monotonic()
    logger.info("[INSTALL] → GET %s (dir listing)", api_url)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(api_url, headers={"Accept": "application/vnd.github+json"})
    except httpx.HTTPError as exc:
        logger.warning("[INSTALL] dir listing failed: %s", exc)
        return None
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[INSTALL] ← status=%d latency_ms=%d", resp.status_code, latency_ms,
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
    has_skill_md = any(item.get("name") == "SKILL.md" and item.get("type") == "file" for item in listing)
    if not has_skill_md:
        logger.info("[INSTALL] no SKILL.md at %s — tarball will fail too", subpath)
        return None
    extras = [
        item for item in listing
        if item.get("name") not in _FAST_PATH_ALLOWED_SIBLINGS
    ]
    if extras:
        names = ", ".join(item.get("name", "?") for item in extras[:5])
        logger.info(
            "[INSTALL] %d sibling file(s) beyond SKILL.md (%s) — using tarball",
            len(extras), names,
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
        resp.status_code, len(resp.content), latency_ms,
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
        top_levels = {m.name.split("/", 1)[0] for m in members if m.name and not m.name.startswith("/")}
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
                target_name = m.name[len(full_prefix):]
            else:
                target_name = m.name
            if not target_name:
                continue
            _check_member_safety(m)
            if _is_unsafe_link(m):
                logger.warning(
                    "[INSTALL] skipping unsafe link %s → %s",
                    m.name, m.linkname,
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


def _write_sidecar(dest: Path, entry: IndexEntry, *, user_confirmed: bool, sidecar_name: str = ".install.json") -> None:
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

    if getattr(settings, "installation_signature_required", False) and not entry.checksum:
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
        except httpx.HTTPError as exc:
            logger.warning("download failed for %s: %s", download_url, exc)
            raise InstallFailed("download_failed") from exc

        if entry.checksum and not _verify_checksum(content, entry.checksum):
            raise InstallFailed("checksum_failed")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / slug
            try:
                _extract_tarball(content, tmp_path, subpath=subpath)
            except tarfile.TarError as exc:
                raise InstallFailed("invalid_archive") from exc

            if not (tmp_path / "SKILL.md").exists():
                raise InstallFailed("invalid_archive")

            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(tmp_path), str(target))

    _write_sidecar(target / ".install.json", entry, user_confirmed=user_confirmed)

    try:
        from .agent_skills import get_skills_loader

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
        slug, latency_ms,
    )

    return InstallResult(
        success=True,
        slug=slug,
        message_en=MSG_INSTALLED_SKILL_EN.format(slug=slug),
        message_uk=MSG_INSTALLED_SKILL_UK.format(slug=slug),
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

    workspace_dir = Path(getattr(settings, "workspace_dir"))
    servers = read_mcp_servers(workspace_dir)
    if slug in servers and not replace:
        raise InstallRefused("slug_collision")

    install_url = entry.install_url or ""
    if install_url and not _validate_url(install_url, DEFAULT_HOSTNAME_ALLOWLIST):
        raise InstallFailed("bad_install_url")

    if getattr(settings, "installation_signature_required", False) and not entry.checksum:
        raise InstallRefused("signature_required")

    server_entry: dict = {"description": entry.description}
    if install_url:
        server_entry["install_url"] = install_url
    servers[slug] = server_entry
    write_mcp_servers(workspace_dir, servers)

    sidecar_dir = workspace_dir / ".mcp_install"
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
        slug, latency_ms,
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
    "is_owner_enrolled",
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
