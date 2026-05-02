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

from src import installer
from src.capability_index import IndexEntry


@dataclass
class _FakeSettings:
    speaker_id_enabled: bool = True
    confirmation_passphrase: str | None = "open sesame"
    speakers_file: Path | None = None
    workspace_dir: Path | None = None
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
    speakers_file = tmp_path / "speakers.json"
    speakers_file.write_text(json.dumps({
        "version": 1,
        "speakers": {"owner": {"embeddings": [[0.1, 0.2, 0.3]]}},
    }))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return _FakeSettings(
        speaker_id_enabled=True,
        confirmation_passphrase=None,
        speakers_file=speakers_file,
        workspace_dir=workspace,
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
    return patch("src.installer._download", AsyncMock(return_value=content))


@pytest.mark.asyncio
async def test_install_refused_when_no_consent_method(settings, fake_home):
    settings.speaker_id_enabled = False
    settings.confirmation_passphrase = None

    result = await installer.install_skill(_entry(), settings=settings, user_confirmed=True)
    assert result.success is False
    assert result.error_code == "no_consent_method"
    assert result.message_en == installer.MSG_NO_CONSENT_EN
    assert result.message_uk == installer.MSG_NO_CONSENT_UK


@pytest.mark.asyncio
async def test_install_refused_when_user_not_confirmed(settings, fake_home):
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
    with _patch_download(tarball), patch("src.agent_skills.get_skills_loader", return_value=fake_loader):
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


@pytest.mark.asyncio
async def test_install_mcp_uses_write_mcp_servers_helper(settings, fake_home):
    entry = IndexEntry(
        source="mcp",
        name="bar",
        description="a test mcp",
        install_url="https://github.com/foo/bar",
    )
    with patch("src.installer.write_mcp_servers") as mock_write, patch("src.installer.read_mcp_servers", return_value={}):
        result = await installer.install_mcp_server(entry, settings=settings, user_confirmed=True)
    assert result.success is True
    mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_install_mcp_returns_requires_restart_true(settings, fake_home):
    entry = IndexEntry(
        source="mcp",
        name="bar",
        description="a test mcp",
        install_url="https://github.com/foo/bar",
    )
    result = await installer.install_mcp_server(entry, settings=settings, user_confirmed=True)
    assert result.success is True
    assert result.requires_restart is True


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
    with patch("src.installer._try_raw_fast_path", AsyncMock(return_value=None)), \
         patch("src.installer._download", AsyncMock(return_value=tarball)) as dl:
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
    with patch("src.installer._try_raw_fast_path", AsyncMock(return_value=None)), \
         patch("src.installer._download", AsyncMock(return_value=tarball)):
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
        "src.installer._try_raw_fast_path",
        AsyncMock(return_value=(skill_md, [])),
    ), patch("src.installer._download", AsyncMock()) as dl:
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
