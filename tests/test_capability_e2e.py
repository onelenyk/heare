"""End-to-end smoke tests for the capability discovery + install flow (US-007)."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src import direct_tools, installer
from src.capability_index import IndexEntry


@dataclass
class _FakeSettings:
    workspace_dir: Path | None = None
    skills_paths: list[str] = field(default_factory=list)
    speaker_id_enabled: bool = True
    confirmation_passphrase: str | None = None
    speakers_file: Path | None = None
    identity_file: Path = field(default_factory=lambda: Path("/nonexistent"))
    marketplace_url: str = "https://skillsmp.com"
    mcp_registry_url: str = ""
    installation_signature_required: bool = False


def _make_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        skill_md = b"---\nname: weather-pro\ndescription: weather lookup\n---\nbody\n"
        info = tarfile.TarInfo(name="SKILL.md")
        info.size = len(skill_md)
        tf.addfile(info, io.BytesIO(skill_md))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _reset_singleton():
    direct_tools._capability_index_singleton = None
    yield
    direct_tools._capability_index_singleton = None


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(direct_tools.Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def settings(tmp_path: Path, fake_home: Path) -> _FakeSettings:
    speakers_file = tmp_path / "speakers.json"
    speakers_file.write_text(json.dumps({
        "version": 1,
        "speakers": {"owner": {"embeddings": [[0.1, 0.2, 0.3]]}},
    }))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return _FakeSettings(
        workspace_dir=workspace,
        speaker_id_enabled=True,
        speakers_file=speakers_file,
    )


@pytest.mark.asyncio
async def test_discover_install_flow_smoke(settings, fake_home):
    """Local empty -> remote returns candidate -> install_skill_tool succeeds."""
    from unittest.mock import MagicMock

    # Stage 1: empty local index forces remote discovery.
    empty_index = MagicMock()
    empty_index.entries = []
    empty_index.query = MagicMock(return_value=[])
    empty_index.rebuild = MagicMock()
    direct_tools.set_capability_index(empty_index)

    tarball = _make_tarball()
    checksum = hashlib.sha256(tarball).hexdigest()
    remote_entry = IndexEntry(
        source="skill",
        name="weather-pro",
        description="weather lookup",
        install_url="https://github.com/foo/weather-pro/archive/v1.tar.gz",
        checksum=checksum,
    )

    with patch(
        "src.discovery.discover_capability_remote",
        AsyncMock(return_value=[remote_entry]),
    ):
        disc = await direct_tools._execute_discover_capability(
            json.dumps({"intent": "weather kyiv"}), settings
        )

    assert disc["success"] is True
    assert disc["source"] == "remote"
    assert disc["results"][0]["name"] == "weather-pro"

    # Stage 2: install — index now needs to find weather-pro by slug.
    populated_index = MagicMock()
    populated_index.entries = [remote_entry]
    populated_index.rebuild = MagicMock()
    direct_tools.set_capability_index(populated_index)

    with patch("src.installer._download", AsyncMock(return_value=tarball)):
        inst = await direct_tools._execute_install_skill_tool(
            json.dumps({"slug": "weather-pro", "user_confirmed": True}),
            settings,
        )

    assert inst["success"] is True
    sidecar = fake_home / ".heare" / "skills" / "_marketplace" / "weather-pro" / ".install.json"
    assert sidecar.exists()
    populated_index.rebuild.assert_called()


@pytest.mark.asyncio
async def test_discover_returns_empty_when_no_match(settings, fake_home):
    from unittest.mock import MagicMock

    empty_index = MagicMock()
    empty_index.entries = []
    empty_index.query = MagicMock(return_value=[])
    direct_tools.set_capability_index(empty_index)

    with patch(
        "src.discovery.discover_capability_remote",
        AsyncMock(return_value=[]),
    ):
        disc = await direct_tools._execute_discover_capability(
            json.dumps({"intent": "obscure thing"}), settings
        )

    assert disc["success"] is True
    assert disc["results"] == []
    assert "don't have a tool" in disc["spoken"]["en"].lower()
