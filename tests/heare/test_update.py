"""A newer heare is found, fetched, checked and handed to the app."""

from __future__ import annotations

import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from heare.api import update
from heare.api.update import Updates, UpdateError, expected_sum, read_release

DMG = b"a dmg, as far as this test knows" * 100


def _release(version="0.2.0", base="http://feed", arch="arm64", sums=True):
    assets = [{"name": f"heare-{arch}.dmg", "browser_download_url": f"{base}/heare-{arch}.dmg"}]
    if sums:
        assets.append({"name": "SHA256SUMS.txt", "browser_download_url": f"{base}/SHA256SUMS.txt"})
    return {"tag_name": f"v{version}", "body": "Що нового: копії памʼяті.",
            "html_url": "https://github.com/onelenyk/heare/releases/tag/v" + version,
            "assets": assets}


def test_a_newer_release_is_newer_and_names_this_macs_dmg():
    found = read_release(_release(), "0.1.0", "arm64")
    assert found["newer"] is True and found["latest"] == "0.2.0"
    assert found["dmg"] == "http://feed/heare-arm64.dmg"
    assert found["sums"] == "http://feed/SHA256SUMS.txt"
    assert found["notes"].startswith("Що нового")


@pytest.mark.parametrize("current", ["0.2.0", "0.3.0", "0.10.0"])
def test_the_same_or_an_older_release_is_not_newer(current):
    assert read_release(_release("0.2.0"), current, "arm64")["newer"] is False


def test_no_dmg_for_this_mac_is_said_as_none():
    found = read_release(_release(arch="arm64"), "0.1.0", "x86_64")
    assert found["newer"] is True and found["dmg"] is None


def test_a_version_that_is_not_one_is_never_newer():
    assert read_release(_release("next"), "0.1.0")["newer"] is False
    assert read_release(_release("0.2.0"), None)["newer"] is False


def test_the_sum_is_read_for_its_file_only():
    sums = f"{'a' * 64}  heare-x86_64.dmg\n{'b' * 64}  heare-arm64.dmg\n"
    assert expected_sum(sums, "heare-arm64.dmg") == "b" * 64
    assert expected_sum(sums, "heare-other.dmg") is None
    assert expected_sum("not a sum  heare-arm64.dmg", "heare-arm64.dmg") is None


class _Settings:
    def __init__(self, feed):
        self.update_feed = feed


async def _feed(release_for, dmg=DMG, sums=None):
    """A local GitHub: the release, the sums and the dmg."""
    async def latest(request):
        return web.json_response(release_for(str(request.url.origin())))

    async def sums_file(request):
        text = sums if sums is not None else f"{hashlib.sha256(dmg).hexdigest()}  heare-arm64.dmg\n"
        return web.Response(text=text)

    async def dmg_file(request):
        return web.Response(body=dmg)

    app = web.Application()
    app.router.add_get("/latest", latest)
    app.router.add_get("/SHA256SUMS.txt", sums_file)
    app.router.add_get("/heare-arm64.dmg", dmg_file)
    server = TestServer(app)
    await server.start_server()
    return server


def _home(tmp_path):
    home = tmp_path / ".heare"
    (home / "memory" / "journal").mkdir(parents=True)
    (home / "memory" / "journal" / "2026-10-25.jsonl").write_text("{}\n", encoding="utf-8")
    (home / "settings").mkdir()
    return home


async def test_an_update_is_found_fetched_checked_backed_up_and_handed_over(tmp_path, monkeypatch):
    monkeypatch.setenv("HEARE_PARENT_PID", "1")
    server = await _feed(lambda base: _release(base=base))
    home = _home(tmp_path)
    seen = []
    try:
        updates = Updates(_Settings(f"{server.make_url('/latest')}"), "0.1.0", home,
                          lambda kind, **f: seen.append(f["state"]), arch="arm64")
        checked = await updates.check()
        assert checked["state"] == "available" and checked["latest"] == "0.2.0"
        done = await updates.install()
    finally:
        await server.close()

    assert done["state"] == "installing" and done["version"] == "0.2.0"
    dmg = home / "cache" / "update" / "heare-0.2.0-arm64.dmg"
    assert done["dmg"] == str(dmg) and dmg.read_bytes() == DMG
    assert list((home / "backups").glob("heare-*.zip")), "a backup before the update"
    assert seen[-3:] == ["downloading", "backing_up", "installing"]
    # The new version, started by the app, says what brought it.
    after = Updates(_Settings(""), "0.2.0", home, arch="arm64")
    assert after.state["state"] == "updated" and after.state["previous"] == "0.1.0"


async def test_a_dmg_that_does_not_match_its_sum_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("HEARE_PARENT_PID", "1")
    server = await _feed(lambda base: _release(base=base), sums=f"{'0' * 64}  heare-arm64.dmg\n")
    home = _home(tmp_path)
    try:
        updates = Updates(_Settings(f"{server.make_url('/latest')}"), "0.1.0", home, arch="arm64")
        await updates.check()
        done = await updates.install()
    finally:
        await server.close()
    assert done["state"] == "failed" and "не збігається" in done["error"]
    assert not list((home / "cache" / "update").glob("*.dmg"))
    assert not (home / "backups").exists()


async def test_nothing_is_installed_when_there_is_nothing_newer(tmp_path, monkeypatch):
    monkeypatch.setenv("HEARE_PARENT_PID", "1")
    server = await _feed(lambda base: _release("0.1.0", base=base))
    try:
        updates = Updates(_Settings(f"{server.make_url('/latest')}"), "0.1.0", _home(tmp_path))
        assert (await updates.check())["state"] == "current"
        with pytest.raises(UpdateError):
            await updates.install()
    finally:
        await server.close()


async def test_a_brain_the_app_did_not_start_sends_to_the_release_page(tmp_path, monkeypatch):
    monkeypatch.delenv("HEARE_PARENT_PID", raising=False)
    server = await _feed(lambda base: _release(base=base))
    try:
        updates = Updates(_Settings(f"{server.make_url('/latest')}"), "0.1.0", _home(tmp_path))
        checked = await updates.check()
        assert checked["can_install"] is False and checked["page"].endswith("v0.2.0")
        with pytest.raises(UpdateError, match="сторінки релізу"):
            await updates.install()
    finally:
        await server.close()


async def test_a_feed_that_does_not_answer_is_said_and_nothing_breaks(tmp_path):
    updates = Updates(_Settings("http://127.0.0.1:9/latest"), "0.1.0", _home(tmp_path))
    checked = await updates.check()
    assert checked["error"] and checked["state"] == "idle"


def test_the_feed_is_githubs_unless_the_settings_name_another(tmp_path):
    assert Updates(_Settings(""), "0.1.0", tmp_path).feed == update.FEED
    assert Updates(_Settings("http://x/latest"), "0.1.0", tmp_path).feed == "http://x/latest"


def test_the_app_saying_it_failed_is_shown(tmp_path):
    updates = Updates(_Settings(""), "0.1.0", tmp_path)
    (tmp_path / "cache" / "update").mkdir(parents=True)
    (tmp_path / "cache" / "update" / "installing.json").write_text(json.dumps({"to": "0.2.0"}))
    failed = updates.failed("тека Applications не записувана")
    assert failed["state"] == "failed" and "Applications" in failed["error"]
    assert not (tmp_path / "cache" / "update" / "installing.json").exists()
