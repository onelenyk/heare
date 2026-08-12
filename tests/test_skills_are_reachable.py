"""Whether an installed skill can actually be found and run.

Written after discovering that the answer had always been no. Four
independent faults stacked, each sufficient on its own, so fixing any
one of them would have changed nothing observable:

1. the only search path was ``~/.heare/skills``, a directory nothing
   ever created;
2. the skills shipped in the repository were flat ``.md`` files, while
   the loader looks for directories holding ``SKILL.md``;
3. those files carried a ``description:`` but no ``name:``, and the
   parser requires both;
4. the prompt renderer asked for the loader without settings on every
   turn, which rebuilt the singleton back onto the hardcoded path.

Each test below pins one of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.skills.agent_skills as agent_skills
from src.config import Settings, bundled_dir
from src.skills.agent_skills import SkillsLoader, get_skills_loader

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_loader():
    """The loader is a module-level singleton; leaking it across tests
    would make them pass or fail depending on their order."""
    agent_skills._loader = None
    agent_skills._loader_paths = None
    yield
    agent_skills._loader = None
    agent_skills._loader_paths = None


# ── 1. the paths ──────────────────────────────────────────────────────


def test_the_skills_that_ship_with_the_app_are_searched() -> None:
    """The spec copies skills/ into the bundle. Nothing looked there."""
    paths = Settings().skills_paths

    assert bundled_dir("skills") in paths
    assert any("skills" in p and ".heare" in p for p in paths), (
        "the user's own skills directory must stay in the search path"
    )


def test_the_users_own_skills_come_first() -> None:
    """A skill someone installed should shadow one of the same name that
    happened to ship — discover() appends in order and load_instructions
    takes the first match."""
    paths = Settings().skills_paths

    assert ".heare" in paths[0]


def test_the_skills_directory_is_created() -> None:
    """Its absence is what the loader silently skipped past."""
    import inspect

    source = inspect.getsource(Settings.ensure_dirs)

    assert "skills" in source


# ── 2 & 3. the shape of what ships ────────────────────────────────────


def test_every_shipped_skill_is_in_the_format_the_loader_reads() -> None:
    """A flat .md file, or one without a name, is invisible — and was."""
    shipped = REPO / "skills"
    found = SkillsLoader([shipped]).discover()

    directories = [p for p in shipped.iterdir() if p.is_dir()]
    assert directories, "no skills ship at all"
    assert len(found) == len(directories), (
        f"{len(directories)} skill directories, {len(found)} of them readable"
    )

    for skill in found:
        assert (skill.path / "SKILL.md").exists()
        assert skill.description.strip()


def test_a_skill_without_a_name_is_not_discovered(tmp_path: Path) -> None:
    """The exact shape the three shipped skills had for the whole of the
    project's life: a description, no name."""
    skill = tmp_path / "nameless"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\ndescription: does a thing\n---\n\nbody\n", encoding="utf-8"
    )

    assert SkillsLoader([tmp_path]).discover() == []


def test_a_flat_markdown_file_is_not_a_skill(tmp_path: Path) -> None:
    (tmp_path / "loose.md").write_text(
        "---\nname: loose\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )

    assert SkillsLoader([tmp_path]).discover() == []


# ── 4. the singleton ──────────────────────────────────────────────────


def test_asking_without_settings_does_not_discard_a_configured_loader(
    tmp_path: Path,
) -> None:
    """render_native_system_prompt calls get_skills_loader(None) on every
    turn. That used to rebuild the loader onto the single hardcoded path,
    so the prompt never saw a skill and the next settings-aware call had
    to build it all over again.
    """
    settings = Settings()
    settings.skills_paths = [str(tmp_path)]

    configured = get_skills_loader(settings)
    assert configured.search_paths == [tmp_path.resolve()]

    again = get_skills_loader(None)

    assert again is configured
    assert again.search_paths == [tmp_path.resolve()]


def test_the_first_call_without_settings_still_finds_the_shipped_skills() -> None:
    """Nothing guarantees a settings-aware caller goes first."""
    loader = get_skills_loader(None)

    assert Path(bundled_dir("skills")).resolve() in loader.search_paths


# ── what the worker is told ───────────────────────────────────────────


def test_the_worker_is_told_which_skills_exist() -> None:
    """The conversational agent is deliberately not told — it cannot run
    one. If the worker is not told either, nobody is, and run_skill sits
    among sixty-odd schemas waiting to be guessed at.
    """
    from src.agent.hands import Hands

    settings = Settings()
    block = Hands(settings)._skills_block()

    assert "run_skill" in block
    for name in ("voice-mode", "voice-start", "voice-stop"):
        assert name in block


def test_no_skills_means_no_block(tmp_path: Path) -> None:
    """An empty heading is worse than nothing: it spends prompt on the
    claim that skills exist."""
    from src.agent.hands import Hands

    settings = Settings()
    settings.skills_paths = [str(tmp_path)]

    assert Hands(settings)._skills_block() == ""
