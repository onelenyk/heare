"""The hands body: the agent that does the work, connected over the door.

    uv run python -m heare.hands

A body, not the brain: it holds no memory and decides nothing. It claims a
piece of work, drives the agent CLI through it, asks about changes, and
returns what came of it. Stop it and the brain keeps running.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

from heare.config import Settings
from heare.reach.agents import Agent, installed


class RemoteDesk:
    """What the agent asks the brain for, over the door."""

    def __init__(self, client: httpx.AsyncClient, base: str, job_id: int) -> None:
        self._client, self._base, self._id = client, base, job_id

    def step(self, text: str) -> None:
        """What it is doing — said without waiting: a step is not an answer."""
        asyncio.create_task(self._client.post(
            f"{self._base}/api/executor/{self._id}/step", json={"text": text}))

    async def permit(self, what: str, kind: str = "change") -> bool:
        response = await self._client.post(
            f"{self._base}/api/executor/{self._id}/permit",
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
    agent = Agent(known, cwd=settings.executor_cwd or None)
    headers = {"X-Heare-Token": secret}
    async with httpx.AsyncClient(headers=headers, timeout=None) as client:
        await client.post(f"{base}/api/bodies", json={
            "channel": agent.name, "forms": ["executor"], "about": agent.about})
        print(f"руки: {agent.name}", file=sys.stderr)
        while True:
            claim = await client.post(f"{base}/api/executor/claim",
                                      json={"name": agent.name})
            task = claim.json() if claim.status_code == 200 else {}
            if not task or "id" not in task:
                continue
            desk = RemoteDesk(client, base, task["id"])
            status, result = "done", ""
            try:
                result = await agent.run(task["request"], desk)
            except asyncio.CancelledError:
                status, result = "stopped", "Зупинено."
            except Exception as exc:  # noqa: BLE001 — the brain is told, not crashed
                status, result = "failed", str(exc)
            await client.post(f"{base}/api/executor/{task['id']}/result",
                              json={"status": status, "result": result})


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
