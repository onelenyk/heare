"""ROLES — task-specific assistant behaviors, defined as markdown, not code.

A role is a package layered over the base persona for the duration of a
voice session: a behavior prompt, a channel policy (does it converse, or
does it just listen and stay silent until the end?), a tool policy (which
globs are off-limits to the Hands worker while the role is active), an
end-of-session artifact instruction, and the trigger phrases that start
and end it.

This mirrors ``src/skills/agent_skills.py``'s SKILL.md frontmatter
parsing (regex over ``---\\n...\\n---\\n`` body) as its precedent, but
roles are their own thing — this module does not import that one, and
does not depend on Agent Skills at all.

Constraints: stdlib only, no ``yaml`` dependency, no pipecat. Frontmatter
is parsed by hand with a small YAML-lite reader that understands
``key: value``, ``key: [a, b, c]``, and multi-line ``- item`` dash lists.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("heare.spine.roles")

_DEFAULT_END_TRIGGERS: tuple[str, ...] = (
    "закінчили",
    "кінець ролі",
    "завершуй роль",
)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)\Z", re.DOTALL)

# A top-level frontmatter key: value line, where value is everything up to
# end of line (possibly empty, meaning "the value continues on following
# dashed lines").
_KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)$")

# A dashed list-continuation line: "  - item" (any leading whitespace).
_DASH_ITEM_RE = re.compile(r"^\s*-\s*(.+?)\s*$")


@dataclass(frozen=True)
class Role:
    """One role package: behavior prompt plus policy, parsed from markdown.

    ``deny_tools`` holds fnmatch-style globs; the caller is responsible
    for applying them against Hands' tool names. ``triggers`` and
    ``end_triggers`` are matched as case-insensitive whole-phrase
    substrings, never regexes.
    """

    name: str
    channel: str = "voice"
    deny_tools: tuple[str, ...] = ()
    artifact: str = ""
    triggers: tuple[str, ...] = ()
    end_triggers: tuple[str, ...] = _DEFAULT_END_TRIGGERS
    prompt: str = ""


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_inline_list(value: str) -> list[str]:
    """Parse a YAML-lite inline list: ``[a, b, "c d"]``."""
    inner = value.strip()
    inner = inner[1:-1]  # strip [ ]
    if not inner.strip():
        return []
    items = [_strip_quotes(part) for part in inner.split(",")]
    return [item for item in items if item]


def _parse_frontmatter(text: str) -> dict[str, object]:
    """Parse YAML-lite frontmatter into a dict of str | list[str].

    Supports ``key: value``, ``key: [a, b]``, and multi-line dashed lists:

        triggers:
          - one
          - two
    """
    result: dict[str, object] = {}
    lines = text.split("\n")
    current_key: str | None = None
    current_list: list[str] | None = None

    def flush() -> None:
        nonlocal current_key, current_list
        if current_key is not None and current_list is not None:
            result[current_key] = current_list
        current_key = None
        current_list = None

    for raw_line in lines:
        if not raw_line.strip():
            continue

        # A dashed continuation line belongs to the list started by the
        # previous key, but only if the line is indented (not a new
        # top-level key).
        dash_match = _DASH_ITEM_RE.match(raw_line)
        is_indented = raw_line[:1] in (" ", "\t")
        if dash_match and is_indented and current_list is not None:
            current_list.append(_strip_quotes(dash_match.group(1)))
            continue

        key_match = _KEY_LINE_RE.match(raw_line)
        if not key_match:
            # Unparseable line — ignore it rather than raise.
            continue

        flush()
        key = key_match.group(1).strip()
        value = key_match.group(2).strip()

        if not value:
            # Value is empty on this line — it may be a multi-line dashed
            # list starting on the next line(s). Speculatively open a
            # list; if no dash lines follow, flush() will store an empty
            # list, which is harmless for the keys we care about.
            current_key = key
            current_list = []
            continue

        if value.startswith("[") and value.endswith("]"):
            result[key] = _parse_inline_list(value)
        else:
            result[key] = _strip_quotes(value)

    flush()
    return result


def _as_str(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return default


def _as_str_tuple(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    if isinstance(value, str):
        return (value,) if value else ()
    return None


class RoleLoader:
    """Discovers and parses role packages from one or more search paths.

    Search paths are walked in order; when two paths define a role with
    the same (lowercased) ``name``, the later path wins. This lets a
    user's ``~/.heare/roles`` override a bundled ``repo/roles`` file of
    the same name.
    """

    def __init__(self, paths: list[Path]) -> None:
        self.paths = [Path(p).expanduser() for p in paths]

    def load(self) -> dict[str, Role]:
        """Parse every ``*.md`` file in the search paths into a Role.

        Later paths override earlier ones on name clash. Files that are
        missing frontmatter, missing the required ``name`` key, or
        otherwise unparseable are skipped with a ``logger.warning`` —
        this method never raises for a single bad file.
        """
        roles: dict[str, Role] = {}

        for search_path in self.paths:
            if not search_path.is_dir():
                logger.debug("Roles path not found: %s", search_path)
                continue

            for md_file in sorted(search_path.glob("*.md")):
                role = self._parse_role_file(md_file)
                if role is None:
                    continue
                roles[role.name] = role

        return roles

    def _parse_role_file(self, md_file: Path) -> Role | None:
        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Cannot read role file %s: %s", md_file, e)
            return None

        match = _FRONTMATTER_RE.match(content)
        if not match:
            logger.warning("Role file missing frontmatter: %s", md_file)
            return None

        frontmatter_text, body = match.group(1), match.group(2)

        try:
            fm = _parse_frontmatter(frontmatter_text)
        except Exception as e:
            logger.warning("Cannot parse frontmatter in %s: %s", md_file, e)
            return None

        raw_name = fm.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            logger.warning("Role file missing required 'name': %s", md_file)
            return None
        name = raw_name.strip().lower()

        channel = _as_str(fm.get("channel"), default="voice") or "voice"

        deny_tools = _as_str_tuple(fm.get("deny_tools"))
        if deny_tools is None:
            deny_tools = ()

        artifact = _as_str(fm.get("artifact"), default="")

        triggers = _as_str_tuple(fm.get("triggers"))
        if triggers is None:
            triggers = ()
        triggers = tuple(t.lower() for t in triggers)

        end_triggers = _as_str_tuple(fm.get("end_triggers"))
        if end_triggers is None:
            end_triggers = _DEFAULT_END_TRIGGERS
        else:
            end_triggers = tuple(t.lower() for t in end_triggers)

        return Role(
            name=name,
            channel=channel,
            deny_tools=deny_tools,
            artifact=artifact,
            triggers=triggers,
            end_triggers=end_triggers,
            prompt=body.strip(),
        )


def match_trigger(text: str, roles: dict[str, Role]) -> Role | None:
    """Find the role whose trigger phrase occurs in ``text``.

    Matching is case-insensitive and whole-phrase-substring: a trigger
    matches when it occurs anywhere inside ``text`` (so «режим інтерв'ю»
    matches inside «Дока, режим інтерв'ю будь ласка»). When multiple
    roles' triggers match, the longest matching trigger phrase wins,
    regardless of which role it belongs to. Returns ``None`` when no
    trigger matches.
    """
    haystack = text.lower()

    best_role: Role | None = None
    best_len = -1

    for role in roles.values():
        for trigger in role.triggers:
            if not trigger:
                continue
            if trigger in haystack and len(trigger) > best_len:
                best_role = role
                best_len = len(trigger)

    return best_role


def is_end_trigger(text: str, role: Role) -> bool:
    """True when any of ``role``'s end triggers occurs in ``text``.

    Matching is case-insensitive, whole-phrase substring — the same rule
    as :func:`match_trigger`.
    """
    haystack = text.lower()
    return any(trigger and trigger in haystack for trigger in role.end_triggers)
