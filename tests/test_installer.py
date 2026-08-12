"""Tests for installer (US-006)."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills import installer
from src.agent.tools.capability_index import IndexEntry


@dataclass
class _FakeSettings:
    capability_install_enabled: bool = True
    workspace_dir: Path | None = None
    mcp_dir: Path | None = None
    installation_signature_required: bool = False
    skills_paths: list[str] = field(default_factory=list)


def _make_tarball(skill_md_text: str = "---\nname: foo\ndescription: A test skill\n---\nbody\n", *, include_skill_md: bool = True, prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if include_skill_md:
            data = skill_md_text.encode()
            info = tarfile.TarInfo(name=f"{prefix}SKILL.md" if prefix else "SKILL.md")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        readme = b"hello"
        info2 = tarfile.TarInfo(name=f"{prefix}README.md" if prefix else "README.md")
        info2.size = len(readme)
        tf.addfile(info2, io.BytesIO(readme))
    return buf.getvalue()


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def settings(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return _FakeSettings(
        capability_install_enabled=True,
        workspace_dir=workspace,
        mcp_dir=tmp_path / "mcp",
    )


def _entry(name: str = "foo", install_url: str = "https://github.com/foo/foo/archive/v1.tar.gz", checksum: str | None = None) -> IndexEntry:
    return IndexEntry(
        source="skill",
        name=name,
        description="a test skill",
        install_url=install_url,
        checksum=checksum,
    )


def _patch_download(content: bytes):
    return patch("src.skills.installer._download", AsyncMock(return_value=content))


@pytest.mark.asyncio
async def test_install_refused_when_installs_disabled(settings, fake_home):
    settings.capability_install_enabled = False

    result = await installer.install_skill(_entry(), settings=settings, user_confirmed=True)
    assert result.success is False
    assert result.error_code == "installs_disabled"
    assert result.message_en == installer.MSG_NO_CONSENT_EN
    assert result.message_uk == installer.MSG_NO_CONSENT_UK


@pytest.mark.asyncio
async def test_install_refused_when_user_not_confirmed(settings, fake_home):
    settings.capability_install_enabled = True
    with pytest.raises(installer.InstallRefused) as exc:
        await installer.install_skill(_entry(), settings=settings, user_confirmed=False)
    assert str(exc.value) == "user_not_confirmed"


@pytest.mark.asyncio
async def test_install_skill_slug_collision_no_replace(settings, fake_home):
    target = fake_home / ".heare" / "skills" / "_marketplace" / "foo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("existing")

    with pytest.raises(installer.InstallRefused) as exc:
        await installer.install_skill(_entry(), settings=settings, user_confirmed=True, replace=False)
    assert str(exc.value) == "slug_collision"


@pytest.mark.asyncio
async def test_install_skill_slug_collision_with_replace(settings, fake_home):
    target = fake_home / ".heare" / "skills" / "_marketplace" / "foo"
    target.mkdir(parents=True)
    (target / "OLD.md").write_text("existing")

    tarball = _make_tarball()
    with _patch_download(tarball):
        result = await installer.install_skill(_entry(), settings=settings, user_confirmed=True, replace=True)

    assert result.success is True
    assert (target / "SKILL.md").exists()
    assert not (target / "OLD.md").exists()


@pytest.mark.asyncio
async def test_install_skill_writes_sidecar_provenance(settings, fake_home):
    tarball = _make_tarball()
    checksum = hashlib.sha256(tarball).hexdigest()
    with _patch_download(tarball):
        result = await installer.install_skill(_entry(checksum=checksum), settings=settings, user_confirmed=True)

    sidecar = fake_home / ".heare" / "skills" / "_marketplace" / "foo" / ".install.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert data["source_url"]
    assert data["install_timestamp"]
    assert data["user_confirmed"] is True
    assert data["signature_verified"] is False
    assert data["source_marketplace"] == "github.com"
    assert data["checksum_sha256"] == checksum
    assert result.success is True


@pytest.mark.asyncio
async def test_install_skill_checksum_match(settings, fake_home):
    tarball = _make_tarball()
    expected = hashlib.sha256(tarball).hexdigest()
    with _patch_download(tarball):
        result = await installer.install_skill(_entry(checksum=expected), settings=settings, user_confirmed=True)
    assert result.success is True


@pytest.mark.asyncio
async def test_install_skill_checksum_mismatch(settings, fake_home):
    tarball = _make_tarball()
    bad_checksum = "0" * 64
    with _patch_download(tarball):
        with pytest.raises(installer.InstallFailed) as exc:
            await installer.install_skill(_entry(checksum=bad_checksum), settings=settings, user_confirmed=True)
    assert str(exc.value) == "checksum_failed"
    target = fake_home / ".heare" / "skills" / "_marketplace" / "foo"
    assert not target.exists()


@pytest.mark.asyncio
async def test_install_skill_invalidates_loader(settings, fake_home):
    tarball = _make_tarball()
    fake_loader = MagicMock()
    with _patch_download(tarball), patch("src.skills.agent_skills.get_skills_loader", return_value=fake_loader):
        result = await installer.install_skill(_entry(), settings=settings, user_confirmed=True)
    assert result.success is True
    fake_loader.invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_install_skill_rebuilds_capability_index(settings, fake_home):
    tarball = _make_tarball()
    fake_index = MagicMock()
    with _patch_download(tarball):
        result = await installer.install_skill(
            _entry(), settings=settings, capability_index=fake_index, user_confirmed=True
        )
    assert result.success is True
    fake_index.rebuild.assert_called_once()


@pytest.mark.asyncio
async def test_install_skill_no_skillmd_in_archive_rolls_back(settings, fake_home):
    tarball = _make_tarball(include_skill_md=False)
    with _patch_download(tarball):
        with pytest.raises(installer.InstallFailed) as exc:
            await installer.install_skill(_entry(), settings=settings, user_confirmed=True)
    assert str(exc.value) == "invalid_archive"
    target = fake_home / ".heare" / "skills" / "_marketplace" / "foo"
    assert not target.exists()


def _mcp_entry(name: str = "bar", *, launch: dict | None = None, install_url: str | None = "https://github.com/foo/bar") -> IndexEntry:
    if launch is None:
        launch = {"command": "npx", "args": ["-y", "test-mcp@latest"]}
    return IndexEntry(
        source="mcp",
        name=name,
        description="a test mcp",
        install_url=install_url,
        launch=launch,
    )


@pytest.mark.asyncio
async def test_install_mcp_uses_write_mcp_servers_helper(settings, fake_home):
    with patch("src.skills.installer.write_mcp_servers") as mock_write, patch("src.skills.installer.read_mcp_servers", return_value={}):
        result = await installer.install_mcp_server(_mcp_entry(), settings=settings, user_confirmed=True)
    assert result.success is True
    mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_install_mcp_returns_requires_restart_true(settings, fake_home):
    result = await installer.install_mcp_server(_mcp_entry(), settings=settings, user_confirmed=True)
    assert result.success is True
    assert result.requires_restart is True


@pytest.mark.asyncio
async def test_install_mcp_writes_launch_command_args_into_mcp_json(settings, fake_home):
    entry = _mcp_entry(launch={"command": "npx", "args": ["-y", "@foo/server"], "env": {"FOO_TOKEN": "abc"}})
    result = await installer.install_mcp_server(entry, settings=settings, user_confirmed=True)
    assert result.success is True

    mcp_json = settings.mcp_dir / ".mcp.json"
    data = json.loads(mcp_json.read_text())
    server = data["mcpServers"]["bar"]
    assert server["command"] == "npx"
    assert server["args"] == ["-y", "@foo/server"]
    assert server["env"] == {"FOO_TOKEN": "abc"}
    assert server["description"] == "a test mcp"


@pytest.mark.asyncio
async def test_install_mcp_omits_env_when_not_provided(settings, fake_home):
    entry = _mcp_entry(launch={"command": "npx", "args": ["server"]})
    result = await installer.install_mcp_server(entry, settings=settings, user_confirmed=True)
    assert result.success is True

    data = json.loads((settings.mcp_dir / ".mcp.json").read_text())
    assert "env" not in data["mcpServers"]["bar"]


@pytest.mark.asyncio
async def test_install_mcp_refuses_when_launch_missing(settings, fake_home):
    entry = IndexEntry(
        source="mcp",
        name="bar",
        description="a test mcp",
        install_url="https://github.com/foo/bar",
        launch=None,
    )
    with pytest.raises(installer.InstallFailed) as exc:
        await installer.install_mcp_server(entry, settings=settings, user_confirmed=True)
    assert str(exc.value) == "launch_required"
    assert not (settings.mcp_dir / ".mcp.json").exists()


@pytest.mark.asyncio
async def test_install_skill_returns_requires_restart_false(settings, fake_home):
    tarball = _make_tarball()
    with _patch_download(tarball):
        result = await installer.install_skill(_entry(), settings=settings, user_confirmed=True)
    assert result.success is True
    assert result.requires_restart is False


@pytest.mark.asyncio
async def test_install_message_en_uk_both_present(settings, fake_home):
    tarball = _make_tarball()
    with _patch_download(tarball):
        result = await installer.install_skill(_entry(), settings=settings, user_confirmed=True)
    assert result.message_en
    assert result.message_uk
    assert "foo" in result.message_en
    assert "foo" in result.message_uk


def _make_traversal_tarball() -> bytes:
    """Build a tarball containing a path-traversal member name."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"evil"
        info = tarfile.TarInfo(name="../etc/passwd")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_install_skill_rejects_archive_with_path_traversal(settings, fake_home):
    tarball = _make_traversal_tarball()
    with _patch_download(tarball):
        with pytest.raises(installer.InstallFailed) as exc:
            await installer.install_skill(_entry(), settings=settings, user_confirmed=True)
    assert str(exc.value) in ("unsafe_archive_path", "invalid_archive")
    target = fake_home / ".heare" / "skills" / "_marketplace" / "foo"
    assert not (target.parent.parent.parent.parent / "etc" / "passwd").exists()


