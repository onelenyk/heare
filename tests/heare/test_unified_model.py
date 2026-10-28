"""One model for roles and parts: the keeper's form, install.sh, and the
public install page say the same names in the same order."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from heare import machine, setup
from heare.config import Settings

ROOT = Path(__file__).resolve().parents[2]

UK_ROLES = {
    "all": ("Усе тут", "мозок і частини на цій машині"),
    "body": ("Тіло", "мозок деінде; тут частини"),
    "brain": ("Лише мозок", "частини підключаються з інших машин"),
}

UK_PARTS = {
    "voice": ("Голос", "слухає й говорить"),
    "eyes": ("Очі", "що попереду, чи ти тут, чи щось грає"),
    "sources": ("Джерела", "погода й інше: що зараз навколо"),
    "hands": ("Руки CLI", "агент діє в системі: код, файли, команди"),
    "native-hands": ("Нативні руки", "швидкі дії з тілами: OLED, світло, піни"),
}


def _site_model() -> dict:
    page = (ROOT / "site" / "install.html").read_text(encoding="utf-8")
    found = re.search(
        r'<script type="application/json" id="model">\s*(.*?)\s*</script>',
        page, re.S)
    assert found, "site/install.html has no machine-readable model block"
    return json.loads(found.group(1))


def test_the_local_form_payload_comes_from_the_canonical_model(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "config.toml", parts_dir=tmp_path / "parts")
    monkeypatch.setattr(setup, "Settings", lambda: settings)
    monkeypatch.setattr(setup.keys, "view", lambda: {})

    payload = setup._machine()

    assert payload["roles"] == machine.roles()
    assert [role["name"] for role in payload["roles"]] == list(machine.ROLES)
    assert [part["name"] for part in payload["parts"]] == list(machine.PARTS)


def test_the_roles_are_all_body_brain_in_that_order():
    assert list(machine.ROLES) == ["all", "body", "brain"]
    assert [role["name"] for role in machine.roles()] == ["all", "body", "brain"]


def test_the_public_page_roles_match_the_canonical_model():
    roles = _site_model()["roles"]

    assert [role["name"] for role in roles] == list(machine.ROLES)
    for role in roles:
        label, what = UK_ROLES[role["name"]]
        assert role["label"] == label
        assert role["what"] == what


def test_the_public_page_parts_match_the_canonical_model():
    parts = _site_model()["parts"]

    assert [part["name"] for part in parts] == list(machine.PARTS)
    for part in parts:
        label, what = UK_PARTS[part["name"]]
        assert part["label"] == label
        assert part["what"] == what
        assert part["only"] == machine.PARTS[part["name"]].only


def test_the_public_page_names_both_hands_apart():
    labels = {part["name"]: part["label"] for part in _site_model()["parts"]}

    assert labels["hands"] == "Руки CLI"
    assert labels["native-hands"] == "Нативні руки"


def test_install_sh_installs_every_part_and_the_brain():
    script = (ROOT / "install.sh").read_text(encoding="utf-8")
    base = re.search(r'^TOOLS="([^"]+)"', script, re.M).group(1).split()
    mac = re.search(r'^\[ "\$OS" = "mac" \] && TOOLS="\$TOOLS ([^"]+)"',
                    script, re.M).group(1).split()

    assert base == ["brain", "keeper", "voice", "hands", "sources", "native-hands", "chat"]
    assert "eyes" not in base and "eyes" in mac
    for name, part in machine.PARTS.items():
        assert name in (mac if part.only == "darwin" else base)
    assert {"brain", "keeper", "chat"} <= set(base)


def test_install_sh_gives_the_brain_the_memory_wheel_it_depends_on():
    script = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "core reach memory $TOOLS" in script
    assert '--with "$MEMORY"' in script


def test_install_sh_accepts_the_canonical_roles_and_old_aliases():
    script = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "all|body|brain|mac|server" in script
    assert "mac) ROLE=all" in script
    assert "server) ROLE=brain" in script
    assert "mac is an alias for all, server for brain" in script


@pytest.mark.parametrize(("name", "wheel"), [
    ("hands", "heare_cli_hands"),
    ("native-hands", "heare_native_hands"),
    ("voice", "heare_voice"),
    ("sources", "heare_sources"),
])
def test_install_sh_finds_the_wheels_by_package_name(name, wheel):
    script = (ROOT / "install.sh").read_text(encoding="utf-8")
    function = re.search(r"wheel_base\(\) \{.*?\n\}", script, re.S).group(0)

    found = subprocess.run(
        ["sh", "-c", f"{function}\nwheel_base {name}"],
        capture_output=True, text=True, check=True).stdout

    assert found == wheel
    assert "heare_native-hands" not in script


def test_the_local_form_has_no_second_role_list():
    page = (ROOT / "packages/heare-keeper/heare/installer.html").read_text(encoding="utf-8")

    assert "const ROLES" not in page
    assert "machine.roles" in page
    assert 'tr("Role")' in page
