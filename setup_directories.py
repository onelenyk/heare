#!/usr/bin/env python3
"""
Setup configuration for heare agent with specified source code and workspace directories.
This script configures the agent to know where its source code is and where it can safely create files.
"""

import os
import sys
import json
from pathlib import Path

def setup_directories():
    """Configure source code and workspace directories."""

    # Current project (heare source code)
    source_code_dir = Path(__file__).parent.resolve()
    print(f"📁 Source code directory: {source_code_dir}")

    # Workspace directory (where agent can create files)
    HEARE_HOME = Path.home() / ".heare"
    workspace_dir = HEARE_HOME / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 Workspace directory: {workspace_dir}")

    return source_code_dir, workspace_dir, HEARE_HOME


def setup_profile(source_code_dir, workspace_dir, HEARE_HOME):
    """Setup user profile with allowed directories and preferences."""

    profile_path = HEARE_HOME / "profile.json"

    # Default profile data
    profile_data = {
        "allowed_directories": [
            {"path": str(workspace_dir), "label": "heare-workspace"},
            {"path": str(source_code_dir), "label": "heare-source"}
        ],
        "favorite_locations": [
            {"path": str(source_code_dir), "label": "Heare Source Code"},
            {"path": str(workspace_dir), "label": "Heare Workspace"},
            {"path": str(Path.home() / "projects"), "label": "My Projects"},
            {"path": str(Path.home() / "Documents"), "label": "Documents"}
        ],
        "ignored_patterns": [
            "*.git", "*.tmp", "*.log", "__pycache__", "*.pyc",
            "node_modules", "*.env", ".DS_Store"
        ],
        "view_preferences": {
            "show_hidden": False,
            "detail_level": "standard",
            "sort_by": "name",
            "sort_order": "asc"
        },
        "search_history": [],
        "access_log": []
    }

    # Save profile
    with open(profile_path, "w") as f:
        json.dump(profile_data, f, indent=2)

    print(f"✅ Profile created: {profile_path}")
    return profile_data


def create_workspace_files(workspace_dir):
    """Create useful files in the workspace."""

    # Create a notes file
    notes_file = workspace_dir / "heare-notes.md"
    if not notes_file.exists():
        notes_content = """# Heare Agent Notes

This is your workspace where you can create and manage files.

## Common Commands You Can Ask:
- "List my workspace"
- "Find notes in my workspace"
- "Create a new file for my project"
- "Organize my documents"
- "Backup important files"

## Workspace Structure:
- `~/.heare/workspace/` - Your main workspace
- `~/.heare/logs/` - Application logs
- `~/.heare/profile.json` - Your preferences

## Tips:
- The agent remembers your favorites across sessions
- You can ask to archive files for backup
- Batch operations help with multiple files
"""
        notes_file.write_text(notes_content)
        print(f"✅ Created notes file: {notes_file}")

    # Create a sample project directory
    sample_dir = workspace_dir / "sample-project"
    if not sample_dir.exists():
        sample_dir.mkdir()
        (sample_dir / "README.md").write_text("# Sample Project\n\nThis is a sample project structure.")
        (sample_dir / "main.py").write_text("print('Hello from heare!')\n")
        print(f"✅ Created sample project: {sample_dir}")

    # Create a todo list
    todo_file = workspace_dir / "todo.txt"
    if not todo_file.exists():
        todo_content = """# Heare Agent Todo List

Ideas for what to ask the agent to do:
- [ ] List all Python files in my project
- [ ] Create a backup of my important documents
- [ ] Find and organize duplicate files
- [ ] Archive old project files
- [ ] Create a new project structure
- [ ] Search for specific content across files
"""
        todo_file.write_text(todo_content)
        print(f"✅ Created todo file: {todo_file}")


def update_config_toml(HEARE_HOME):
    """Update config.toml with directory settings."""

    config_path = HEARE_HOME / "config.toml"

    # Add file access settings if not present
    config_content = f"""# heare runtime config — picked up by load_settings()
use_agent_sdk = true
speaker_id_enabled = true
speaker_id_threshold_match = 0.45
speaker_id_min_duration_ms = 400
confirmation_passphrase = "авторизую"
proactivity_level = "high"

# Conversation memory — s2s-integration branch
turn_aggregation_enabled = true
conversation_memory_enabled = true
topic_extraction_enabled = true

# s2s-realtime Phase 1 — generator pipeline via OpenRouter
generator_mode = true

# TTS voice - use Ukrainian voice
tts_voice = "uk-UA-PolinaNeural"

# File access settings
file_access_auto_approve_workspace = true
file_access_ask_for_new_dirs = true
file_access_max_archive_size = 1073741824
file_access_operation_timeout = 300
"""

    with open(config_path, "w") as f:
        f.write(config_content)

    print(f"✅ Config updated: {config_path}")


def main():
    """Main setup function."""

    print("🚀 Setting up heare agent directories...\n")

    # Setup directories
    source_code_dir, workspace_dir, HEARE_HOME = setup_directories()

    # Setup profile
    profile_data = setup_profile(source_code_dir, workspace_dir, HEARE_HOME)

    # Update config
    update_config_toml(HEARE_HOME)

    # Create useful files
    create_workspace_files(workspace_dir)

    # Ensure logs directory exists
    logs_dir = HEARE_HOME / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    print("\n✅ Configuration complete!")
    print("\n📋 Summary:")
    print(f"   Source code: {source_code_dir}")
    print(f"   Workspace: {workspace_dir}")
    print(f"   Profile: {HEARE_HOME / 'profile.json'}")
    print(f"   Config: {HEARE_HOME / 'config.toml'}")
    print("\n🎯 Now you can ask:")
    print("   - 'List my workspace'")
    print("   - 'Go to my source code directory'")
    print("   - 'Create a new project in my workspace'")
    print("   - 'Show me my favorite locations'")
    print("   - 'Help me organize my files'")
    print("   - 'Find all Python files in my project'")
    print("   - 'Archive my project files'")

    print("\n💡 Pro tip: The agent now knows about:")
    print("   - Your heare source code location")
    print("   - Your workspace for creating files")
    print("   - Your favorite locations")
    print("   - Common patterns to ignore")


if __name__ == "__main__":
    main()