def test_parse_github_tree_url_with_subpath():
    result = installer._parse_github_tree_url(
        "https://github.com/owner/repo/tree/main/skills/foo"
    )
    assert result is not None
    tarball_url, subpath = result
    assert tarball_url == "https://codeload.github.com/owner/repo/tar.gz/refs/heads/main"
    assert subpath == "skills/foo"


def test_parse_github_tree_url_branch_only():
    result = installer._parse_github_tree_url(
        "https://github.com/owner/repo/tree/main"
    )
    assert result is not None
    tarball_url, subpath = result
    assert tarball_url == "https://codeload.github.com/owner/repo/tar.gz/refs/heads/main"
    assert subpath == ""


def test_parse_github_tree_url_rejects_non_tree():
    assert installer._parse_github_tree_url("https://github.com/owner/repo") is None
    assert installer._parse_github_tree_url(
        "https://github.com/owner/repo/archive/v1.tar.gz"
    ) is None
    assert installer._parse_github_tree_url(
        "https://example.com/owner/repo/tree/main/skills/foo"
    ) is None


def _make_repo_tarball_with_subpath(subpath: str) -> bytes:
    """Build a tarball mimicking GitHub's repo archive layout: ``repo-main/<subpath>/SKILL.md``."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        prefix = f"repo-main/{subpath}/"
        skill_md = b"---\nname: foo\ndescription: A test skill\n---\nbody\n"
        info = tarfile.TarInfo(name=prefix + "SKILL.md")
        info.size = len(skill_md)
        tf.addfile(info, io.BytesIO(skill_md))
        scripts = b"#!/bin/sh\necho hi\n"
        info2 = tarfile.TarInfo(name=prefix + "scripts/helper.sh")
        info2.size = len(scripts)
        tf.addfile(info2, io.BytesIO(scripts))
        unrelated = b"# top-level readme"
        info3 = tarfile.TarInfo(name="repo-main/README.md")
        info3.size = len(unrelated)
        tf.addfile(info3, io.BytesIO(unrelated))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_install_skill_extracts_subpath_from_github_tree_url(settings, fake_home):
    tarball = _make_repo_tarball_with_subpath("skills/foo")
    entry = _entry(install_url="https://github.com/owner/repo/tree/main/skills/foo")
    with patch("src.skills.installer._try_raw_fast_path", AsyncMock(return_value=None)), \
         patch("src.skills.installer._download", AsyncMock(return_value=tarball)) as dl:
        result = await installer.install_skill(entry, settings=settings, user_confirmed=True)
    assert result.success
    dl.assert_awaited_once()
    download_url = dl.await_args.args[0]
    assert download_url == "https://codeload.github.com/owner/repo/tar.gz/refs/heads/main"
    target = fake_home / ".heare" / "skills" / "_marketplace" / "foo"
    assert (target / "SKILL.md").read_text().startswith("---")
    assert (target / "scripts" / "helper.sh").exists()
    assert not (target / "README.md").exists()


@pytest.mark.asyncio
async def test_install_skill_subpath_missing_in_archive(settings, fake_home):
    tarball = _make_repo_tarball_with_subpath("skills/other")
    entry = _entry(install_url="https://github.com/owner/repo/tree/main/skills/foo")
    with patch("src.skills.installer._try_raw_fast_path", AsyncMock(return_value=None)), \
         patch("src.skills.installer._download", AsyncMock(return_value=tarball)):
        with pytest.raises(installer.InstallFailed) as exc:
            await installer.install_skill(entry, settings=settings, user_confirmed=True)
    assert str(exc.value) in ("subpath_not_found", "invalid_archive")
    target = fake_home / ".heare" / "skills" / "_marketplace" / "foo"
    assert not target.exists()


@pytest.mark.asyncio
async def test_install_skill_uses_raw_fast_path_when_no_assets(settings, fake_home):
    """When SKILL.md has no asset references, fast path writes only SKILL.md."""
    skill_md = "---\nname: foo\ndescription: A test skill\n---\nplain body, no assets.\n"
    entry = _entry(install_url="https://github.com/owner/repo/tree/main/skills/foo")
    with patch(
        "src.skills.installer._try_raw_fast_path",
        AsyncMock(return_value=(skill_md, [])),
    ), patch("src.skills.installer._download", AsyncMock()) as dl:
        result = await installer.install_skill(entry, settings=settings, user_confirmed=True)
    assert result.success
    dl.assert_not_awaited()
    target = fake_home / ".heare" / "skills" / "_marketplace" / "foo"
    assert (target / "SKILL.md").read_text() == skill_md
    assert not (target / "scripts").exists()


def test_skill_md_references_local_assets_detects_patterns():
    assert installer._skill_md_references_local_assets("Run `${CLAUDE_SKILL_DIR}/scripts/x.sh`")
    assert installer._skill_md_references_local_assets("Output: !`bash scripts/run.sh`")
    assert installer._skill_md_references_local_assets("See [doc](reference/api.md)")
    assert not installer._skill_md_references_local_assets("Plain skill body, no refs.")


def test_parse_github_tree_parts_extracts_components():
    result = installer._parse_github_tree_parts(
        "https://github.com/lsiten/mult-agent/tree/main/skills/turix-windows"
    )
    assert result == ("lsiten", "mult-agent", "main", "skills/turix-windows")


def test_raw_url_for_builds_correct_path():
    assert installer._raw_url_for("o", "r", "main", "skills/foo", "SKILL.md") == (
        "https://raw.githubusercontent.com/o/r/main/skills/foo/SKILL.md"
    )


# ---------------------------------------------------------------------------
# _download — overall-timeout, size-cap, every-exit-path logging
#
# These tests exist because a real install hung silently for ~2 minutes:
# the per-op httpx timeout is between chunks, so a slow-trickle stream
# blew past it without firing, and no completion log ever appeared.


import asyncio  # noqa: E402

import httpx  # noqa: E402


@pytest.mark.asyncio
async def test_download_overall_timeout_raises_install_failed(monkeypatch, caplog):
    """A stream that never finishes within ``overall_timeout`` must raise
    ``InstallFailed("download_timeout")`` and log a TIMEOUT line — not hang."""

    async def _hang(_url):
        await asyncio.sleep(10)
        return httpx.Response(200, content=b"x")

    transport = httpx.MockTransport(_hang)

    real_async_client = installer.httpx.AsyncClient

    class _StubClient:
        def __init__(self, *a, **kw):
            self._client = real_async_client(transport=transport, timeout=kw.get("timeout"))
        async def __aenter__(self):
            return self._client
        async def __aexit__(self, *exc):
            await self._client.aclose()

    monkeypatch.setattr(installer.httpx, "AsyncClient", _StubClient)

    caplog.set_level("WARNING", logger="heare.installer")
    with pytest.raises(installer.InstallFailed, match="download_timeout"):
        await installer._download(
            "https://example.com/x.tar.gz", timeout=1.0, overall_timeout=0.05
        )
    assert any("TIMEOUT" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_download_size_cap_raises_install_failed(monkeypatch, caplog):
    """A response larger than ``max_bytes`` must abort with
    ``download_too_large`` rather than buffer unbounded into memory."""

    big = b"A" * 4096

    async def _big(_url):
        return httpx.Response(200, content=big)

    transport = httpx.MockTransport(_big)

    real_async_client = installer.httpx.AsyncClient

    class _StubClient:
        def __init__(self, *a, **kw):
            self._client = real_async_client(transport=transport, timeout=kw.get("timeout"))
        async def __aenter__(self):
            return self._client
        async def __aexit__(self, *exc):
            await self._client.aclose()

    monkeypatch.setattr(installer.httpx, "AsyncClient", _StubClient)

    caplog.set_level("WARNING", logger="heare.installer")
    with pytest.raises(installer.InstallFailed, match="download_too_large"):
        await installer._download(
            "https://example.com/x.tar.gz",
            timeout=2.0,
            overall_timeout=5.0,
            max_bytes=1024,
        )
    assert any("FAILED" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_download_logs_completion_on_success(monkeypatch, caplog):
    """Successful download must emit the ``← status=`` line — silence on
    success was the original symptom that masked the bug."""

    async def _ok(_url):
        return httpx.Response(200, content=b"hello")

    transport = httpx.MockTransport(_ok)

    real_async_client = installer.httpx.AsyncClient

    class _StubClient:
        def __init__(self, *a, **kw):
            self._client = real_async_client(transport=transport, timeout=kw.get("timeout"))
        async def __aenter__(self):
            return self._client
        async def __aexit__(self, *exc):
            await self._client.aclose()

    monkeypatch.setattr(installer.httpx, "AsyncClient", _StubClient)

    caplog.set_level("INFO", logger="heare.installer")
    out = await installer._download(
        "https://example.com/x.tar.gz", timeout=2.0, overall_timeout=5.0
    )
    assert out == b"hello"
    assert any("status=200" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_download_cancelled_logs_and_propagates(monkeypatch, caplog):
    """``asyncio.CancelledError`` must propagate (not be silently
    swallowed) AND emit a CANCELLED log line so a dropped tool-call
    future is traceable."""

    started = asyncio.Event()

    async def _slow(_url):
        started.set()
        await asyncio.sleep(10)
        return httpx.Response(200, content=b"x")

    transport = httpx.MockTransport(_slow)

    real_async_client = installer.httpx.AsyncClient

    class _StubClient:
        def __init__(self, *a, **kw):
            self._client = real_async_client(transport=transport, timeout=kw.get("timeout"))
        async def __aenter__(self):
            return self._client
        async def __aexit__(self, *exc):
            await self._client.aclose()

    monkeypatch.setattr(installer.httpx, "AsyncClient", _StubClient)

    caplog.set_level("WARNING", logger="heare.installer")
    task = asyncio.create_task(
        installer._download(
            "https://example.com/x.tar.gz", timeout=5.0, overall_timeout=5.0
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any("CANCELLED" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# create_skill — author user-supplied skills locally
#
# Mirrors the install_skill consent contract but without download or
# checksum: the body comes from the conversation, not the marketplace.


@pytest.mark.asyncio
async def test_create_skill_writes_skill_md_and_sidecar(settings, fake_home):
    result = await installer.create_skill(
        name="audio-debug",
        description="Probe macOS audio devices and report what's connected.",
        body="Run `system_profiler SPAudioDataType` and summarize.",
        settings=settings,
        user_confirmed=True,
    )
    assert result.success is True
    assert result.slug == "audio-debug"
    skill_dir = fake_home / ".heare" / "skills" / "audio-debug"
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert skill_md.startswith("---\nname: audio-debug\n")
    assert "description: Probe macOS audio devices" in skill_md
    assert "system_profiler SPAudioDataType" in skill_md
    sidecar = json.loads((skill_dir / ".install.json").read_text())
    assert sidecar["source_marketplace"] == "user_authored"
    assert sidecar["user_confirmed"] is True


@pytest.mark.asyncio
async def test_create_skill_refuses_without_user_confirmed(settings, fake_home):
    with pytest.raises(installer.InstallRefused, match="user_not_confirmed"):
        await installer.create_skill(
            name="x",
            description="d",
            body="b",
            settings=settings,
            user_confirmed=False,
        )


@pytest.mark.asyncio
async def test_create_skill_refuses_without_consent_method(settings, fake_home):
    settings.capability_install_enabled = False
    result = await installer.create_skill(
        name="x",
        description="d",
        body="b",
        settings=settings,
        user_confirmed=True,
    )
    assert result.success is False
    assert result.error_code == "installs_disabled"


@pytest.mark.asyncio
async def test_create_skill_rejects_invalid_name(settings, fake_home):
    for bad in ["", "Foo", "foo bar", "-foo", "foo-", "foo_bar", "f" * 65]:
        with pytest.raises(installer.InstallFailed):
            await installer.create_skill(
                name=bad,
                description="d",
                body="b",
                settings=settings,
                user_confirmed=True,
            )


@pytest.mark.asyncio
async def test_create_skill_rejects_missing_description(settings, fake_home):
    with pytest.raises(installer.InstallFailed, match="description_required"):
        await installer.create_skill(
            name="foo",
            description="   ",
            body="b",
            settings=settings,
            user_confirmed=True,
        )


@pytest.mark.asyncio
async def test_create_skill_rejects_missing_body(settings, fake_home):
    with pytest.raises(installer.InstallFailed, match="body_required"):
        await installer.create_skill(
            name="foo",
            description="d",
            body="\n  \n",
            settings=settings,
            user_confirmed=True,
        )


@pytest.mark.asyncio
async def test_create_skill_slug_collision_no_replace(settings, fake_home):
    await installer.create_skill(
        name="foo", description="d1", body="b1",
        settings=settings, user_confirmed=True,
    )
    with pytest.raises(installer.InstallRefused, match="slug_collision"):
        await installer.create_skill(
            name="foo", description="d2", body="b2",
            settings=settings, user_confirmed=True, replace=False,
        )


@pytest.mark.asyncio
async def test_create_skill_replace_overwrites(settings, fake_home):
    await installer.create_skill(
        name="foo", description="old desc", body="old body",
        settings=settings, user_confirmed=True,
    )
    result = await installer.create_skill(
        name="foo", description="new desc", body="new body",
        settings=settings, user_confirmed=True, replace=True,
    )
    assert result.success is True
    skill_md = (fake_home / ".heare" / "skills" / "foo" / "SKILL.md").read_text()
    assert "new desc" in skill_md
    assert "new body" in skill_md
    assert "old desc" not in skill_md
    assert "old body" not in skill_md


@pytest.mark.asyncio
async def test_create_skill_invalidates_loader_and_rebuilds_index(settings, fake_home):
    fake_loader = MagicMock()
    fake_loader.invalidate = MagicMock()
    fake_index = MagicMock()
    fake_index.rebuild = MagicMock()
    with patch("src.skills.agent_skills.get_skills_loader", return_value=fake_loader):
        await installer.create_skill(
            name="foo", description="d", body="b",
            settings=settings, capability_index=fake_index, user_confirmed=True,
        )
    assert fake_loader.invalidate.called
    assert fake_index.rebuild.called


@pytest.mark.asyncio
async def test_execute_create_skill_dispatcher_happy_path(settings, fake_home):
    """The LLM-facing dispatcher unwraps JSON args and surfaces the
    InstallResult in the spoken-en/uk shape that pipecat will read back."""
    from src.agent.tools.direct import _execute_create_skill

    args = json.dumps({
        "name": "audio-debug",
        "description": "Probe macOS audio devices.",
        "body": "Run system_profiler SPAudioDataType.",
        "user_confirmed": True,
    })
    result = await _execute_create_skill(args, settings)
    assert result["success"] is True
    assert result["slug"] == "audio-debug"
    assert "audio-debug" in result["spoken"]["en"]
    assert "audio-debug" in result["spoken"]["uk"]


@pytest.mark.asyncio
async def test_execute_create_skill_dispatcher_surfaces_error_code(settings, fake_home):
    """Validation failures must come back as ``error_code`` so the LLM can
    route on it (e.g. ask for a different name) instead of a free-form
    error string."""
    from src.agent.tools.direct import _execute_create_skill

    args = json.dumps({
        "name": "Foo Bar",  # invalid: uppercase + space
        "description": "d",
        "body": "b",
        "user_confirmed": True,
    })
    result = await _execute_create_skill(args, settings)
    assert result["success"] is False
    assert result["error_code"] == "name_invalid_format"


@pytest.mark.asyncio
async def test_execute_create_skill_dispatcher_rejects_bad_json(settings, fake_home):
    from src.agent.tools.direct import _execute_create_skill

    result = await _execute_create_skill("not json{", settings)
    assert result["success"] is False
    assert "Invalid JSON" in result["error"]


@pytest.mark.asyncio
async def test_create_skill_is_discoverable_by_skills_loader(settings, fake_home, monkeypatch):
    """End-to-end: after create_skill, the SkillsLoader sees the new skill
    and can return its body — proving the file layout matches the loader's
    parser contract."""
    from src.skills import agent_skills

    settings.skills_paths = [str(fake_home / ".heare" / "skills")]
    # Fresh loader that scans the fake_home path so we don't pick up
    # whatever's in the developer's real ~/.heare/skills/.
    monkeypatch.setattr(agent_skills, "_loader", None)
    monkeypatch.setattr(agent_skills, "_loader_paths", None)

    await installer.create_skill(
        name="audio-debug",
        description="Probe macOS audio devices.",
        body="Step 1: run `system_profiler SPAudioDataType`.",
        settings=settings,
        user_confirmed=True,
    )

    loader = agent_skills.get_skills_loader(settings)
    loader.invalidate()
    names = loader.get_skill_names()
    assert "audio-debug" in names
    body = loader.load_instructions("audio-debug")
    assert "system_profiler SPAudioDataType" in body


@pytest.mark.asyncio
async def test_install_skill_extracts_off_event_loop(settings, fake_home):
    """``_extract_tarball`` must be invoked via ``asyncio.to_thread`` so a
    multi-MB archive cannot freeze TTS / transcription / heartbeat."""
    tarball = _make_tarball()
    entry = _entry()

    seen: list[str] = []
    real_to_thread = asyncio.to_thread

    async def _spy(func, /, *args, **kwargs):
        if func is installer._extract_tarball:
            seen.append("extract_off_loop")
        return await real_to_thread(func, *args, **kwargs)

    with _patch_download(tarball), patch("asyncio.to_thread", _spy):
        result = await installer.install_skill(
            entry, settings=settings, user_confirmed=True
        )
    assert result.success is True
    assert "extract_off_loop" in seen
