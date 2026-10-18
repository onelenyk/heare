"""Live end-to-end against the running app — not part of `make test`.

    uv run python tests/heare/e2e_live.py

Talks to the real brain, the real parts and the real agent: costs a few
cents and a few minutes. One line per scenario, exit code 1 if any failed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("HEARE_URL", "http://127.0.0.1:9790")
TOKEN_FILE = Path(os.environ.get(
    "HEARE_TOKEN_FILE", Path.home() / ".heare" / "api_token"))
HEAD = {"X-Heare-Token": TOKEN_FILE.read_text(encoding="utf-8").strip()}


async def state(client: httpx.AsyncClient) -> dict:
    return (await client.get(f"{BASE}/api/state", headers=HEAD)).json()


async def wait_for(check, timeout: float, step: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await check()
        if value:
            return value
        await asyncio.sleep(step)
    return None


async def health(client):
    s = await state(client)
    parts = {b["channel"]: b["state"] for b in s["bodies"].values()}
    ok = parts.get("eyes") == "approved" and parts.get("opencode") == "approved"
    return ok, f"parts {parts}"


async def chat(client):
    said = "Скажи одне слово: тест."
    await client.post(f"{BASE}/api/message", json={"text": said}, headers=HEAD)

    async def replied():
        # The last turn, not the length: history?n=5 is a page, so once the
        # history is longer than that the length never grows again.
        past = (await client.get(f"{BASE}/api/history?n=5", headers=HEAD)).json()
        last = past[-1] if past else {}
        if last.get("user") == said and last.get("agent"):
            return last["agent"]

    text = await wait_for(replied, 60)
    return bool(text), (text or "no reply")[:70]


async def control(client):
    s = await state(client)
    pid = next((k for k, b in s["bodies"].items()
                if b["channel"] == "eyes" and b["state"] == "approved"), None)
    if pid is None:
        return False, "no eyes"

    async def looking(want: bool):
        s = await state(client)
        body = s["bodies"].get(pid) or {}
        return (body.get("values") or {}).get("looking") is want

    await client.post(f"{BASE}/api/controls", headers=HEAD,
                      json={"part": pid, "name": "looking", "value": False})
    off = await wait_for(lambda: looking(False), 10, 0.3)
    await client.post(f"{BASE}/api/controls", headers=HEAD,
                      json={"part": pid, "name": "looking", "value": True})
    on = await wait_for(lambda: looking(True), 10, 0.3)
    return bool(off and on), "looking false → true"


async def forget(client):
    registration = asyncio.create_task(client.post(
        f"{BASE}/api/bodies", headers=HEAD, json={
            "channel": "probe", "forms": ["input"], "about": "e2e probe",
            "key": "probe@e2e", "host": "e2e"}))
    await asyncio.sleep(0.6)
    s = await state(client)
    pending = (s["bodies"].get("probe@e2e") or {}).get("state") == "pending"
    await client.post(f"{BASE}/api/bodies/probe@e2e/approve", headers=HEAD)
    approved = (await registration).json().get("approved") is True
    await client.post(f"{BASE}/api/bodies/probe@e2e/forget", headers=HEAD)
    s = await state(client)
    gone = "probe@e2e" not in s["bodies"]
    return bool(pending and approved and gone), "pending → approved → forgotten"


async def task(client, allow: dict):
    path = f"/tmp/heare-e2e-{int(time.time())}.txt"
    await client.post(f"{BASE}/api/message", headers=HEAD, json={
        "text": f"Доручи агенту створити файл {path} з текстом e2e. "
                f"Скажи одним реченням, коли готово."})

    async def finished():
        s = await state(client)
        for item in s["tasks"]:
            if path in item["request"] and item["status"] != "running":
                return item

    item = await wait_for(finished, 300, 2)
    await asyncio.sleep(1)
    written = Path(path).exists()
    if written:
        Path(path).unlink(missing_ok=True)
    return bool(item and item["status"] == "done" and written), (
        f"task {item and item['status']}, file {'yes' if written else 'no'}, "
        f"permissions answered {allow['n']}")


async def ask(client):
    folder = f"/tmp/heare-e2e-ask-{int(time.time())}"
    await client.post(f"{BASE}/api/message", headers=HEAD, json={
        "text": f"Доручи агенту створити файл у теці {folder}, але ім'я файлу "
                f"йому не кажи — він має спитати мене, як його назвати."})

    async def asked():
        s = await state(client)
        return s.get("question")

    question = await wait_for(asked, 300, 2)
    if not question:
        return False, "the agent did not ask"
    await client.post(f"{BASE}/api/answer/{question['id']}", headers=HEAD,
                      json={"text": "answer.txt"})
    return True, f"asked: {question['text'][:70]}"


async def allow_permissions(client, stop: asyncio.Event, seen: dict):
    while not stop.is_set():
        try:
            s = await state(client)
            permission = s.get("permission")
            if permission:
                seen["n"] += 1
                await client.post(f"{BASE}/api/permission/{permission['id']}",
                                  headers=HEAD, json={"allow": True})
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1)


async def main() -> int:
    parser = argparse.ArgumentParser(description="live e2e against a running heare")
    parser.add_argument("--only", default="",
                        help="comma-separated: health,chat,control,forget,task,ask")
    args = parser.parse_args()
    allow = {"n": 0}
    results: list[tuple[str, bool, str]] = []
    async with httpx.AsyncClient(timeout=None) as client:
        stop = asyncio.Event()
        watcher = asyncio.create_task(allow_permissions(client, stop, allow))
        scenarios = {
            "health": lambda: health(client),
            "chat": lambda: chat(client),
            "control": lambda: control(client),
            "forget": lambda: forget(client),
            "task": lambda: task(client, allow),
            "ask": lambda: ask(client),
        }
        wanted = args.only.split(",") if args.only else list(scenarios)
        picked = [(name, scenarios[name]) for name in wanted if name in scenarios]
        try:
            for name, run in picked:
                try:
                    ok, detail = await run()
                except Exception as exc:  # noqa: BLE001 — a crash is a failed scenario
                    ok, detail = False, f"{type(exc).__name__}: {exc}"
                results.append((name, ok, detail))
                print(f"{'PASS' if ok else 'FAIL'}  {name:8s}  {detail}",
                      flush=True)
        finally:
            stop.set()
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            await client.post(f"{BASE}/api/stop", headers=HEAD)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
