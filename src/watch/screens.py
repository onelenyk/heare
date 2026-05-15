"""Modal screens used by the watch dashboard."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, ListItem, ListView

from . import models

ADD_MARKER = "+ Add custom…"


class ModelSelectScreen(ModalScreen[str | None]):
    """Modal dialog: pick an LLM model for the active provider.

    Shows the hardcoded shortlist plus any custom models the operator has
    added previously, with an "+ Add custom…" entry at the bottom that
    opens an Input. Returns the chosen model id via ``dismiss()``; ``None``
    on Esc.
    """

    BINDINGS = (
        Binding("escape", "dismiss_none", "Cancel"),
    )

    DEFAULT_CSS = """
    ModelSelectScreen {
        align: center middle;
    }

    #model-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #model-dialog Label.title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #model-dialog ListView {
        height: auto;
        max-height: 16;
        border: tall $surface-darken-2;
    }

    #model-dialog Input {
        margin-top: 1;
        border: tall $accent;
    }
    """

    def __init__(self, settings, provider: str, current: str) -> None:
        super().__init__()
        self._settings = settings
        self._provider = provider
        self._current = current
        self._add_input: Input | None = None

    def compose(self) -> ComposeResult:
        items = self._build_items()
        with Vertical(id="model-dialog"):
            yield Label(f"Select model — provider: {self._provider}", classes="title")
            yield ListView(*items, id="model-list")

    def _build_items(self) -> list[ListItem]:
        ids = models.models_for_provider(self._settings, self._provider)
        items: list[ListItem] = []
        for mid in ids:
            marker = "● " if mid == self._current else "  "
            items.append(ListItem(Label(f"{marker}{mid}"), id=f"m-{len(items)}"))
        items.append(ListItem(Label(ADD_MARKER), id="m-add"))
        return items

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if item is None:
            return
        label_widget = item.query_one(Label)
        text = str(label_widget.renderable).strip()
        if text == ADD_MARKER:
            self._open_add_input()
            return
        # Strip the leading bullet ("●  " or "  ")
        if text.startswith("● "):
            text = text[2:].strip()
        self.dismiss(text)

    def _open_add_input(self) -> None:
        if self._add_input is not None:
            return
        self._add_input = Input(placeholder="Type model id and press Enter")
        self.query_one("#model-dialog", Vertical).mount(self._add_input)
        self._add_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        model_id = event.value.strip()
        if not model_id:
            return
        models.add_custom_model(self._settings, self._provider, model_id)
        self.dismiss(model_id)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ChromeProfileSelectScreen(ModalScreen[str | None]):
    """Modal dialog: pick which Chrome profile to launch with the CDP debug port.

    Reads profiles from ``list_chrome_profiles()`` (Chrome's ``Local State``
    file). Returns the chosen ``--profile-directory`` value via ``dismiss()``;
    ``None`` on Esc.
    """

    BINDINGS = (
        Binding("escape", "dismiss_none", "Cancel"),
    )

    DEFAULT_CSS = """
    ChromeProfileSelectScreen {
        align: center middle;
    }

    #chrome-dialog {
        width: 70;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #chrome-dialog Label.title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #chrome-dialog ListView {
        height: auto;
        max-height: 16;
        border: tall $surface-darken-2;
    }
    """

    def __init__(self, profiles) -> None:
        super().__init__()
        self._profiles = profiles

    def compose(self) -> ComposeResult:
        items: list[ListItem] = []
        for i, p in enumerate(self._profiles):
            marker = "● " if p.last_used else "  "
            label = f"{marker}{p.directory}"
            if p.name:
                label += f"  —  {p.name}"
            if p.email:
                label += f"  ({p.email})"
            items.append(ListItem(Label(label), id=f"cp-{i}"))
        with Vertical(id="chrome-dialog"):
            yield Label("Select Chrome profile to launch with CDP", classes="title")
            if not items:
                yield Label("(no profiles found — has Chrome ever run?)")
                return
            yield ListView(*items, id="chrome-profile-list")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if item is None or item.id is None or not item.id.startswith("cp-"):
            return
        idx = int(item.id.split("-", 1)[1])
        self.dismiss(self._profiles[idx].directory)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ToolingScreen(ModalScreen[None]):
    """Read-only modal: every capability the agent can call, in one table.

    Three buckets (built-in tools, skills, MCP servers) are concatenated
    into a single DataTable with a SOURCE column so the operator can scan
    them all without flipping panes. Data is fetched synchronously from
    the same helpers the LLM's list_capabilities tool uses, so the table
    is authoritative — what you see here is what the agent sees.
    """

    BINDINGS = (
        Binding("escape", "dismiss_none", "Close"),
        Binding("q", "dismiss_none", "Close"),
    )

    DEFAULT_CSS = """
    ToolingScreen {
        align: center middle;
    }

    #tooling-dialog {
        width: 90%;
        max-width: 120;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #tooling-dialog Label.title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #tooling-dialog DataTable {
        height: 1fr;
    }
    """

    def __init__(self, settings) -> None:
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        rows, totals = self._gather()
        title = (
            f"Available tooling — "
            f"{totals['built_in']} built-in · "
            f"{totals['skills']} skills · "
            f"{totals['mcps']} MCP   (Esc to close)"
        )
        with Vertical(id="tooling-dialog"):
            yield Label(title, classes="title")
            table: DataTable = DataTable(zebra_stripes=True, cursor_type="row")
            table.add_columns("SOURCE", "NAME", "DESCRIPTION")
            for source, name, description in rows:
                table.add_row(source, name, description)
            yield table

    def _gather(self) -> tuple[list[tuple[str, str, str]], dict[str, int]]:
        from src.agent.tools.direct import (
            _list_built_in_tools,
            _list_mcp_servers,
            _list_skills,
        )

        built_in = _list_built_in_tools()
        skills = _list_skills(self._settings)
        mcps = _list_mcp_servers(self._settings)
        rows: list[tuple[str, str, str]] = []
        for item in built_in:
            rows.append(("built-in", item["name"], item.get("description", "")))
        for item in skills:
            rows.append(("skill", item["name"], item.get("description", "")))
        for item in mcps:
            rows.append(("mcp", item["name"], item.get("description", "")))
        totals = {
            "built_in": len(built_in),
            "skills": len(skills),
            "mcps": len(mcps),
        }
        return rows, totals

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
