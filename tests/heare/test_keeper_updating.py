"""The keeper's side of an update on a machine without the app."""

import hashlib
import json


from heare import part, updating
from heare.updating import Updating, command, sum_for, wanted

R = "https://github.com/onelenyk/heare/releases/download/v0.9.4"


def _said(**over):
    return {"kind": "wheels", "version": "0.9.4", "install": f"{R}/install.sh",
            "sums": f"{R}/SHA256SUMS.txt", "from": R, **over}


def test_only_this_repositorys_release_is_taken():
    assert wanted(_said()) == {"version": "0.9.4", "install": f"{R}/install.sh",
                               "sums": f"{R}/SHA256SUMS.txt", "from": R}
    assert wanted({}) is None
    assert wanted(_said(kind="dmg")) is None
    assert wanted(_said(install="https://evil.example/install.sh")) is None
    assert wanted(_said(sums="https://evil.example/SHA256SUMS.txt")) is None
    assert wanted(_said(version="0.9.4; rm -rf ~")) is None
    # The script must be the named version's, from the same place as the wheels.
    assert wanted(_said(install=R.replace("v0.9.4", "v0.9.0") + "/install.sh")) is None


def test_the_script_runs_in_a_unit_of_its_own_where_systemd_is(tmp_path):
    script = tmp_path / "install.sh"
    run = command(script, R, "0.9.4", unit_ok=True)
    assert run[:2] == ["systemd-run", "--user"] and run[-6:] == [
        "sh", str(script), "--from", R, "--version", "0.9.4"]
    assert command(script, R, "0.9.4", unit_ok=False)[0] == "sh"


def test_the_sum_is_read_for_its_file_only():
    sums = f"{'a' * 64}  install.sh\n{'b' * 64}  heare_core-0.9.4-py3-none-any.whl\n"
    assert sum_for(sums, "install.sh") == "a" * 64
    assert sum_for(sums, "other.sh") is None


class _Web:
    """httpx.AsyncClient in a box: what the brain and GitHub answer."""

    def __init__(self, files, said):
        self.files, self.said, self.posted = files, said, []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **k):
        import httpx
        if url.endswith("/health"):
            return httpx.Response(200, request=httpx.Request("GET", url))
        if url.endswith("/api/update/mine"):
            return httpx.Response(200, json=self.said, request=httpx.Request("GET", url))
        return httpx.Response(200, content=self.files[url], request=httpx.Request("GET", url))

    async def post(self, url, json=None, **k):
        import httpx
        self.posted.append(json)
        return httpx.Response(200, request=httpx.Request("POST", url))


class _Settings:
    base = "http://127.0.0.1:9790"
    secret = "t"


def _box(monkeypatch, tmp_path, script=b"echo hi\n", sum_of=None):
    files = {f"{R}/install.sh": script,
             f"{R}/SHA256SUMS.txt": f"{sum_of or hashlib.sha256(script).hexdigest()}  install.sh\n".encode()}
    web = _Web(files, _said())
    monkeypatch.setattr(updating.httpx, "AsyncClient", web)
    ran = []

    async def run(*args, **k):
        ran.append(args)

        class P:
            returncode = 0

            async def communicate(self):
                return b"", b""
        return P()
    monkeypatch.setattr(updating.asyncio, "create_subprocess_exec", run)
    monkeypatch.setattr(updating.shutil, "which", lambda name: None)
    monkeypatch.setattr(part, "installed", lambda: "0.9.3")
    return web, ran, Updating(_Settings(), tmp_path)


async def test_a_checked_script_runs_and_leaves_a_mark(monkeypatch, tmp_path):
    web, ran, up = _box(monkeypatch, tmp_path)
    await up.once()
    assert ran and ran[0][:2] == ("sh", str(tmp_path / "cache" / "update" / "install-0.9.4.sh"))
    mark = json.loads(up.mark.read_text())
    assert mark["to"] == "0.9.4" and mark["back"] == "0.9.3"


async def test_a_script_that_does_not_match_its_sum_never_runs(monkeypatch, tmp_path):
    web, ran, up = _box(monkeypatch, tmp_path, sum_of="0" * 64)
    await up.once()
    assert ran == [] and not up.mark.exists()
    assert len(web.posted) == 1 and web.posted[0]["why"]


async def test_after_the_update_the_new_version_answering_is_done(monkeypatch, tmp_path):
    web, ran, up = _box(monkeypatch, tmp_path)
    up.folder.mkdir(parents=True)
    up.mark.write_text(json.dumps({"to": "0.9.3", "back": "0.9.2"}))
    await up.settle()
    assert ran == [] and not up.mark.exists() and web.posted == []


async def test_a_version_that_did_not_come_in_puts_the_previous_back(monkeypatch, tmp_path):
    web, ran, up = _box(monkeypatch, tmp_path)
    up.folder.mkdir(parents=True)
    up.mark.write_text(json.dumps({"to": "0.9.4", "back": "0.9.2"}))
    web.files = {k.replace("v0.9.4", "v0.9.2"): v for k, v in web.files.items()}
    await up.settle()
    assert ran and "0.9.2" in ran[0]
    assert json.loads(up.mark.read_text())["rolled_back"] is True


async def test_a_rollback_is_said_once_the_old_keeper_is_back(monkeypatch, tmp_path):
    web, ran, up = _box(monkeypatch, tmp_path)
    up.folder.mkdir(parents=True)
    up.mark.write_text(json.dumps({"rolled_back": True, "why": "the brain did not answer after the update"}))
    await up.settle()
    assert web.posted == [{"why": "the brain did not answer after the update"}] and not up.mark.exists()


def test_a_mac_is_left_to_its_app(monkeypatch, tmp_path):
    monkeypatch.setattr(updating.sys, "platform", "darwin")
    assert not Updating(_Settings(), tmp_path).here
    monkeypatch.setattr(updating.sys, "platform", "linux")
    monkeypatch.setenv("HEARE_PARENT_PID", "1")
    assert not Updating(_Settings(), tmp_path).here
    monkeypatch.delenv("HEARE_PARENT_PID")
    assert Updating(_Settings(), tmp_path).here
