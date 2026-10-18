"""The hands body: the agent that does the work, connected over the door.

    uv run python -m heare.hands

A body, not the brain: it holds no memory and decides nothing. It claims a
piece of work, drives the agent CLI through it, asks about changes, and
returns what came of it. Stop it and the brain keeps running.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import logging
import socket
import sys
from pathlib import Path

import httpx

from heare import part
from heare.config import Settings
from heare.reach.agents import Agent, installed


def _short(value: object, width: int = 70) -> str:
    text = " ".join(str(value).split())
    return text[:width] + "…" if len(text) > width else text


def _line(direction: str, msg: dict) -> str:
    """One message of the ACP wire, as a person reads it."""
    arrow = "→" if direction == "out" else "←"
    method = msg.get("method")
    if method == "session/update":
        update = (msg.get("params") or {}).get("update") or {}
        kind = str(update.get("sessionUpdate") or "?")
        detail = (update.get("title")
                  or (update.get("content") or {}).get("text") or "")
        return f"{arrow} {kind}" + (f" · {_short(detail)}" if detail else "")
    if method == "session/prompt":
        blocks = (msg.get("params") or {}).get("prompt") or [{}]
        text = _short(blocks[0].get("text", ""))
        return f"{arrow} prompt" + (f" · {text}" if text else "")
    if method:
        return f"{arrow} {method}"
    if "error" in msg:
        error = msg["error"]
        detail = error.get("message") if isinstance(error, dict) else error
        return f"{arrow} error · {_short(detail)}"
    return f"{arrow} ok #{msg.get('id')}"


class RemoteDesk:
    """What the agent asks the brain for, over the door."""

    def __init__(self, client: httpx.AsyncClient, base: str, job_id: int,
                 name: str) -> None:
        self._client, self._base = client, base
        self._id, self._name = job_id, name

    def step(self, text: str) -> None:
        """What it is doing — said without waiting: a step is not an answer."""
        asyncio.create_task(self._client.post(
            f"{self._base}/api/executor/{self._id}/step?name={self._name}",
            json={"text": text}))

    async def permit(self, what: str, kind: str = "change") -> bool:
        response = await self._client.post(
            f"{self._base}/api/executor/{self._id}/permit?name={self._name}",
            json={"what": what, "kind": kind})
        answer = response.json()
        if answer.get("stopped"):
            raise asyncio.CancelledError
        return bool(answer.get("allow"))


async def run() -> int:
    settings = Settings()
    base = f"http://127.0.0.1:{settings.api_port}"
    try:
        secret = Path(settings.api_token_file).read_text(encoding="utf-8").strip()
    except OSError:
        print("немає ключа — мозок запущений?", file=sys.stderr)
        return 1
    wanted = str(settings.executor or "opencode")
    known = next((k for k in installed() if k.name == wanted), None)
    if known is None:
        print(f"агента {wanted} немає на PATH", file=sys.stderr)
        return 1
    wire: collections.deque[str] = collections.deque(maxlen=40)
    told = 0

    def tap(direction: str, message: dict) -> None:
        nonlocal told
        line = _line(direction, message)
        if line:
            wire.append(line)
            told += 1

    agent = Agent(known, cwd=settings.executor_cwd or None, tap=tap)
    ui = [
        {"name": "taking", "kind": "toggle", "label": "Брати справи",
         "value": True},
        {"name": "agent", "kind": "label", "label": "Агент", "value": agent.name},
        {"name": "work", "kind": "label", "label": "Стан", "value": "чекає"},
        {"name": "acp", "kind": "log", "label": "ACP", "value": ""},
    ]
    values = {"taking": True, "agent": agent.name, "work": "чекає", "acp": ""}
    headers = {"X-Heare-Token": secret}
    async with httpx.AsyncClient(headers=headers, timeout=None) as client:
        host = socket.gethostname()
        print("стучу в мозок… (нова частина чекає на одобрення на сторінці)",
              file=sys.stderr)
        registration = await client.post(f"{base}/api/bodies", json={
            "channel": agent.name, "forms": ["executor"], "about": agent.about,
            "key": f"{agent.name}@{host}", "host": host, "ui": ui})
        answer = registration.json()
        if not answer.get("approved"):
            print(f"мозок не одобрив: {answer.get('why') or 'відхилено'}",
                  file=sys.stderr)
            return 1
        print(f"руки: {agent.name}", file=sys.stderr)

        def on_control(event: dict) -> None:
            if event.get("kind") == "control" and event.get("name") == "taking":
                values["taking"] = bool(event.get("value"))
                asyncio.create_task(
                    part.report(client, base, agent.name, values))

        await part.report(client, base, agent.name, values)
        following = asyncio.create_task(
            part.follow(base, secret, agent.name, on_control))
        sent = 0

        async def report_wire() -> None:
            nonlocal sent
            while True:
                await asyncio.sleep(0.4)
                if told != sent:
                    sent = told
                    values["acp"] = "\n".join(wire)
                    await part.report(client, base, agent.name, values)

        wiring = asyncio.create_task(report_wire())
        try:
            while True:
                if not values["taking"]:
                    await asyncio.sleep(1.0)
                    continue
                claim = await client.post(f"{base}/api/executor/claim",
                                          json={"name": agent.name})
                task = claim.json() if claim.status_code == 200 else {}
                if not task or "id" not in task:
                    continue
                print(f"взято {task['id']}: {task['request'][:60]}",
                      file=sys.stderr)
                values["work"] = f"роблю #{task['id']}"
                await part.report(client, base, agent.name, values)
                desk = RemoteDesk(client, base, task["id"], agent.name)
                status, result = "done", ""
                try:
                    result = await agent.run(task["request"], desk)
                except asyncio.CancelledError:
                    status, result = "stopped", "Зупинено."
                except Exception as exc:  # noqa: BLE001 — the brain is told, not crashed
                    status, result = "failed", str(exc)
                print(f"готово {task['id']}: {status} {result[:60]}",
                      file=sys.stderr)
                await client.post(
                    f"{base}/api/executor/{task['id']}/result?name={agent.name}",
                    json={"status": status, "result": result})
                values["work"] = "чекає"
                await part.report(client, base, agent.name, values)
        finally:
            following.cancel()
            wiring.cancel()
            await asyncio.gather(following, wiring, return_exceptions=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.hands")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
