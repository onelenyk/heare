"""The model's pin: an anchor in the dock, never a copy."""

from __future__ import annotations

def run(store: object, arguments: dict) -> str:
    message = str(arguments.get("message", "")).strip()
    title = str(arguments.get("title", "")).strip()
    line = str(arguments.get("line", "")).strip()
    kind = str(arguments.get("kind", "")).strip()
    if message == "now":
        store.claim_self(store._sessions.active)
        return "I will pin it when I say it."
    found = store.resolve(message)
    if found is None:
        return "I could not find that message."
    session, _, _n = found.rpartition("/")
    try:
        store.add(session, found, by="model", title=title, line=line, kind=kind)
    except OverflowError:
        return "The dock is full; unpin something first."
    except LookupError:
        return "I could not find that message."
    except ValueError:
        return "That message cannot be pinned."
    return f"Pinned. There are {len(store.all())} in the dock."
