"""Tests for extended file access capabilities."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.direct_tools import (
    _execute_list_directory,
    _execute_find_files,
    _execute_get_tree_view,
    _execute_get_current_directory,
    _execute_get_file_info,
    _execute_get_disk_usage,
    _execute_get_file_hash,
    _execute_copy_file,
    _execute_move_file,
    _execute_delete_file,
    _execute_create_directory,
    _execute_create_archive,
    _execute_extract_archive,
    _execute_batch_operation,
    _execute_add_favorite,
    _execute_list_favorites,
    _execute_set_view_preference,
    _execute_show_profile,
)
from src.user_profile import UserProfile, ProfileManager
from src.permission_manager import PermissionManager
from src.config import Settings


class TestExtendedFileAccess(unittest.TestCase):
    """Test cases for extended file access tools."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.settings = Settings()
        self.settings.workspace_dir = self.temp_dir

        # Create test files and directories
        (self.temp_dir / "test_file.txt").write_text("Hello, World!")
        (self.temp_dir / "subdir").mkdir()
        (self.temp_dir / "subdir" / "nested.txt").write_text("Nested content")
        (self.temp_dir / "test_file.py").write_text("print('hello')")

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir)

    async def test_list_directory(self):
        """Test directory listing."""
        result = await _execute_list_directory(str(self.temp_dir), self.settings)
        self.assertTrue(result["success"])
        self.assertIn("items", result)
        self.assertTrue(len(result["items"]) > 0)

        # Test with different detail levels
        result = await _execute_list_directory(f"{self.temp_dir} detailed", self.settings)
        self.assertTrue(result["success"])

    async def test_find_files(self):
        """Test file finding."""
        result = await _execute_find_files(f"{self.temp_dir} *.txt", self.settings)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["items"]), 1)  # Only test_file.txt should match

        # Test recursive search
        result = await _execute_find_files(f"{self.temp_dir} *.txt recursive=true", self.settings)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["items"]), 2)  # test_file.txt and nested.txt

    async def test_get_tree_view(self):
        """Test directory tree view."""
        result = await _execute_get_tree_view(str(self.temp_dir), self.settings)
        self.assertTrue(result["success"])
        self.assertIn("test_file.txt", result["output"])

    async def test_get_current_directory(self):
        """Test current directory retrieval."""
        import os
        os.chdir(self.temp_dir)
        result = await _execute_get_current_directory("", self.settings)
        self.assertTrue(result["success"])
        self.assertEqual(result["output"], str(self.temp_dir))

    async def test_get_file_info(self):
        """Test file information retrieval."""
        test_file = self.temp_dir / "test_file.txt"
        result = await _execute_get_file_info(str(test_file), self.settings)
        self.assertTrue(result["success"])
        self.assertIn("size", result["info"])
        self.assertEqual(result["info"]["size"], 13)  # "Hello, World!" is 13 bytes

    async def test_get_disk_usage(self):
        """Test disk usage calculation."""
        result = await _execute_get_disk_usage(str(self.temp_dir), self.settings)
        self.assertTrue(result["success"])
        self.assertIn("usage", result)

    async def test_get_file_hash(self):
        """Test file hash calculation."""
        test_file = self.temp_dir / "test_file.txt"
        result = await _execute_get_file_hash(str(test_file), self.settings)
        self.assertTrue(result["success"])
        self.assertIn("hash", result["hash"])

    async def test_copy_file(self):
        """Test file copying."""
        source = self.temp_dir / "test_file.txt"
        dest = self.temp_dir / "copy.txt"
        result = await _execute_copy_file(f"{source} {dest}", self.settings)
        self.assertTrue(result["success"])
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_text(), "Hello, World!")

    async def test_move_file(self):
        """Test file moving."""
        source = self.temp_dir / "test_file.txt"
        dest = self.temp_dir / "moved.txt"
        result = await _execute_move_file(f"{source} {dest}", self.settings)
        self.assertTrue(result["success"])
        self.assertFalse(source.exists())
        self.assertTrue(dest.exists())

    async def test_create_directory(self):
        """Test directory creation."""
        new_dir = self.temp_dir / "new_dir"
        result = await _execute_create_directory(str(new_dir), self.settings)
        self.assertTrue(result["success"])
        self.assertTrue(new_dir.exists())

    async def test_create_archive(self):
        """Test archive creation."""
        archive_path = self.temp_dir / "test.tar.gz"
        sources = [str(self.temp_dir / "test_file.txt")]
        result = await _execute_create_archive(f"{archive_path} {sources} tar.gz", self.settings)
        self.assertTrue(result["success"])
        self.assertTrue(archive_path.exists())

    async def test_extract_archive(self):
        """Test archive extraction."""
        # First create an archive
        archive_path = self.temp_dir / "test.tar.gz"
        sources = [str(self.temp_dir / "test_file.txt")]
        await _execute_create_archive(f"{archive_path} {sources} tar.gz", self.settings)

        # Then extract it
        extract_dir = self.temp_dir / "extracted"
        result = await _execute_extract_archive(f"{archive_path} {extract_dir} overwrite=true", self.settings)
        self.assertTrue(result["success"])
        self.assertTrue((extract_dir / "test_file.txt").exists())

    async def test_batch_operation_list_info(self):
        """Test batch operation for listing info."""
        result = await _execute_batch_operation("list_info *.txt", self.settings)
        self.assertTrue(result["success"])
        self.assertEqual(result["result"]["matched_count"], 1)

    async def test_batch_operation_delete(self):
        """Test batch operation for deletion."""
        result = await _execute_batch_operation("delete *.py dry_run=true", self.settings)
        self.assertTrue(result["success"])
        self.assertEqual(result["result"]["dry_run"], True)


