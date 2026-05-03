"""Modal screens used by the watch dashboard."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView

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
