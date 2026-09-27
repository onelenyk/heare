"""Talk to an ACP agent in the terminal — the three verbs without a voice.

    uv run python experiments/acp/try.py claude            # you type
    uv run python experiments/acp/try.py claude --model haiku
    uv run python experiments/acp/try.py opencode
    uv run python experiments/acp/try.py claude --bench    # measure, no typing

In the terminal:

    ask <питання>    wait for the answer, streamed as it is written
    do <доручення>   hand it off; the prompt comes back at once
    jobs             what is running and what finished
    stop <n>         cancel job n
    yes <n> / no <n> answer permission question n
    q                quit

``--bench`` runs a fixed script and prints the table the README asks for.
It never allows a change: every permission it is asked is answered by a
policy that lets reading through and rejects the rest.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
import tempfile
import time
from pathlib import Path

from acp import Agent, Event, Session, describe

HERE = Path(__file__).parent
LOGS = HERE / "logs"


def say(line: str) -> None:
    print(line, flush=True)


# -- you type -----------------------------------------------------------------


class Terminal:
    def __init__(self) -> None:
        self.jobs: dict[int, Session] = {}
        self.questions: dict[int, asyncio.Future] = {}
        self._n = 0
        self._q = 0

    async def decide(self, session: Session, request: dict) -> str:
        self._q += 1
        q = self._q
        fut = asyncio.get_running_loop().create_future()
        self.questions[q] = fut
        who = next((f"#{n}" for n, s in self.jobs.items() if s is session), "ask")
        say(f"\n  [?{q}] {who} хоче: {describe(request)}   →  yes {q} / no {q}")
        try:
            return await fut
        finally:
            self.questions.pop(q, None)

    def narrate(self, tag: str):
        def on(ev: Event) -> None:
            if ev.kind == "text" and tag == "ask":
                print(ev.text, end="", flush=True)
            elif ev.kind == "tool":
                say(f"\n  [{tag} {ev.t:4.1f}s] → {ev.text}")

        return on

    async def run(self, agent: Agent) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        say("ask … · do … · jobs · stop n · yes n · no n · q")
        while True:
            print("\n> ", end="", flush=True)
            line = (await reader.readline()).decode().strip()
            if not line:
                if reader.at_eof():
                    return
                continue
            verb, _, rest = line.partition(" ")
            if verb == "q":
                return
            if verb == "ask" and rest:
                s = await agent.ask(rest, self.narrate("ask"))
                say(f"\n  ({timings(s)})")
            elif verb == "do" and rest:
                self._n += 1
                n = self._n
                s = await agent.do(rest, self.narrate(f"#{n}"))
                self.jobs[n] = s
                s.task.add_done_callback(lambda _t, n=n, s=s: self.finished(n, s))
                say(f"  #{n} взяв")
            elif verb == "jobs":
                for n, s in self.jobs.items():
                    say(f"  #{n} {s.status:9} {s.text[-80:]!r}")
            elif verb == "stop" and rest.isdigit() and int(rest) in self.jobs:
                await self.jobs[int(rest)].cancel()
            elif (
                verb in ("yes", "no") and rest.isdigit() and int(rest) in self.questions
            ):
                self.questions[int(rest)].set_result(
                    "allow" if verb == "yes" else "reject"
                )
            else:
                say("  ?")

    def finished(self, n: int, s: Session) -> None:
        say(f"\n  #{n} {s.status}: {s.text.strip()[-300:]}\n  ({timings(s)})")


def timings(s: Session) -> str:
    f = lambda x: "—" if x is None else f"{x:.1f}s"  # noqa: E731
    return (
        f"перший текст {f(s.first_text)} · перше речення {f(s.first_sentence)}"
        f" · усього {f(s.total)} · {s.stop_reason}"
    )


# -- measured, no typing -----------------------------------------------------

# What the bench lets through. Reading is harmless; the three commands are
# the ones "що жере ресурси" needs. Anything else is refused, so the
# bench can never change the machine it runs on.
READ_KINDS = {"read", "search", "fetch", "think"}
READ_COMMANDS = {"ps", "top", "uptime", "vm_stat", "memory_pressure", "df", "sysctl"}


async def bench_decide(session: Session, request: dict) -> str:
    call = request.get("toolCall") or {}
    command = (call.get("rawInput") or {}).get("command") or ""
    first = shlex.split(command)[0] if command.strip() else ""
    verdict = (
        "allow"
        if call.get("kind") in READ_KINDS or Path(first).name in READ_COMMANDS
        else "reject"
    )
    session.events.append(Event("permission", f"{verdict}: {describe(request)}", 0))
    say(f"    дозвіл: {describe(request)} → {verdict}")
    return verdict


async def bench(agent: Agent, model: str | None) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []

    async def ask(label: str, text: str) -> None:
        say(f"\n— {label}: {text}")
        s = await agent.ask(text)
        say(f"  «{s.text.strip()[:300]}»\n  {timings(s)}")
        rows.append((label, timings(s)))

    if model:
        first = await agent.session()
        agent._ask_session = first
        ok = await first.set("model", model)
        say(f"модель {model}: {'так' if ok else 'агент такої не пропонує'}")

    await ask("ask холодний", "Котра зараз година?")
    await ask("ask теплий", "А який сьогодні день тижня?")
    await ask("ask з інструментом", "Що зараз найбільше навантажує мій комп'ютер?")

    say("\n— do (зміна, бенч відмовить) + паралельно ask")
    job = await agent.do("Створи в поточній теці файл hello.txt зі словом привіт.")
    s = await agent.ask("Скільки буде сім помножити на вісім?")
    say(f"  ask під час do: «{s.text.strip()[:120]}» {timings(s)}; do ще {job.status}")
    rows.append(("ask під час do", timings(s)))
    await job.task
    said = [e.text for e in job.events if e.kind == "permission"]
    say(
        f"  do: «{job.text.strip()[:200]}»\n  {timings(job)}; дозволи: {said or 'не питав'}"
    )
    rows.append(("do з відмовою", timings(job)))
    created = (Path(agent.cwd) / "hello.txt").exists()
    rows.append(("файл створено попри відмову?", "так ✗" if created else "ні ✓"))

    say("\n— стоп посеред довгого do")
    job = await agent.do("Напиши оповідання на дві тисячі слів про маяк.")
    await asyncio.sleep(4)
    t0 = time.monotonic()
    await job.cancel()
    try:
        await asyncio.wait_for(job.task, 30)
    except TimeoutError:
        pass
    rows.append(
        ("стоп → зупинився за", f"{time.monotonic() - t0:.1f}s, {job.stop_reason}")
    )
    say(f"  {rows[-1][1]}")
    return rows


def table(agent: Agent, model: str | None, rows: list) -> str:
    out = [
        f"\n{agent.name}{' / ' + model if model else ''} · запуск {agent.startup:.1f}s"
    ]
    for label, v in rows:
        out.append(f"  {label:28} {v}")
    return "\n".join(out)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", choices=["claude", "opencode"])
    ap.add_argument("--model")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument(
        "--cwd", help="where the agent works (default: home, bench: a temp dir)"
    )
    a = ap.parse_args()

    LOGS.mkdir(exist_ok=True)
    term = Terminal()
    cwd = a.cwd or (tempfile.mkdtemp(prefix="acp-bench-") if a.bench else None)
    agent = Agent(
        a.agent,
        on_permission=bench_decide if a.bench else term.decide,
        cwd=cwd,
        stderr=LOGS / f"{a.agent}.stderr",
    )
    await agent.start()
    info = agent.info.get("agentInfo") or {}
    say(
        f"{info.get('title') or info.get('name')} {info.get('version', '')} · {agent.startup:.1f}s · {agent.cwd}"
    )
    try:
        if a.bench:
            rows = await bench(agent, a.model)
            say(table(agent, a.model, rows))
        else:
            if a.model:
                s = await agent.session()
                agent._ask_session = s
                say(
                    f"модель {a.model}: {'так' if await s.set('model', a.model) else 'немає'}"
                )
            await term.run(agent)
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
