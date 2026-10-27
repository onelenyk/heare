"""Reading a body: a sensor asked in the same turn, without the hands.

Only skills whose effect is ``read`` — they change nothing, so they need
neither the judge nor an agent. Anything that changes the world still goes
through ``delegate``.
"""

from __future__ import annotations

import json

from heare.tools.ports import Bodies


async def read(bodies: Bodies, arguments: dict) -> str:
    part = str(arguments.get("part", "")).strip()
    skill = str(arguments.get("skill", "")).strip()
    args = arguments.get("args") if isinstance(arguments.get("args"), dict) else {}
    found = [(pid, entry) for pid, entry in bodies.skilled().items()
             if part in (pid, entry.get("name"))]
    if not found:
        return f"Тіла «{part}» зараз немає."
    if len(found) > 1:
        return f"Тіл «{part}» кілька: " + ", ".join(pid for pid, _ in found) + ". Яке?"
    pid, entry = found[0]
    known = next((s for s in entry["skills"] if s["name"] == skill), None)
    if known is None:
        return f"У «{part}» немає вміння «{skill}»."
    if known["effect"] != "read":
        return "Це вміння щось змінює — його роблять руки, через delegate."
    answer = await bodies.call(pid, skill, args)
    result = answer.get("result")
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return text if answer.get("ok") else f"Не вийшло прочитати: {text}"
