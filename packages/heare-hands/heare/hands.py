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
import json
import logging
import os
import socket
import sys
from pathlib import Path

import httpx

from heare import part, runner
from heare.config import Settings
from heare.reach.agents import KNOWN as AGENTS, Agent


def _short(value: object, width: int = 70) -> str:
    text = " ".join(str(value).split())
    return text[:width] + "…" if len(text) > width else text


def _price(cost: float, used: int, size: int) -> str:
    """What the last turn cost, and how much of the window it used."""
    if not cost and not used:
        return ""
    window = f"{size // 1_000_000}M" if size >= 1_000_000 else f"{size // 1000}k"
    tokens = f"{used // 1000}k" if used >= 1000 else str(used)
    return f"${cost:.4f} · {tokens} / {window}"


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

    async def ask(self, text: str) -> str:
        """The agent's question: the person's words come back as the answer."""
        response = await self._client.post(
            f"{self._base}/api/executor/{self._id}/ask?name={self._name}",
            json={"question": text})
        answer = response.json()
        if answer.get("stopped"):
            raise asyncio.CancelledError
        return str(answer.get("answer") or "")

    async def permit(self, what: str, kind: str = "change") -> bool:
        response = await self._client.post(
            f"{self._base}/api/executor/{self._id}/permit?name={self._name}",
            json={"what": what, "kind": kind})
        answer = response.json()
        if answer.get("stopped"):
            raise asyncio.CancelledError
        return bool(answer.get("allow"))


def _agent(settings: Settings, tap, mcp_servers: list[dict]) -> Agent | None:
    kind = str(settings.executor or "opencode").strip()
    entry = runner.KNOWN.get(kind)
    if entry is None:
        print(f"невідомий агент {kind}; відомі: {', '.join(runner.KNOWN)}",
              file=sys.stderr)
        return None

    setting = str(settings.executor_binary or "").strip()
    resolved = runner.resolve(setting or entry["binary"])
    if resolved is None:
        print(f"агента {kind} немає: {setting or entry['binary']} не знайдено "
              "— задай шлях у налаштуваннях", file=sys.stderr)
        return None

    # opencode is its own ACP server. Claude speaks ACP through an adapter
    # fetched by npx, so the binary named for detection and sign-in is not
    # the process we start: npx is.
    launch = resolved
    if entry["command"][0] != entry["binary"]:
        launch = runner.resolve(entry["command"][0])
        if launch is None:
            print(f"для {kind} не знайдено {entry['command'][0]}",
                  file=sys.stderr)
            return None
    command = [launch] + list(entry["command"][1:])

    model = str(settings.executor_model or "").strip()
    auth = str(settings.executor_auth or "login").strip()
    auth_env = str(settings.executor_auth_env or "").strip()
    base_url = str(settings.executor_base_url or "").strip()
    env = runner.env(kind, auth, auth_env, base_url, model)
    patch = runner.opencode_config_patch(kind, base_url, model)

    known = next((k for k in AGENTS if k.name == kind), None)
    if known is None:
        print(f"агента {kind} немає в таблиці", file=sys.stderr)
        return None
    return Agent(
        known,
        cwd=settings.executor_cwd or None,
        command=command,
        env=env,
        model=model or None,
        config_patch=patch or None,
        tap=tap,
        mcp_servers=mcp_servers,
    )


