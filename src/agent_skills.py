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
from typing import Optional

logger = logging.getLogger("heare.agent_skills")


@dataclass(frozen=True)
class SkillMetadata:
    """Lightweight skill metadata (always loaded)."""

    name: str  # lowercase-hyphenated
    description: str  # one-liner from SKILL.md frontmatter
    path: Path  # skill root dir (contains SKILL.md)
    installed_via_discovery: bool = False


class SkillsLoader:
    """Discovers and loads Agent Skills from multiple search paths."""

    def __init__(self, search_paths: list[Path]) -> None:
        """Initialize with search paths (expanded, absolute).

        Parameters
        ----------
        search_paths : list[Path]
            Directories to scan for skills (each should be an absolute Path).
        """
        self.search_paths = [p.expanduser().resolve() for p in search_paths]
        self._metadata_cache: list[SkillMetadata] = []
        self._discovered = False

    def discover(self) -> list[SkillMetadata]:
        """Discover available skills (names + descriptions only).

        Returns
        -------
        list[SkillMetadata]
            Lightweight metadata for each discovered skill.
            Bodies are NOT loaded; use load_instructions() for full content.
        """
        if self._discovered:
            return self._metadata_cache

        self._metadata_cache = []

        for search_path in self.search_paths:
            if not search_path.is_dir():
                logger.debug(f"Skills path not found: {search_path}")
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
                            installed = (marketplace_skill_dir / ".install.json").exists()
                            metadata = self._parse_skill_metadata(skill_md, installed_via_discovery=installed)
                            if metadata:
                                self._metadata_cache.append(metadata)
                        except Exception as e:
                            logger.warning(
                                f"Failed to parse skill at {marketplace_skill_dir}: {e}"
                            )
                    continue

                skill_md = potential_skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue

                try:
                    installed = (potential_skill_dir / ".install.json").exists()
                    metadata = self._parse_skill_metadata(skill_md, installed_via_discovery=installed)
                    if metadata:
                        self._metadata_cache.append(metadata)
                except Exception as e:
                    logger.warning(
                        f"Failed to parse skill at {potential_skill_dir}: {e}"
                    )
                    continue

        self._discovered = True
        return self._metadata_cache

    def invalidate(self) -> None:
        """Force re-scan on next discover() call. Used post-install to surface freshly-installed skills mid-session."""
        self._discovered = False
        self._metadata_cache = []

    def load_instructions(self, name: str) -> str:
        """Load full SKILL.md instructions for a named skill.

        Parameters
        ----------
        name : str
            Skill name (lowercase-hyphenated).

        Returns
        -------
        str
            Full SKILL.md body (everything after frontmatter).

        Raises
        ------
        KeyError
            If skill is not found.
        """
        # Ensure discovery has run
        self.discover()

        # Find the skill
        skill = next((s for s in self._metadata_cache if s.name == name), None)
        if not skill:
            available = ", ".join(self.get_skill_names())
            raise KeyError(
                f"Skill '{name}' not found. Available: {available}"
            )

        # Load full body
        skill_md = skill.path / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")

        # Extract body (everything after frontmatter)
        # Frontmatter is between --- markers
        match = re.search(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
        if match:
            return match.group(2).strip()
        else:
            # No frontmatter found, return entire content
            return content.strip()

    def get_skill_names(self) -> list[str]:
        """Get list of available skill names.

        Returns
        -------
        list[str]
            Skill names in discovery order.
        """
        return [s.name for s in self.discover()]

    def _parse_skill_metadata(self, skill_md: Path, installed_via_discovery: bool = False) -> Optional[SkillMetadata]:
        """Parse YAML frontmatter from SKILL.md.

        Parameters
        ----------
        skill_md : Path
            Path to SKILL.md file.
        installed_via_discovery : bool
            True when a .install.json sidecar exists alongside the skill.

        Returns
        -------
        Optional[SkillMetadata]
            Metadata if parsing succeeds, None if frontmatter is invalid.
        """
        content = skill_md.read_text(encoding="utf-8")

        # Extract YAML frontmatter (between --- markers)
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

        name = name_match.group(1).strip(' "\'')
        description = desc_match.group(1).strip(' "\'')

        # Validate name format (lowercase-hyphenated)
        if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name):
            logger.warning(
                f"Invalid skill name format: {name} (expected lowercase-hyphenated)"
            )
            return None

        return SkillMetadata(name=name, description=description, path=skill_md.parent, installed_via_discovery=installed_via_discovery)


# Module-level singleton
_loader: Optional[SkillsLoader] = None
_loader_paths: Optional[list[str]] = None


def get_skills_loader(settings: Optional[object] = None) -> SkillsLoader:
    """Get or create the global SkillsLoader singleton.

    Parameters
    ----------
    settings : Optional[object]
        Settings object with skills_paths attribute.
        If None, defaults to ["~/.heare/skills"].

    Returns
    -------
    SkillsLoader
        The cached loader instance, rebuilt if settings_paths changed.
    """
    global _loader, _loader_paths

    # Determine the desired paths
    if settings and hasattr(settings, "skills_paths"):
        desired_paths = list(settings.skills_paths)
    else:
        desired_paths = ["~/.heare/skills"]

    # Rebuild if paths changed or loader doesn't exist
    if _loader is None or _loader_paths != desired_paths:
        paths = [Path(p) for p in desired_paths]
        _loader = SkillsLoader(paths)
        _loader_paths = desired_paths

        # Log startup info
        discovered = _loader.discover()
        logger.info(
            f"Loaded {len(discovered)} skills from {len(_loader.search_paths)} paths"
        )

    return _loader
