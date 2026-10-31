"""An agent that speaks ACP and does nothing: for testing the executor.

What it does is chosen by a word in the task — WRITE asks before it
changes something, READ asks to look, HANG works until it is cancelled,
DEAF works and ignores the cancel, DIE exits, MALFORMED writes a frame
that is not an object, and REPORT writes the child's environment and cwd
to a path and answers.
"""

from __future__ import annotations

import json
import os
import sys


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def update(session: str, body: dict) -> None:
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": session, "update": body}})


def say(session: str, text: str) -> None:
    update(session, {"sessionUpdate": "agent_message_chunk",
                     "content": {"type": "text", "text": text}})


def main() -> None:
    sessions = 0
    waiting: dict[int, tuple[str, int]] = {}  # permission id -> (session, prompt id)
    hanging: dict[str, int] = {}  # session -> prompt id
    deaf: set[str] = set()  # sessions whose cancel is ignored
    asks = 900
    for line in sys.stdin:
        message = json.loads(line)
        method, rid = message.get("method"), message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1}})
        elif method == "session/new":
            sessions += 1
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": f"s{sessions}"}})
        elif method == "session/prompt":
            session = params["sessionId"]
            text = " ".join(b.get("text", "") for b in params["prompt"])
            if "REPORT" in text:
                where = text.split("REPORT", 1)[1].strip().split()[0]
                with open(where, "w", encoding="utf-8") as report:
                    json.dump({"env": dict(os.environ), "cwd": os.getcwd()}, report)
                say(session, "Звіт.")
                send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
            elif "MALFORMED" in text:
                # A frame that is not an object, then a bad update, then a
                # good answer: the reader must survive all three.
                sys.stdout.write("[]\n")
                update(session, [])
                say(session, "Зроблено.")
                send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
            elif "DIE" in text:
                sys.exit(1)
            elif "DEAF" in text:
                update(session, {"sessionUpdate": "tool_call", "title": "Довга робота"})
                hanging[session] = rid
                deaf.add(session)
            elif "HANG" in text:
                update(session, {"sessionUpdate": "tool_call", "title": "Довга робота"})
                hanging[session] = rid
            elif "WRITE" in text or "READ" in text:
                kind = "edit" if "WRITE" in text else "read"
                asks += 1
                waiting[asks] = (session, rid)
                send({"jsonrpc": "2.0", "id": asks, "method": "session/request_permission",
                      "params": {"sessionId": session,
                                 "toolCall": {"title": "Записати файл", "kind": kind,
                                              "rawInput": {"file_path": "note.txt"}},
                                 "options": [{"optionId": "y", "kind": "allow_once"},
                                             {"optionId": "n", "kind": "reject_once"}]}})
            else:
                say(session, "Зроблено.")
                send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        elif method == "session/cancel":
            session = params["sessionId"]
            if session in hanging and session not in deaf:
                send({"jsonrpc": "2.0", "id": hanging.pop(session),
                      "result": {"stopReason": "cancelled"}})
        elif method is None and rid in waiting:
            session, prompt = waiting.pop(rid)
            outcome = (message.get("result") or {}).get("outcome") or {}
            say(session, "Записано." if outcome.get("optionId") == "y" else "Відмовлено.")
            send({"jsonrpc": "2.0", "id": prompt, "result": {"stopReason": "end_turn"}})


if __name__ == "__main__":
    main()
