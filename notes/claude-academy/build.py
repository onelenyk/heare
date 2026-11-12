"""Zbiraie rozdily konspektu v odyn dokument.

Dzherela: _head.md, 0X-*.md, _tail.md. Kozhen rozdil spuska svii H1 (vin
zaminiaietsia na H2 chastyny), reshta zagolovkiv zsuvaietsia na odyn riven.
Kodovi bloky ne chypaiemo.
"""

from pathlib import Path

HERE = Path(__file__).parent


def demote(lines):
    out, fenced = [], False
    for line in lines:
        if line.startswith("```"):
            fenced = not fenced
        out.append("#" + line if line.startswith("#") and not fenced else line)
    return out


def body(name):
    lines = (HERE / name).read_text().splitlines()
    assert lines[0].startswith("# "), lines[0]
    return "\n".join(demote(lines[1:])).strip()


parts = [(HERE / "_head.md").read_text().strip()]

parts.append("## 1. AI Fluency: 4D-фреймворк\n\n" + body("01-ai-fluency.md"))
parts.append("## 2. Межі моделей\n\n" + body("02-ai-capabilities.md"))

# Заголовок 03 — два курси в одному файлі, ділимо по другому H1.
raw = (HERE / "03-human-agent-teams-sdlc.md").read_text()
first, second = raw.split("\n# The AI-native SDLC playbook\n", 1)
team = "\n".join(demote(first.splitlines()[1:])).strip()
sdlc = "\n".join(demote(second.splitlines())).strip()
parts.append(
    "## 3. Люди й агенти, SDLC\n\n"
    "### Building effective human-agent teams (beta)\n\n"
    f"{team}\n\n### The AI-native SDLC playbook\n\n{sdlc}"
)

parts.append("## 4. Розробка з Claude API\n\n" + body("04-building-with-the-claude-api.md"))
parts.append("## 5. Короткі туторіали\n\n" + body("05-short-tutorials.md"))
parts.append((HERE / "_tail.md").read_text().strip())

out = "\n\n".join(parts) + "\n"
(HERE.parent / "claude-academy.md").write_text(out)
print(f"{len(out.split())} words -> {HERE.parent / 'claude-academy.md'}")
