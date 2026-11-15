"""An opencode server that does nothing: for testing the executor.

What it does is chosen by a word in the task — WRITE asks before it
changes something, READ asks to look, ASK asks the person a question,
HANG works until it is aborted, DEAF works and ignores the abort, DIE
exits, MALFORMED writes junk, and REPORT writes the child's environment
and cwd to a path and answers.

It speaks the same HTTP as the real server: ``POST /session``,
``prompt_async``, ``GET /event`` as a stream, permission and question
replies, and the reads a hand makes after the work (messages, diff, todo).
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORLD_LOCK = threading.Condition()


class World:
    """Everything the fake remembers between requests."""

    def __init__(self) -> None:
        self.sessions: dict[str, int] = {}
        self.events: list[dict] = []
        self.permissions: dict[str, str | None] = {}
        self.questions: dict[str, list[str] | None] = {}
        self.aborted: set[str] = set()
        self.reverted: set[str] = set()
        self.created = 0
        self.replied = 0

    def session(self) -> str:
        with WORLD_LOCK:
            self.created += 1
            session = f"ses_{self.created}"
            self.sessions[session] = 0
            return session

    def emit(self, kind: str, props: dict) -> None:
        with WORLD_LOCK:
            self.events.append({"type": kind, "properties": props})
            WORLD_LOCK.notify_all()

    def update(self, session: str, part: dict) -> None:
        self.emit("message.part.updated", {"sessionID": session, "part": part})

    def say(self, session: str, text: str) -> None:
        message = f"msg_{session}"
        self.emit("message.updated",
                  {"sessionID": session, "info": {"id": message, "role": "assistant"}})
        self.update(session, {"id": f"prt_{time.monotonic_ns()}", "messageID": message,
                              "sessionID": session, "type": "text", "text": text})

    def ask_permission(self, session: str, permission: str, patterns: list[str],
                       metadata: dict) -> str:
        with WORLD_LOCK:
            self.replied += 1
            request = f"per_{self.replied}"
            self.permissions[request] = None
        self.emit("permission.asked", {
            "id": request, "sessionID": session, "permission": permission,
            "patterns": patterns, "metadata": metadata,
            "tool": {"messageID": f"msg_{session}", "callID": f"call_{request}"},
        })
        return request

    def ask_question(self, session: str, text: str) -> list[str] | None:
        with WORLD_LOCK:
            self.replied += 1
            request = f"que_{self.replied}"
            self.questions[request] = None
        self.emit("question.asked", {
            "id": request, "sessionID": session,
            "questions": [{"question": text, "header": "Heare"},
                          {"question": text + " (still)", "header": "Heare"}],
        })
        return request

    def wait(self, box: dict, key: str, timeout: float = 30.0) -> object:
        deadline = time.monotonic() + timeout
        with WORLD_LOCK:
            while box[key] is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                WORLD_LOCK.wait(left)
            return box[key]

    def idle(self, session: str) -> None:
        self.emit("session.status", {"sessionID": session,
                                     "status": {"type": "idle"}})
        self.emit("session.idle", {"sessionID": session})


WORLD = World()


def _chore(world: World, session: str, text: str) -> None:
    if "REPORT" in text:
        where = text.split("REPORT", 1)[1].strip().split()[0]
        with open(where, "w", encoding="utf-8") as report:
            json.dump({"env": dict(os.environ), "cwd": os.getcwd()}, report)
        world.say(session, "Звіт.")
        world.idle(session)
    elif "MALFORMED" in text:
        # A frame that is not an event, then a part of a shape nothing has,
        # then a good answer: the reader must survive all three.
        world.emit("message.part.updated", {"sessionID": session, "part": []})
        world.update(session, {"id": "prt_bad", "sessionID": session, "type": "?"})
        world.say(session, "Зроблено.")
        world.idle(session)
    elif "DEAF" in text:
        world.say(session, "Роблю.")
        world.update(session, {"id": "prt_tool", "sessionID": session,
                               "type": "tool", "tool": "bash",
                               "callID": "call_1",
                               "state": {"status": "running",
                                         "input": {"command": "sleep 300"}}})
        # The abort is ignored on purpose: the hands must kill the server.
    elif "HANG" in text:
        world.update(session, {"id": "prt_tool", "sessionID": session,
                               "type": "tool", "tool": "bash",
                               "callID": "call_1",
                               "state": {"status": "running",
                                         "input": {"command": "sleep 300"}}})
        while session not in world.aborted:
            time.sleep(0.05)
        world.say(session, "Спинився.")
        world.idle(session)
    elif "ASK" in text:
        request = world.ask_question(session, "Куди писати?")
        answers = world.wait(world.questions, request)
        world.say(session, "Гаразд: " + " / ".join(
            "; ".join(a) for a in (answers or [])) + ".")
        world.idle(session)
    elif "WRITE" in text or "READ" in text:
        if "WRITE" in text:
            request = world.ask_permission(
                session, "edit", ["note.txt"],
                {"filepath": "/tmp/note.txt", "diff": "-a\n+b\n"})
        else:
            request = world.ask_permission(session, "read", ["note.txt"], {})
        answer = world.wait(world.permissions, request)
        world.say(session, "Записано." if answer == "once" else "Відмовлено.")
        world.idle(session)
    else:
        world.say(session, "Зроблено.")
        world.idle(session)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args: object) -> None:
        pass  # a test's silence

    # -- plumbing ------------------------------------------------------

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            found = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return found if isinstance(found, dict) else {}

    def _send(self, code: int, body: object | None = None) -> None:
        self.send_response(code)
        if body is None:
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        raw = json.dumps(body).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 — the server's own spelling
        path = self.path.split("?", 1)[0]
        if path == "/global/health":
            self._send(200, {"healthy": True, "version": "fake"})
            return
        if path == "/event":
            self._stream()
            return
        if path == "/session":
            with WORLD_LOCK:
                found = [{"id": name} for name in WORLD.sessions]
            self._send(200, found)
            return
        if path.startswith("/session/"):
            rest = path.removeprefix("/session/")
            session, _, tail = rest.partition("/")
            if session not in WORLD.sessions:
                if session == "ses_elsewhere":
                    self._send(200, {"id": session, "directory": "/somewhere/else"})
                else:
                    self._send(404, {"error": "no such session"})
                return
            if tail == "":
                info: dict = {"id": session, "title": "fake", "cost": 0.0,
                              "tokens": {"input": 10, "output": 5,
                                         "reasoning": 0,
                                         "cache": {"read": 0, "write": 0}}}
                with WORLD_LOCK:
                    if session in WORLD.reverted:
                        info["revert"] = {"messageID": f"msg_{session}"}
                self._send(200, info)
            elif tail == "message":
                self._send(200, [{"info": {"id": f"msg_{session}", "role": "user"},
                                  "parts": []}])
            elif tail == "diff":
                self._send(200, [] if session in WORLD.reverted else [
                    {"file": "note.txt", "patch": "-a\n+b\n",
                     "additions": 1, "deletions": 1, "status": "modified"}])
            elif tail == "todo":
                self._send(200, [{"content": "щось", "status": "completed",
                                  "priority": "high"}])
            else:
                self._send(404, {"error": "no such thing"})
            return
        self._send(404, {"error": "no such path"})

    def do_POST(self) -> None:  # noqa: N802 — the server's own spelling
        path = self.path.split("?", 1)[0]
        body = self._body()
        if path == "/session":
            session = WORLD.session()
            self._send(200, {"id": session, "tokens": {}})
            return
        if path.startswith("/permission/"):
            request = path.removeprefix("/permission/").removesuffix("/reply")
            with WORLD_LOCK:
                if request in WORLD.permissions:
                    WORLD.permissions[request] = str(body.get("reply") or "reject")
                    WORLD_LOCK.notify_all()
            self._send(200, {"ok": True})
            return
        if path.startswith("/question/"):
            request = path.removeprefix("/question/").removesuffix("/reply")
            answers = body.get("answers")
            with WORLD_LOCK:
                if request in WORLD.questions:
                    WORLD.questions[request] = answers if isinstance(answers, list) else []
                    WORLD_LOCK.notify_all()
            self._send(200, {"ok": True})
            return
        if path.startswith("/session/"):
            rest = path.removeprefix("/session/")
            session, _, tail = rest.partition("/")
            if session not in WORLD.sessions:
                self._send(404, {"error": "no such session"})
                return
            if tail == "prompt_async":
                text = " ".join(str(part.get("text") or "")
                                for part in body.get("parts") or []
                                if isinstance(part, dict))
                if "DIE" in text:
                    os._exit(1)  # a server that goes away mid-answer
                threading.Thread(target=_chore, args=(WORLD, session, text),
                                 daemon=True).start()
                self._send(204)
                return
            if tail == "fork":
                branch = WORLD.session()
                self._send(200, {"id": branch})
                return
            if tail == "abort":
                with WORLD_LOCK:
                    WORLD.aborted.add(session)
                    WORLD_LOCK.notify_all()
                self._send(200, {"ok": True})
                return
            if tail == "revert":
                with WORLD_LOCK:
                    WORLD.reverted.add(session)
                self._send(200, {"id": session})
                return
            if tail == "unrevert":
                with WORLD_LOCK:
                    WORLD.reverted.discard(session)
                self._send(200, {"id": session})
                return
        self._send(404, {"error": "no such path"})

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        seen = 0
        try:
            while True:
                with WORLD_LOCK:
                    if len(WORLD.events) <= seen:
                        WORLD_LOCK.wait(0.1)
                    pending = WORLD.events[seen:]
                for event in pending:
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                if pending:
                    self.wfile.flush()
                seen += len(pending)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--hostname", default="127.0.0.1")
    # ``serve`` comes before the flags from the hands: it is implied here.
    args, _unknown = parser.parse_known_args()
    server = ThreadingHTTPServer((args.hostname, args.port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
