"""User profile system for persistent preferences and settings.

This module implements a cross-session profile system that stores user preferences
for file access, view settings, favorite locations, and access history.
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    """Persistent user preferences for file access and workspace management."""

    # Directories explicitly approved for access
    allowed_directories: List[Dict[str, str]] = field(
        default_factory=list
    )  # [{"path": "/path/to/dir", "label": "My Projects", "approved_at": "2026-01-01T00:00:00"}]

    # Frequently accessed paths (favorites)
    favorite_locations: List[Dict[str, str]] = field(
        default_factory=list
    )  # [{"path": "/path/to/dir", "label": "Projects", "access_count": 5, "last_accessed": "2026-01-01T00:00:00"}]

    # Patterns to ignore in file operations
    ignored_patterns: List[str] = field(
        default_factory=lambda: [".git", ".svn", "node_modules", "__pycache__", "*.pyc", ".DS_Store"]
    )

    # Display preferences
    view_preferences: Dict[str, Any] = field(
        default_factory=lambda: {
            "show_hidden": False,
            "detail_level": "standard",  # minimal, standard, detailed
            "sort_by": "name",  # name, size, modified, type
            "sort_order": "asc",  # asc, desc
        }
    )

    # Recent search history
    search_history: List[Dict[str, Any]] = field(
        default_factory=list
    )  # [{"query": "python files", "path": "/path", "timestamp": "2026-01-01T00:00:00", "results": 10}]

    # Recent file access log
    access_log: List[Dict[str, Any]] = field(
        default_factory=list
    )  # [{"path": "/path/to/file", "operation": "read", "timestamp": "2026-01-01T00:00:00", "duration_ms": 150}]

    # Security settings
    security_settings: Dict[str, Any] = field(
        default_factory=lambda: {
            "confirm_deletes": True,
            "confirm_overwrites": True,
            "max_archive_size": 1024 * 1024 * 1024,  # 1GB
            "operation_timeout": 300,  # 5 minutes
        }
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert profile to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserProfile":
        """Create profile from dictionary."""
        return cls(**data)


class ProfileManager:
    """Manager for user profiles with persistence and concurrent access."""

    def __init__(self, profile_path: Path):
        self.profile_path = profile_path
        self.profile: Optional[UserProfile] = None
        self._lock = asyncio.Lock()

    async def load(self) -> UserProfile:
        """Load profile from file or create default."""
        async with self._lock:
            if self.profile_path.exists():
                try:
                    with open(self.profile_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        self.profile = UserProfile.from_dict(data)
                        logger.info("Loaded user profile from %s", self.profile_path)
                except Exception as e:
                    logger.warning("Failed to load profile: %s, creating default", e)
                    self.profile = UserProfile()
                    await self.save()
            else:
                self.profile = UserProfile()
                logger.info("Created new user profile at %s", self.profile_path)

            return self.profile

    async def save(self) -> None:
        """Save profile to file."""
        async with self._lock:
            if self.profile is None:
                return

            try:
                self.profile_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.profile_path, 'w', encoding='utf-8') as f:
                    json.dump(self.profile.to_dict(), f, indent=2, ensure_ascii=False)
                logger.info("Saved user profile to %s", self.profile_path)
            except Exception as e:
                logger.error("Failed to save profile: %s", e)

    async def add_allowed_directory(self, path: Path, label: Optional[str] = None) -> None:
        """Add a directory to the allowed list."""
        async with self._lock:
            if self.profile is None:
                await self.load()

            # Check if already allowed
            existing = next(
                (item for item in self.profile.allowed_directories if item["path"] == str(path)),
                None
            )

            if existing:
                # Update timestamp
                existing["approved_at"] = datetime.now().isoformat()
                if label:
                    existing["label"] = label
            else:
                # Add new entry
                self.profile.allowed_directories.append({
                    "path": str(path),
                    "label": label or path.name,
                    "approved_at": datetime.now().isoformat(),
                })

            await self.save()

    async def remove_allowed_directory(self, path: Path) -> bool:
        """Remove a directory from the allowed list."""
        async with self._lock:
            if self.profile is None:
                await self.load()

            original_count = len(self.profile.allowed_directories)
            self.profile.allowed_directories = [
                item for item in self.profile.allowed_directories
                if item["path"] != str(path)
            ]

            if len(self.profile.allowed_directories) < original_count:
                await self.save()
                return True
            return False

    def is_directory_allowed(self, path: Path, current_session_permissions: Set[Path] = None) -> bool:
        """Check if a directory is allowed for access."""
        if current_session_permissions and path in current_session_permissions:
            return True

        if self.profile is None:
            return False

        # Check allowed directories
        for item in self.profile.allowed_directories:
            allowed_path = Path(item["path"])
            if path == allowed_path or allowed_path in path.parents:
                return True

        # Always allow workspace directory
        if hasattr(self, 'workspace_path') and path == self.workspace_path:
            return True

        return False

    async def add_favorite(self, path: Path, label: Optional[str] = None) -> None:
        """Add a location to favorites."""
        async with self._lock:
            if self.profile is None:
                await self.load()

            # Remove existing favorite for this path
            self.profile.favorite_locations = [
                item for item in self.profile.favorite_locations
                if item["path"] != str(path)
            ]

            # Add or update favorite
            favorite = {
                "path": str(path),
                "label": label or path.name,
                "access_count": 0,
                "last_accessed": datetime.now().isoformat(),
            }

            self.profile.favorite_locations.append(favorite)

            # Keep only last 50 favorites
            self.profile.favorite_locations.sort(
                key=lambda x: x["last_accessed"],
                reverse=True
            )
            self.profile.favorite_locations = self.profile.favorite_locations[:50]

            await self.save()

    async def record_access(self, path: Path, operation: str, duration_ms: int = 0) -> None:
        """Record file/directory access in the log."""
        async with self._lock:
            if self.profile is None:
                await self.load()

            # Update favorites
            for favorite in self.profile.favorite_locations:
                if favorite["path"] == str(path):
                    favorite["access_count"] += 1
                    favorite["last_accessed"] = datetime.now().isoformat()
                    break
            else:
                # Not in favorites, add it
                await self.add_favorite(path)

            # Add to access log
            self.profile.access_log.append({
                "path": str(path),
                "operation": operation,
                "timestamp": datetime.now().isoformat(),
                "duration_ms": duration_ms,
            })

            # Keep only last 1000 log entries
            self.profile.access_log.sort(key=lambda x: x["timestamp"], reverse=True)
            self.profile.access_log = self.profile.access_log[:1000]

            await self.save()

    async def add_to_search_history(self, query: str, path: Path, results_count: int) -> None:
        """Add search to history."""
        async with self._lock:
            if self.profile is None:
                await self.load()

            # Remove existing same query
            self.profile.search_history = [
                item for item in self.profile.search_history
                if item["query"] != query
            ]

            # Add new search
            self.profile.search_history.append({
                "query": query,
                "path": str(path),
                "timestamp": datetime.now().isoformat(),
                "results": results_count,
            })

            # Keep only last 50 searches
            self.profile.search_history.sort(key=lambda x: x["timestamp"], reverse=True)
            self.profile.search_history = self.profile.search_history[:50]

            await self.save()

    def get_favorites(self, limit: int = 10) -> List[Dict[str, str]]:
        """Get favorite locations."""
        if self.profile is None:
            return []

        # Sort by last accessed and return limited results
        sorted_favorites = sorted(
            self.profile.favorite_locations,
            key=lambda x: x["last_accessed"],
            reverse=True
        )
        return sorted_favorites[:limit]

    def get_ignored_patterns(self) -> List[str]:
        """Get patterns to ignore."""
        if self.profile is None:
            return UserProfile().ignored_patterns
        return self.profile.ignored_patterns

    def get_view_preferences(self) -> Dict[str, Any]:
        """Get display preferences."""
        if self.profile is None:
            return UserProfile().view_preferences
        return self.profile.view_preferences

    async def set_view_preference(self, key: str, value: Any) -> None:
        """Set a view preference."""
        async with self._lock:
            if self.profile is None:
                await self.load()

            self.profile.view_preferences[key] = value
            await self.save()

    async def search_access_log(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search access log."""
        if self.profile is None:
            return []

        results = []
        query_lower = query.lower()

        for entry in self.profile.access_log:
            if (query_lower in entry["path"].lower() or
                query_lower in entry["operation"].lower()):
                results.append(entry)
                if len(results) >= limit:
                    break

        return results

    async def export_profile(self, export_path: Path) -> None:
        """Export profile to JSON file."""
        async with self._lock:
            if self.profile is None:
                await self.load()

            export_data = {
                "export_timestamp": datetime.now().isoformat(),
                "profile": self.profile.to_dict(),
            }

            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            logger.info("Exported profile to %s", export_path)

    async def import_profile(self, import_path: Path) -> None:
        """Import profile from JSON file."""
        async with self._lock:
            with open(import_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if "profile" in data:
                self.profile = UserProfile.from_dict(data["profile"])
                await self.save()
            else:
                # Legacy format - assume entire file is profile
                self.profile = UserProfile.from_dict(data)
                await self.save()

            logger.info("Imported profile from %s", import_path)


# Global profile manager instance
_profile_manager: Optional[ProfileManager] = None


async def get_profile_manager(profile_path: Path) -> ProfileManager:
    """Get global profile manager instance."""
    global _profile_manager

    if _profile_manager is None:
        _profile_manager = ProfileManager(profile_path)
        await _profile_manager.load()

    return _profile_manager


async def get_user_profile(profile_path: Path) -> UserProfile:
    """Get user profile."""
    manager = await get_profile_manager(profile_path)
    return manager.profile


# Convenience functions for direct access
async def add_favorite_location(path: Path, label: Optional[str] = None, profile_path: Path = None) -> None:
    """Add a location to favorites."""
    if profile_path is None:
        profile_path = Path.home() / ".heare" / "profile.json"

    manager = await get_profile_manager(profile_path)
    await manager.add_favorite(path, label)


async def get_favorite_locations(limit: int = 10, profile_path: Path = None) -> List[Dict[str, str]]:
    """Get favorite locations."""
    if profile_path is None:
        profile_path = Path.home() / ".heare" / "profile.json"

    manager = await get_profile_manager(profile_path)
    return manager.get_favorites(limit)