class TestUserProfile(unittest.TestCase):
    """Test cases for user profile system."""

    def setUp(self):
        """Set up test environment."""
        self.profile_path = Path(tempfile.mkdtemp()) / "profile.json"

    async def test_profile_creation(self):
        """Test profile creation and saving."""
        manager = ProfileManager(self.profile_path)
        await manager.load()

        # Add a favorite
        await manager.add_favorite(Path("/test/path"), "Test Path")
        favorites = manager.get_favorites()
        self.assertEqual(len(favorites), 1)
        self.assertEqual(favorites[0]["path"], "/test/path")

    async def test_persistence(self):
        """Test profile persistence across reloads."""
        manager = ProfileManager(self.profile_path)
        await manager.load()

        # Add some data
        await manager.add_favorite(Path("/test1"), "Test 1")
        await manager.add_favorite(Path("/test2"), "Test 2")

        # Reload manager
        new_manager = ProfileManager(self.profile_path)
        await new_manager.load()

        # Check data persists
        favorites = new_manager.get_favorites()
        self.assertEqual(len(favorites), 2)


class TestPermissionManager(unittest.TestCase):
    """Test cases for permission manager."""

    def setUp(self):
        """Set up test environment."""
        self.profile_path = Path(tempfile.mkdtemp()) / "profile.json"
        self.perm_manager = PermissionManager(self.profile_path)
        self.perm_manager.auto_approve_workspace = False

    async def test_directory_permission_checking(self):
        """Test directory permission checking."""
        test_path = Path("/test/dir")

        # Initially should not be allowed
        self.assertFalse(await self.perm_manager.is_allowed(test_path))

        # Add permanent permission
        await self.perm_manager.grant_permanent_access(test_path, "Test Directory")
        self.assertTrue(await self.perm_manager.is_allowed(test_path))

    async def test_temporary_permissions(self):
        """Test temporary permission expiration."""
        test_path = Path("/test/temp")

        # Grant temporary permission
        await self.perm_manager.grant_temporary_permission(test_path, "Temp", duration_hours=1)
        self.assertTrue(self.perm_manager.is_temporarily_allowed(test_path))

        # Simulate time passage (would need proper time mocking in real tests)
        # For now, just check the permission exists
        self.assertTrue(str(test_path) in self.perm_manager.temp_permissions)


class TestIntegration(unittest.TestCase):
    """Integration tests for the complete system."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.settings = Settings()
        self.settings.workspace_dir = self.temp_dir

        # Create test structure
        (self.temp_dir / "src").mkdir()
        (self.temp_dir / "src" / "main.py").write_text("print('hello')")
        (self.temp_dir / "docs").mkdir()
        (self.temp_dir / "docs" / "readme.md").write_text("# Documentation")

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir)

    async def test_complete_workflow(self):
        """Test a complete file management workflow."""
        # 1. List directory
        result = await _execute_list_directory(str(self.temp_dir), self.settings)
        self.assertTrue(result["success"])

        # 2. Find specific files
        result = await _execute_find_files(f"{self.temp_dir} *.py", self.settings)
        self.assertTrue(result["success"])
        self.assertEqual(result["matched_count"], 1)

        # 3. Get file info
        result = await _execute_get_file_info(str(self.temp_dir / "src" / "main.py"), self.settings)
        self.assertTrue(result["success"])

        # 4. Create directory
        new_dir = self.temp_dir / "backup"
        result = await _execute_create_directory(str(new_dir), self.settings)
        self.assertTrue(result["success"])

        # 5. Copy file
        result = await _execute_copy_file(
            f"{self.temp_dir / 'src' / 'main.py'} {new_dir / 'main.py'}",
            self.settings
        )
        self.assertTrue(result["success"])

        # 6. Create archive
        archive_path = self.temp_dir / "backup.tar.gz"
        result = await _execute_create_archive(
            f"{archive_path} {str(new_dir)}",
            self.settings
        )
        self.assertTrue(result["success"])

        # 7. Clean up
        result = await _execute_delete_file(str(new_dir), self.settings)
        self.assertTrue(result["success"])


if __name__ == "__main__":
    # Run async tests
    unittest.main(verbosity=2)