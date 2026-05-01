"""Permission manager for interactive directory access control.

This module handles user prompts for accessing new directories and manages
both temporary and permanent access permissions.
"""
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from datetime import datetime, timedelta

from .user_profile import ProfileManager, get_profile_manager
from .indication import Indication, IndicationKind, get_indication

logger = logging.getLogger(__name__)


@dataclass
class TemporaryPermission:
    """Temporary permission for a directory."""
    path: Path
    granted_at: datetime
    expires_at: datetime
    label: Optional[str] = None


class PermissionManager:
    """Handle interactive permission requests for new directories."""

    def __init__(self, profile_path: Path):
        self.profile_path = profile_path
        self.temp_permissions: Dict[str, TemporaryPermission] = {}
        self.auto_approve_workspace = True
        self.session_permissions: Set[Path] = set()

    async def initialize(self) -> None:
        """Initialize permission manager."""
        # Clean up expired temporary permissions
        await self._cleanup_expired_permissions()

    async def _cleanup_expired_permissions(self) -> None:
        """Remove expired temporary permissions."""
        now = datetime.now()
        expired_paths = []

        for path, permission in self.temp_permissions.items():
            if now > permission.expires_at:
                expired_paths.append(path)

        for path in expired_paths:
            del self.temp_permissions[path]
            logger.info("Removed expired permission for %s", path)

    async def request_directory_access(
        self,
        path: Path,
        operation: str = "access",
        auto_approve_workspace: bool = True,
        session_timeout: int = 3600  # 1 hour in seconds
    ) -> bool:
        """Ask user for permission to access a directory.

        Args:
            path: Directory path to request access to
            operation: What operation will be performed ("access", "read", "write", "delete")
            auto_approve_workspace: Whether to auto-allow workspace directory
            session_timeout: How long to remember permission in seconds

        Returns:
            True if user grants permission
        """
        from .config import load_settings

        # Check if already allowed
        if await self.is_allowed(path):
            logger.info("Directory already allowed: %s", path)
            return True

        # Auto-approve workspace if enabled
        if auto_approve_workspace:
            settings = load_settings()
            if path == settings.workspace_dir:
                await self.grant_permanent_access(path, "Workspace")
                logger.info("Auto-approved workspace directory: %s", path)
                return True

        # Check for existing temporary permission
        if str(path) in self.temp_permissions:
            temp_perm = self.temp_permissions[str(path)]
            if datetime.now() < temp_perm.expires_at:
                logger.info("Using temporary permission for %s", path)
                return True

        # Request permission from user
        return await self._ask_user_permission(path, operation, session_timeout)

    async def _ask_user_permission(
        self,
        path: Path,
        operation: str,
        session_timeout: int
    ) -> bool:
        """Ask user for permission using indication system."""
        ind = get_indication()
        if ind is None:
            logger.warning("No indication system available for permission request")
            # Fallback to CLI prompt
            return await self._cli_prompt_permission(path, operation)

        # Send notification to user
        message = f"Request permission to {operation} directory: {path}"
        try:
            ind.notify(IndicationKind.PERMISSION_REQUEST, body=message)
        except Exception as e:
            logger.error("Failed to send permission notification: %s", e)
            return await self._cli_prompt_permission(path, operation)

        # Wait for user response (this would need to be implemented differently)
        # For now, we'll implement a simple CLI fallback
        return await self._cli_prompt_permission(path, operation)

    async def _cli_prompt_permission(self, path: Path, operation: str) -> bool:
        """CLI fallback for permission prompts."""
        print(f"\n[Permission Request]")
        print(f"Operation: {operation}")
        print(f"Directory: {path}")
        print(f"\nDo you want to allow access to this directory? (y/N): ", end="")

        try:
            import sys
            response = sys.stdin.readline().strip().lower()

            if response in ('y', 'yes'):
                await self.grant_session_permission(path, f"CLI-approved {operation}")
                return True
            else:
                return False
        except (EOFError, KeyboardInterrupt):
            print("\nPermission denied.")
            return False

    async def is_allowed(self, path: Path, current_session_permissions: Optional[Set[Path]] = None) -> bool:
        """Check if directory is allowed."""
        # Check session permissions
        if current_session_permissions and path in current_session_permissions:
            return True

        # Check temporary permissions
        if str(path) in self.temp_permissions:
            temp_perm = self.temp_permissions[str(path)]
            if datetime.now() < temp_perm.expires_at:
                return True

        # Check profile permissions
        profile_manager = await get_profile_manager(self.profile_path)
        return profile_manager.is_directory_allowed(path)

    def is_temporarily_allowed(self, path: Path) -> bool:
        """Check if directory has temporary permission."""
        if str(path) not in self.temp_permissions:
            return False

        temp_perm = self.temp_permissions[str(path)]
        return datetime.now() < temp_perm.expires_at

    async def grant_permanent_access(self, path: Path, label: Optional[str] = None) -> None:
        """Add to user profile's allowed_directories."""
        profile_manager = await get_profile_manager(self.profile_path)
        await profile_manager.add_allowed_directory(path, label)
        logger.info("Granted permanent access to: %s", path)

    async def grant_temporary_permission(
        self,
        path: Path,
        label: Optional[str] = None,
        duration_hours: int = 1
    ) -> None:
        """Grant temporary permission for a directory."""
        expires_at = datetime.now() + timedelta(hours=duration_hours)

        self.temp_permissions[str(path)] = TemporaryPermission(
            path=path,
            granted_at=datetime.now(),
            expires_at=expires_at,
            label=label
        )

        logger.info("Granted temporary access to %s until %s", path, expires_at)

    async def grant_session_permission(self, path: Path, label: Optional[str] = None) -> None:
        """Grant permission for current session only."""
        self.session_permissions.add(path)
        logger.info("Granted session permission to: %s", path)

    def revoke_session_permission(self, path: Path) -> bool:
        """Revoke session permission."""
        if path in self.session_permissions:
            self.session_permissions.remove(path)
            logger.info("Revoked session permission for: %s", path)
            return True
        return False

    async def revoke_temporary_permission(self, path: Path) -> bool:
        """Revoke temporary permission."""
        if str(path) in self.temp_permissions:
            del self.temp_permissions[str(path)]
            logger.info("Revoked temporary permission for: %s", path)
            return True
        return False

    def get_session_permissions(self) -> Set[Path]:
        """Get all session permissions."""
        return set(self.session_permissions)

    def get_temporary_permissions(self) -> List[TemporaryPermission]:
        """Get all temporary permissions."""
        return list(self.temp_permissions.values())

    async def clear_expired_permissions(self) -> int:
        """Clear all expired permissions and return count cleared."""
        await self._cleanup_expired_permissions()

        # Clear expired session permissions (none expire by design)
        # This could be enhanced to support session timeouts

        return 0

    async def list_permissions(self, include_expired: bool = False) -> Dict[str, List[Dict]]:
        """List all permissions by type."""
        result = {
            "session": [],
            "temporary": [],
            "permanent": [],
        }

        # Session permissions
        for path in self.session_permissions:
            result["session"].append({
                "path": str(path),
                "type": "session",
                "granted_at": datetime.now().isoformat(),
            })

        # Temporary permissions
        for perm in self.temp_permissions.values():
            is_expired = datetime.now() > perm.expires_at
            if include_expired or not is_expired:
                result["temporary"].append({
                    "path": str(perm.path),
                    "label": perm.label,
                    "granted_at": perm.granted_at.isoformat(),
                    "expires_at": perm.expires_at.isoformat(),
                    "expired": is_expired,
                })

        # Permanent permissions (from profile)
        profile_manager = await get_profile_manager(self.profile_path)
        if profile_manager.profile:
            for item in profile_manager.profile.allowed_directories:
                result["permanent"].append({
                    "path": item["path"],
                    "label": item.get("label", item["path"]),
                    "approved_at": item.get("approved_at"),
                    "type": "permanent",
                })

        return result

    async def revoke_all_permissions(self, permission_type: str = "all") -> int:
        """Revoke permissions by type.

        Args:
            permission_type: "session", "temporary", "permanent", or "all"

        Returns:
            Number of permissions revoked
        """
        revoked_count = 0

        if permission_type in ("session", "all"):
            revoked_count += len(self.session_permissions)
            self.session_permissions.clear()

        if permission_type in ("temporary", "all"):
            revoked_count += len(self.temp_permissions)
            self.temp_permissions.clear()

        if permission_type in ("permanent", "all"):
            profile_manager = await get_profile_manager(self.profile_path)
            if profile_manager.profile:
                original_count = len(profile_manager.profile.allowed_directories)
                profile_manager.profile.allowed_directories.clear()
                await profile_manager.save()
                revoked_count += original_count

        logger.info("Revoked %d permissions of type: %s", revoked_count, permission_type)
        return revoked_count

    async def permission_summary(self, path: Path) -> Dict[str, any]:
        """Get permission summary for a specific path."""
        summary = {
            "path": str(path),
            "allowed": await self.is_allowed(path),
            "allowed_types": [],
            "permissions": {},
        }

        # Check session permissions
        if path in self.session_permissions:
            summary["allowed_types"].append("session")
            summary["permissions"]["session"] = {
                "type": "session",
                "description": "Allowed for current session",
            }

        # Check temporary permissions
        temp_perm = self.temp_permissions.get(str(path))
        if temp_perm:
            summary["allowed_types"].append("temporary")
            summary["permissions"]["temporary"] = {
                "type": "temporary",
                "granted_at": temp_perm.granted_at.isoformat(),
                "expires_at": temp_perm.expires_at.isoformat(),
                "label": temp_perm.label,
                "expired": datetime.now() > temp_perm.expires_at,
            }

        # Check permanent permissions
        profile_manager = await get_profile_manager(self.profile_path)
        if profile_manager.profile:
            for item in profile_manager.profile.allowed_directories:
                if item["path"] == str(path):
                    summary["allowed_types"].append("permanent")
                    summary["permissions"]["permanent"] = {
                        "type": "permanent",
                        "approved_at": item.get("approved_at"),
                        "label": item.get("label", item["path"]),
                    }
                    break

        return summary


# Global permission manager instance
_permission_manager: Optional[PermissionManager] = None


async def get_permission_manager(profile_path: Path) -> PermissionManager:
    """Get global permission manager instance."""
    global _permission_manager

    if _permission_manager is None:
        _permission_manager = PermissionManager(profile_path)
        await _permission_manager.initialize()

    return _permission_manager