async def run() -> int:
    settings = Settings()
    base = settings.base
    try:
        secret = settings.secret
    except OSError:
        print("немає ключа — мозок запущений?", file=sys.stderr)
        return 1
    session_file = Path(settings.parts_file).parent / "agent-session.json"
    try:
        resume = session_file.read_text(encoding="utf-8").strip()
    except OSError:
        resume = ""

    wire: collections.deque[str] = collections.deque(maxlen=40)
    usage = {"cost": 0.0, "used": 0, "size": 0}
    told = 0

    def tap(direction: str, message: dict) -> None:
        nonlocal told
        update = ((message.get("params") or {}).get("update") or {}
                  if message.get("method") == "session/update" else {})
        if update.get("sessionUpdate") == "usage_update":
            cost = update.get("cost") or {}
            usage["cost"] = float(cost.get("amount") or 0.0)
            usage["used"] = int(update.get("used") or 0)
            usage["size"] = int(update.get("size") or 0)
            told += 1
        line = _line(direction, message)
        if line:
            wire.append(line)
            told += 1

    socket_path = Path(settings.parts_file).parent / f"hands-{os.getpid()}.sock"
    try:
        socket_path.unlink()
    except OSError:
        pass
    current: dict = {"desk": None}

    async def handle(reader, writer):
        try:
            line = await reader.readline()
            try:
                request = json.loads(line or b"{}")
            except ValueError:
                request = {}
            desk = current.get("desk")
            answer = ""
            if request.get("tool") == "ask" and desk is not None:
                answer = await desk.ask(str(request.get("question") or ""))
            writer.write((json.dumps({"answer": answer},
                                     ensure_ascii=False) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    mcp_servers = [{"name": "heare", "command": sys.executable,
                    "args": ["-m", "heare.reach.bridge",
                             "--socket", str(socket_path)],
                    "env": []}]
    agent = _agent(settings, tap, mcp_servers)
    if agent is None:
        return 1
    asks = await asyncio.start_unix_server(handle, path=str(socket_path))
    ui = [
        {"name": "taking", "kind": "toggle", "label": "Брати справи",
         "value": True},
        {"name": "agent", "kind": "label", "label": "Агент", "value": agent.name},
        {"name": "work", "kind": "label", "label": "Стан", "value": "чекає"},
        {"name": "usage", "kind": "label", "label": "Ціна", "value": "—"},
        {"name": "acp", "kind": "log", "label": "ACP", "value": ""},
    ]
    values = {"taking": True, "agent": agent.name, "work": "чекає",
              "usage": "—", "acp": ""}
    headers = {"X-Heare-Token": secret}
    async with httpx.AsyncClient(headers=headers, timeout=None) as client:
        host = socket.gethostname()
        print("стучу в мозок… (нова частина чекає на одобрення на сторінці)",
              file=sys.stderr)
        def knock():
            # The channel is the agent's name. Switching the runner type
            # therefore changes the channel, while the away card in the
            # manifest still says "opencode"; the manifest is the lead's
            # decision, not this part's.
            return client.post(f"{base}/api/bodies", json={
                "channel": agent.name, "forms": ["executor"],
                "about": agent.about, "key": f"{agent.name}@{host}",
                "host": host, "ui": ui})

        registration = await knock()
        answer = registration.json()
        if not answer.get("approved"):
            print(f"мозок не одобрив: {answer.get('why') or 'відхилено'}",
                  file=sys.stderr)
            return 1
        if answer.get("token"):
            secret = answer["token"]
            client.headers["X-Heare-Token"] = secret
        print(f"руки: {agent.name}", file=sys.stderr)

        def on_control(event: dict) -> None:
            if event.get("kind") == "control" and event.get("name") == "taking":
                values["taking"] = bool(event.get("value"))
                asyncio.create_task(
                    part.report(client, base, agent.name, values))

        await part.report(client, base, agent.name, values)
        following = asyncio.create_task(
            part.follow(base, secret, agent.name, on_control,
                        on_open=lambda: asyncio.create_task(knock())))
        sent = 0

        async def report_wire() -> None:
            nonlocal sent
            while True:
                await asyncio.sleep(0.4)
                if told != sent:
                    sent = told
                    values["acp"] = "\n".join(wire)
                    values["usage"] = _price(**usage) or values["usage"]
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
                values["work"] = f"роблю: {task['request'][:50]}"
                await part.report(client, base, agent.name, values)
                desk = RemoteDesk(client, base, task["id"], agent.name)
                status, result = "done", ""
                current["desk"] = desk
                try:
                    result = await agent.run(task["request"], desk, resume=resume)
                except asyncio.CancelledError:
                    status, result = "stopped", "Зупинено."
                except Exception as exc:  # noqa: BLE001 — the brain is told, not crashed
                    status, result = "failed", str(exc)
                resume = ""
                print(f"готово {task['id']}: {status} {result[:60]}",
                      file=sys.stderr)
                await client.post(
                    f"{base}/api/executor/{task['id']}/result?name={agent.name}",
                    json={"status": status, "result": result})
                current["desk"] = None
                if agent.last_session:
                    try:
                        session_file.write_text(agent.last_session, encoding="utf-8")
                    except OSError:
                        pass
                values["work"] = "чекає"
                await part.report(client, base, agent.name, values)
        finally:
            following.cancel()
            wiring.cancel()
            asks.close()
            await asyncio.gather(following, wiring, return_exceptions=True)
            socket_path.unlink(missing_ok=True)


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
