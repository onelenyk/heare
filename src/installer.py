"""Installer for skills + MCP servers sourced from discovery (US-006).

Security-critical. All installs require a hard consent gate (speaker-ID OR
configured passphrase) and an explicit ``user_confirmed=True`` flag from the
LLM tool call before any filesystem mutation occurs.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
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
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return resp.content


def _extract_tarball(content: bytes, dest: Path) -> None:
    """Extract the tarball into dest. Strips a single top-level directory if present.

    Raises tarfile.TarError on bad archives.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=BytesIO(content), mode="r:*") as tf:
        members = tf.getmembers()
        top_levels = {m.name.split("/", 1)[0] for m in members if m.name and not m.name.startswith("/")}
        strip_prefix = ""
        if len(top_levels) == 1:
            sole = next(iter(top_levels))
            if any(m.name != sole and m.name.startswith(sole + "/") for m in members):
                strip_prefix = sole + "/"
        for m in members:
            if m.name.startswith("/") or ".." in Path(m.name).parts:
                continue
            target_name = m.name[len(strip_prefix):] if strip_prefix and m.name.startswith(strip_prefix) else m.name
            if not target_name:
                continue
            target_path = dest / target_name
            if m.isdir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            f = tf.extractfile(m)
            if f is None:
                continue
            target_path.write_bytes(f.read())


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

    try:
        content = await _download(install_url)
    except httpx.HTTPError as exc:
        logger.warning("download failed for %s: %s", install_url, exc)
        raise InstallFailed("download_failed") from exc

    if entry.checksum and not _verify_checksum(content, entry.checksum):
        raise InstallFailed("checksum_failed")

    if getattr(settings, "installation_signature_required", False) and not entry.checksum:
        raise InstallRefused("signature_required")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / slug
        try:
            _extract_tarball(content, tmp_path)
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
