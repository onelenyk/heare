"""Background sub-agent manager using OpenCode serve mode.

Each agent gets its own ``opencode serve`` process on a random port.
SSE event streaming provides real-time tool visibility and permission gating.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

logger = logging.getLogger("heare.subagent_manager")


def _resolve_opencode_binary(explicit: str | None = None) -> str:
    if explicit:
        logger.info("opencode binary: using explicit path %s", explicit)
        return explicit
    candidates = [
        os.path.expanduser("~/.opencode/bin/opencode"),
        os.path.expanduser("~/.superset/bin/opencode"),
        "/Users/lenyk/.opencode/bin/opencode",
        "/Users/lenyk/.superset/bin/opencode",
        "/usr/local/bin/opencode",
        os.path.expanduser("~/.local/bin/opencode"),
        "/opt/homebrew/bin/opencode",
    ]
    for cand in candidates:
        if Path(cand).is_file():
            logger.info("opencode binary: found candidate %s", cand)
            return cand
    resolved = shutil.which("opencode")
    if resolved:
        logger.info("opencode binary: found via PATH at %s", resolved)
        return resolved
    logger.warning("opencode binary: NOT FOUND in candidates or PATH: %s", candidates)
    return "opencode"


@dataclass
class SubAgentState:
    """Live state for one background sub-agent."""

    session_id: str
    prompt: str
    cwd: str | None
    port: int

    status: Literal[
        "starting", "running", "waiting_for_input", "done", "error", "cancelled"
    ] = "starting"
    server_process: asyncio.subprocess.Process | None = None

    output_parts: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    cost: float | None = None
    tokens: dict | None = None
    tool_calls: int = 0
    current_step: str = ""
    error_message: str | None = None

    pending_permission: dict | None = None
    pending_since: float | None = None

    started_at: float = 0.0
    finished_at: float | None = None
    turn: int = 1

    _sse_task: asyncio.Task | None = field(default=None, repr=False)
    _slot_released: bool = field(default=False, repr=False)


class SubAgentManager:
    """Manages background OpenCode agents via serve mode."""

    _instance: SubAgentManager | None = None

    def __init__(self, settings: Any = None) -> None:
        self._agents: dict[str, SubAgentState] = {}
        self._settings = settings

        max_c = getattr(settings, "agent_max_concurrent", 5) if settings else 5
        ttl = getattr(settings, "agent_result_ttl_seconds", 600.0) if settings else 600.0
        start_timeout = getattr(settings, "agent_server_start_timeout", 10.0) if settings else 10.0
        port_start = getattr(settings, "agent_port_range_start", 14100) if settings else 14100
        permission_timeout = getattr(settings, "agent_permission_timeout_seconds", 120.0) if settings else 120.0

        self._max_concurrent: int = max_c
        self._ttl_seconds: float = ttl
        self._start_timeout: float = start_timeout
        self._permission_timeout: float = permission_timeout
        self._port_range_start: int = port_start
        self._port_range_end: int = port_start + 100
        self._client: httpx.AsyncClient | None = None
        self._prune_task: asyncio.Task | None = None

        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_c)

    @classmethod
    def get(cls) -> SubAgentManager | None:
        return cls._instance

    @classmethod
    def set(cls, mgr: SubAgentManager) -> None:
        cls._instance = mgr

    def start_pruner(self) -> None:
        if self._prune_task is None:
            self._prune_task = asyncio.create_task(self._prune_loop())

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._client

    def _find_agent(self, session_id: str) -> SubAgentState | None:
        agent = self._agents.get(session_id)
        if agent is not None:
            return agent
        for a in self._agents.values():
            if a.session_id == session_id:
                return a
        return None

    # -- CRUD API ---------------------------------------------------------

    async def start(self, prompt: str, *, cwd: str | None = None) -> SubAgentState:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            raise RuntimeError(f"Max {self._max_concurrent} concurrent agents")

        port = self._find_free_port()
        temp_id = f"agent-{port}"
        state = SubAgentState(session_id=temp_id, prompt=prompt, cwd=cwd, port=port, started_at=time.time())

        # Phase 1: spawn server process (fast — just exec)
        await self._spawn_server_only(state)

        self._agents[temp_id] = state
        # Phase 2: background bootstrap (health check, session creation, SSE listen)
        state._sse_task = asyncio.create_task(self._bootstrap_and_listen(state))
        return state

    def status(self, session_id: str) -> dict:
        agent = self._find_agent(session_id)
        if agent is None:
            return {"error": f"Agent not found: {session_id}"}

        parts_snapshot = list(agent.output_parts)
        result: dict[str, Any] = {
            "session_id": agent.session_id,
            "status": agent.status,
            "prompt": agent.prompt,
            "current_step": agent.current_step,
            "tool_calls": agent.tool_calls,
            "cost_so_far": agent.cost,
            "elapsed_seconds": int(time.time() - agent.started_at) if agent.started_at else 0,
            "turn": agent.turn,
        }
        if agent.status == "waiting_for_input" and agent.pending_permission:
            result["permission"] = agent.pending_permission
            if agent.pending_since:
                result["waiting_seconds"] = int(time.time() - agent.pending_since)
        if parts_snapshot:
            result["partial_output"] = "".join(parts_snapshot)[:10000]
        if agent.status in ("done", "error", "cancelled"):
            result["finished_at"] = agent.finished_at
            result["output"] = "".join(parts_snapshot)
        if agent.status == "error":
            result["error_message"] = agent.error_message
        return result

    def result(self, session_id: str) -> dict:
        agent = self._find_agent(session_id)
        if agent is None:
            return {"error": f"Agent not found: {session_id}", "success": False}
        output = "".join(agent.output_parts)
        truncated = agent.status in ("running", "starting", "waiting_for_input")
        return {
            "session_id": agent.session_id,
            "status": agent.status,
            "output": output[:100000],
            "truncated": truncated and len(output) > 100000,
            "tool_calls": agent.tool_calls,
            "cost": agent.cost,
            "events": agent.events,
            "success": agent.status == "done",
        }

    async def message(self, session_id: str, prompt: str) -> dict:
        agent = self._find_agent(session_id)
        if agent is None:
            return {"error": f"Agent not found: {session_id}", "success": False}
        if agent.status in ("running", "starting", "waiting_for_input"):
            return {"error": f"Agent is still {agent.status}. Cancel or wait.", "success": False}

        client = await self._get_client()
        url = f"http://127.0.0.1:{agent.port}/session/{agent.session_id}/prompt_async"
        try:
            resp = await client.post(url, json={"parts": [{"type": "text", "text": prompt}]})
            resp.raise_for_status()
        except Exception as e:
            return {"error": f"Failed: {e}", "success": False}

        agent.status = "running"
        agent.turn += 1
        agent.output_parts = []
        agent.events = []
        agent.cost = None
        agent.tool_calls = 0
        agent.current_step = ""
        agent.error_message = None
        agent.pending_permission = None
        agent.pending_since = None
        agent.finished_at = None

        if agent._sse_task and not agent._sse_task.done():
            agent._sse_task.cancel()
        agent._sse_task = asyncio.create_task(self._listen_sse(agent))
        return {"session_id": session_id, "status": "running", "turn": agent.turn, "success": True}

    async def cancel(self, session_id: str) -> dict:
        agent = self._find_agent(session_id)
        if agent is None:
            return {"error": f"Agent not found: {session_id}", "cancelled": False}
        was_running = agent.status in ("running", "starting", "waiting_for_input")
        client = await self._get_client()
        try:
            await client.post(f"http://127.0.0.1:{agent.port}/session/{agent.session_id}/abort")
        except Exception:
            pass
        await self._stop_server(agent)
        agent.status = "cancelled"
        agent.finished_at = time.time()
        if was_running:
            self._release_slot(agent)
        return {
            "session_id": session_id, "cancelled": True,
            "was_running": was_running,
            "partial_output": "".join(agent.output_parts)[:2000],
        }

    async def approve(self, session_id: str) -> dict:
        agent = self._find_agent(session_id)
        if agent is None or agent.status != "waiting_for_input" or not agent.pending_permission:
            return {"error": "Agent not waiting for input", "approved": False}
        req_id = agent.pending_permission.get("requestID", "")
        client = await self._get_client()
        try:
            await client.post(f"http://127.0.0.1:{agent.port}/permission/{req_id}/reply", json={"reply": "once"})
        except Exception as e:
            return {"error": f"Reply failed: {e}", "approved": False}
        agent.status = "running"
        agent.pending_permission = None
        agent.pending_since = None
        return {"session_id": session_id, "approved": True, "status": "running"}

    async def deny(self, session_id: str, reason: str | None = None) -> dict:
        agent = self._find_agent(session_id)
        if agent is None or agent.status != "waiting_for_input" or not agent.pending_permission:
            return {"error": "Agent not waiting for input", "denied": False}
        req_id = agent.pending_permission.get("requestID", "")
        client = await self._get_client()
        try:
            await client.post(f"http://127.0.0.1:{agent.port}/permission/{req_id}/reply", json={"reply": "reject"})
        except Exception as e:
            return {"error": f"Reply failed: {e}", "denied": False}
        agent.status = "running"
        agent.pending_permission = None
        agent.pending_since = None
        corrective = False
        if reason:
            try:
                await client.post(
                    f"http://127.0.0.1:{agent.port}/session/{agent.session_id}/message",
                    json={"parts": [{"type": "text", "text": f"CORRECTION: {reason}"}]},
                )
                corrective = True
            except Exception:
                pass
        return {"session_id": session_id, "denied": True, "corrective_sent": corrective, "status": "running"}

    def list_all(self) -> list[dict]:
        return [self._agent_to_dict(a) for a in self._agents.values()]

    def list_active(self) -> list[dict]:
        now = time.time()
        result = []
        for a in self._agents.values():
            if a.status in ("starting", "running", "waiting_for_input"):
                result.append(self._agent_to_dict(a))
            elif a.status in ("done", "error", "cancelled") and a.finished_at:
                if (now - a.finished_at) < self._ttl_seconds:
                    result.append(self._agent_to_dict(a))
        return result

    def _agent_to_dict(self, a: SubAgentState) -> dict:
        now = time.time()
        partial = "".join(a.output_parts)[:500] if a.output_parts else None
        return {
            "session_id": a.session_id,
            "prompt": a.prompt[:100],
            "status": a.status,
            "current_step": a.current_step,
            "tool_calls": a.tool_calls,
            "cost": a.cost,
            "age_seconds": int(now - a.started_at) if a.started_at else 0,
            "turn": a.turn,
            "partial_output": partial,
        }

    # -- Server lifecycle -------------------------------------------------

    async def _spawn_server_only(self, state: SubAgentState) -> None:
        cwd = state.cwd or os.getcwd()
        opencode_bin = _resolve_opencode_binary(
            getattr(self._settings, "opencode_binary", None) if self._settings else None
        )
        cmd = [opencode_bin, "serve", "--port", str(state.port)]
        logger.info("Spawning agent server: %s (cwd=%s)", cmd, cwd)
        try:
            state.server_process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=cwd, start_new_session=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"OpenCode binary not found at '{opencode_bin}'. "
                f"Install opencode or set opencode_binary in config.toml."
            )

    async def _bootstrap_and_listen(self, state: SubAgentState) -> None:
        try:
            await self._wait_healthy(state)
            await self._create_session_and_send(state)
            await self._listen_sse(state)
        except Exception as e:
            logger.exception("Bootstrap failed for %s: %s", state.session_id[:20], e)
            if state.status == "starting":
                state.status = "error"
                state.error_message = str(e)
                state.finished_at = time.time()
                self._release_slot(state)

    async def _wait_healthy(self, state: SubAgentState) -> None:
        deadline = time.time() + self._start_timeout
        while time.time() < deadline:
            try:
                client = await self._get_client()
                resp = await client.get(f"http://127.0.0.1:{state.port}/global/health")
                if resp.status_code == 200:
                    logger.info("Agent server healthy on port %d", state.port)
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError(f"Server on port {state.port} not healthy within {self._start_timeout}s")

    async def _create_session_and_send(self, state: SubAgentState) -> None:
        client = await self._get_client()
        base = f"http://127.0.0.1:{state.port}"
        resp = await client.post(f"{base}/session", json={"title": state.prompt[:80]})
        resp.raise_for_status()
        state.session_id = resp.json()["id"]
        resp = await client.post(
            f"{base}/session/{state.session_id}/prompt_async",
            json={"parts": [{"type": "text", "text": state.prompt}]},
        )
        resp.raise_for_status()
        state.status = "running"
        logger.info("Agent session created: %s (port=%d)", state.session_id, state.port)

    async def _listen_sse(self, state: SubAgentState) -> None:
        """Background task: subscribe to SSE events, accumulate state."""
        client = await self._get_client()
        url = f"http://127.0.0.1:{state.port}/event"
        retries = 0
        max_retries = 3

        while retries < max_retries:
            try:
                async with client.stream("GET", url, headers={"Accept": "text/event-stream"}) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue

                        state.events.append(event)
                        props = event.get("properties", event)
                        etype = event.get("type", "")

                        if etype == "message.part.updated":
                            part = props.get("part", {})
                            ptype = part.get("type", "")

                            if ptype == "tool":
                                tool_name = part.get("tool", "")
                                ts = part.get("state", {})
                                tool_status = ts.get("status", "")
                                if tool_status == "completed":
                                    state.tool_calls += 1
                                    state.current_step = ""
                                elif tool_status == "running":
                                    inp = ts.get("input", {})
                                    desc = str(inp.get("command", inp.get("description", tool_name)))
                                    state.current_step = f"{tool_name}: {desc[:100]}"
                                elif tool_status == "pending":
                                    state.current_step = f"{tool_name}: pending..."

                            elif ptype == "step-finish":
                                p_cost = part.get("cost")
                                p_tokens = part.get("tokens")
                                if p_cost is not None:
                                    state.cost = (state.cost or 0.0) + float(p_cost)
                                if p_tokens:
                                    state.tokens = p_tokens

                            elif ptype in ("text",):
                                text = part.get("text", "")
                                if text:
                                    state.output_parts.append(str(text))

                        elif etype == "message.part.delta":
                            delta = props.get("delta", "")
                            if delta:
                                state.output_parts.append(str(delta))

                        elif etype == "permission.asked":
                            state.status = "waiting_for_input"
                            state.pending_permission = {
                                "requestID": props.get("requestID", ""),
                                "tool": props.get("tool", ""),
                                "input": props.get("input", {}),
                            }
                            state.pending_since = time.time()
                            logger.info("Agent %s waiting: %s", state.session_id[:20], props.get("tool"))

                        elif etype == "waiting_for_input":
                            state.status = "waiting_for_input"
                            state.pending_since = time.time()
                            logger.info("Agent %s waiting_for_input event", state.session_id[:20])

                        elif etype in ("session.status", "session.idle"):
                            stype = "idle" if etype == "session.idle" else props.get("status", {}).get("type", "")
                            if stype == "idle" and state.status == "running":
                                state.status = "done"
                                state.finished_at = time.time()
                                self._release_slot(state)
                                logger.info("Agent %s finished (%s)", state.session_id[:20], etype)

                # Stream ended naturally
                if state.status == "running":
                    state.status = "done"
                    state.finished_at = time.time()
                    self._release_slot(state)
                return

            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
                retries += 1
                logger.warning("SSE connection failed (%d/%d): %s", retries, max_retries, e)
                if retries < max_retries:
                    await asyncio.sleep(1.0 * retries)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.exception("SSE listener error for %s", state.session_id[:20])
                retries += 1
                if retries < max_retries:
                    await asyncio.sleep(1.0)

        if state.status in ("running", "starting", "waiting_for_input"):
            state.status = "error"
            state.error_message = "SSE connection lost after max retries"
            state.finished_at = time.time()
            self._release_slot(state)

    async def _stop_server(self, state: SubAgentState) -> None:
        if state._sse_task and not state._sse_task.done():
            state._sse_task.cancel()
            try:
                await state._sse_task
            except asyncio.CancelledError:
                pass
        proc = state.server_process
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning("Error stopping server on port %d: %s", state.port, e)

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            now = time.time()
            to_remove = []
            for sid, agent in self._agents.items():
                if agent.status in ("done", "error", "cancelled") and agent.finished_at:
                    if (now - agent.finished_at) > self._ttl_seconds:
                        to_remove.append(sid)
                elif agent.status == "waiting_for_input" and agent.pending_since:
                    if (now - agent.pending_since) > self._permission_timeout:
                        logger.warning(
                            "Agent %s permission timeout (%.0fs) — auto-denying",
                            sid[:20], now - agent.pending_since,
                        )
                        try:
                            await self.deny(sid, reason="Permission request timed out")
                        except Exception as e:
                            logger.error("Auto-deny failed for %s: %s", sid[:20], e)
            for sid in to_remove:
                agent = self._agents.pop(sid, None)
                if agent:
                    await self._stop_server(agent)
                    logger.debug("Pruned agent: %s", sid[:20])

    def _release_slot(self, state: SubAgentState) -> None:
        if not state._slot_released:
            state._slot_released = True
            self._semaphore.release()

    def _find_free_port(self) -> int:
        used = {a.port for a in self._agents.values()}
        for port in range(self._port_range_start, self._port_range_end):
            if port in used:
                continue
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
                    return port
            except OSError:
                continue
        raise RuntimeError(f"No free ports in {self._port_range_start}-{self._port_range_end}")

    async def shutdown(self) -> None:
        if self._prune_task:
            self._prune_task.cancel()
        for agent in list(self._agents.values()):
            await self._stop_server(agent)
        self._agents.clear()
        if self._client:
            await self._client.aclose()
            self._client = None


def get_agent_manager() -> SubAgentManager | None:
    return SubAgentManager.get()


def set_agent_manager(mgr: SubAgentManager) -> None:
    SubAgentManager.set(mgr)


__all__ = ["SubAgentState", "SubAgentManager", "get_agent_manager", "set_agent_manager"]
