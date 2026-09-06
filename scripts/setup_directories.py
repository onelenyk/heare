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
    """Merge default settings into config.toml — never overwrite it.

    ``load_settings()`` (src/config.py) treats config.toml as the user's
    file: existing keys (including ``engine = "spine"``, which selects the
    live voice engine over the retired pipecat one) and all comments must
    survive untouched. Only keys this script cares about, and that are
    still read somewhere in ``src/`` on the live engine, are added — and
    only if they are not already present. Table sections (``[browser_bridge]``
    etc.) are never touched, matching ``write_config_toml_values`` in
    src/config.py.
    """
    import re

    config_path = HEARE_HOME / "config.toml"
    content = config_path.read_text() if config_path.exists() else ""

    # `file_access_max_archive_size` is the one key from this script's old
    # payload that anything still reads (src/agent/tools/direct.py, the
    # archive tools reachable from the live engine's delegate worker). The
    # rest of the old payload (use_agent_sdk, speaker_id_*,
    # confirmation_passphrase, proactivity_level, turn_aggregation_enabled,
    # conversation_memory_enabled, topic_extraction_enabled, generator_mode,
    # tts_voice, file_access_auto_approve_workspace,
    # file_access_ask_for_new_dirs, file_access_operation_timeout) is dead:
    # either read by nothing in src/, or read only on the retired pipecat
    # path that the live engine never runs. Dropped rather than merged.
    defaults = {
        "file_access_max_archive_size": 1073741824,
    }

    first_table = re.search(r"(?m)^[ \t]*\[", content)
    head = content[: first_table.start()] if first_table else content
    tail = content[first_table.start() :] if first_table else ""

    added = []
    for key, value in defaults.items():
        key_re = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=")
        if key_re.search(head):
            continue  # already set by the user — leave it alone
        if head and not head.endswith("\n"):
            head += "\n"
        literal = json.dumps(value) if isinstance(value, str) else value
        head += f"{key} = {literal}\n"
        added.append(key)

    if not config_path.exists():
        head = "# heare runtime config — picked up by load_settings()\n" + head

    if tail and added and not head.endswith("\n\n"):
        head += "\n"

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        f.write(head + tail)

    if added:
        print(f"✅ Config updated: {config_path} (added {', '.join(added)})")
    else:
        print(f"✅ Config already complete, left untouched: {config_path}")


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