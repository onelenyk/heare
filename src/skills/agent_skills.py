"""Agent Skills integration for heare.

Discovers and loads agentskills.io format skills from the filesystem.
Skills are stored as folders with SKILL.md files containing YAML frontmatter
and markdown instructions.

Progressive disclosure: skill metadata (name + description) is loaded at startup,
full instructions are loaded on-demand when the skill is executed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("heare.agent_skills")

_MAX_SKILL_MD_BYTES = 1 << 20  # 1 MB


@dataclass(frozen=True)
class SkillMetadata:
    """Lightweight skill metadata (always loaded)."""

    name: str
    description: str
    path: Path
    installed_via_discovery: bool = False


class SkillsLoader:
    """Discovers and loads Agent Skills from multiple search paths."""

    def __init__(self, search_paths: list[Path]) -> None:
        self.search_paths = [p.expanduser().resolve() for p in search_paths]
        self._metadata_cache: list[SkillMetadata] = []
        self._discovered = False

    def discover(self) -> list[SkillMetadata]:
        """Return lightweight metadata for each discovered skill. Bodies are NOT loaded."""
        if self._discovered:
            return self._metadata_cache

        self._metadata_cache = []

        for search_path in self.search_paths:
            if not search_path.is_dir():
                logger.debug("Skills path not found: %s", search_path)
                continue

            for potential_skill_dir in search_path.iterdir():
                if not potential_skill_dir.is_dir():
                    continue

                if potential_skill_dir.name == "_marketplace":
                    # Scan one level deeper: _marketplace/<slug>/SKILL.md
                    for marketplace_skill_dir in potential_skill_dir.iterdir():
                        if not marketplace_skill_dir.is_dir():
                            continue
                        skill_md = marketplace_skill_dir / "SKILL.md"
                        if not skill_md.exists():
                            continue
                        try:
                            installed = (
                                marketplace_skill_dir / ".install.json"
                            ).exists()
                            metadata = self._parse_skill_metadata(
                                skill_md, installed_via_discovery=installed
                            )
                            if metadata:
                                self._metadata_cache.append(metadata)
                        except Exception as e:
                            logger.warning(
                                "Failed to parse skill at %s: %s",
                                marketplace_skill_dir,
                                e,
                            )
                    continue

                skill_md = potential_skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue

                try:
                    installed = (potential_skill_dir / ".install.json").exists()
                    metadata = self._parse_skill_metadata(
                        skill_md, installed_via_discovery=installed
                    )
                    if metadata:
                        self._metadata_cache.append(metadata)
                except Exception as e:
                    logger.warning(
                        "Failed to parse skill at %s: %s", potential_skill_dir, e
                    )

        self._discovered = True
        return self._metadata_cache

    def invalidate(self) -> None:
        """Force re-scan on next discover() call. Used post-install to surface freshly-installed skills mid-session."""
        self._discovered = False
        self._metadata_cache = []

    def load_instructions(self, name: str) -> str:
        """Return full SKILL.md body (everything after frontmatter). Raises KeyError if not found."""
        self.discover()

        skill = next((s for s in self._metadata_cache if s.name == name), None)
        if not skill:
            available = ", ".join(self.get_skill_names())
            raise KeyError(f"Skill '{name}' not found. Available: {available}")

        content = (skill.path / "SKILL.md").read_text(encoding="utf-8")
        match = re.search(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
        if match:
            return match.group(2).strip()
        return content.strip()

    def get_skill_names(self) -> list[str]:
        return [s.name for s in self.discover()]

    def _parse_skill_metadata(
        self, skill_md: Path, installed_via_discovery: bool = False
    ) -> SkillMetadata | None:
        try:
            size = skill_md.stat().st_size
            if size > _MAX_SKILL_MD_BYTES:
                logger.warning(
                    "Skipping oversized SKILL.md: %s (%d bytes > %d)",
                    skill_md,
                    size,
                    _MAX_SKILL_MD_BYTES,
                )
                return None
            content = skill_md.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Cannot read %s: %s", skill_md, e)
            return None

        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None

        frontmatter_text = match.group(1)

        name_match = re.search(r"^name:\s*(.+?)$", frontmatter_text, re.MULTILINE)
        desc_match = re.search(
            r"^description:\s*(.+?)$", frontmatter_text, re.MULTILINE
        )

        if not (name_match and desc_match):
            return None

        name = name_match.group(1).strip(" \"'")
        description = desc_match.group(1).strip(" \"'")

        if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name):
            logger.warning(
                "Invalid skill name format: %s (expected lowercase-hyphenated)", name
            )
            return None

        return SkillMetadata(
            name=name,
            description=description,
            path=skill_md.parent,
            installed_via_discovery=installed_via_discovery,
        )


_loader: SkillsLoader | None = None
_loader_paths: list[str] | None = None


def get_skills_loader(settings: object | None = None) -> SkillsLoader:
    """Return the global SkillsLoader singleton, rebuilding if settings paths changed."""
    global _loader, _loader_paths

    if settings and hasattr(settings, "skills_paths"):
        desired_paths = list(settings.skills_paths)
    else:
        desired_paths = ["~/.heare/skills"]

    if _loader is None or _loader_paths != desired_paths:
        paths = [Path(p) for p in desired_paths]
        _loader = SkillsLoader(paths)
        _loader_paths = desired_paths
        discovered = _loader.discover()
        logger.info(
            "Loaded %d skills from %d paths", len(discovered), len(_loader.search_paths)
        )

    return _loader
