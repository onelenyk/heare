# heare

macOS voice assistant: it listens, decides when to answer, and acts
through an agent (opencode over ACP) without blocking the conversation.
The app is `heare/`; the old app that lived in `src/` was removed on
18 October 2026 and its last commit is tagged `legacy-app`.

## THE APP (`heare/`)

One core and five kinds of part: **input, output, model, memory,
executor** (`heare/core/forms.py`). A channel (voice, text) is an input,
an output, or both. `heare/app.py` builds the local app; `heare/api/server.py`
builds the door — the page, the events, the API the parts knock on.

HARD RULES:

- **The core imports only itself and the standard library.** No
  `aiohttp`, no `httpx`, no model SDK. The core's language is `Message`,
  `Port`, `Output`, `Executor` — everything else is a part.
- **A part imports only the core and itself.** Two parts never import
  each other. `tests/heare/test_dependencies.py` enforces both; run it
  after any import change.
- Import style: `from heare.core.conversation import Core` — always
  absolute, always the `heare.` prefix.

### THE RULE OF 2

When wiring a new capability, count the participants: one caller + one
implementation = the core calls it directly. If a third participant
needs it (a CLI, an agent, a second part), introduce the boundary —
protocol first, implementation second. Never abstract for one.

## Commands

```bash
uv sync
uv run python -m heare            # by typing
uv run python -m heare --voice    # aloud
uv run python -m heare --serve    # the page and the API on 127.0.0.1:9790

make test                         # the core against stand-ins: no network, no keys
make lint                         # ruff
make format                       # ruff format
make check                        # lint + test
```

Parts — separate processes that knock and wait for approval on the page:

```bash
uv run python -m heare.hands
uv run python -m heare.eyes
uv run python -m heare.voice
uv run python -m heare.chat
```

`uv run python -m heare.listener` drives real speech in and the reply out;
it pays for the recogniser and reads the real keys. Tests marked
`spine_live` do the same and are excluded by default.

## WHERE TO LOOK

| Task | Location |
|---|---|
| A spoken sentence becomes a reply | `heare/core/conversation.py` |
| The door: HTTP, events, page, parts registry | `heare/api/server.py`, `heare/api/page.html` |
| The canvas: what the model shows | `heare/api/canvas.py` |
| Hands: an agent over ACP | `heare/hands.py`, `heare/reach/` |
| Voice: hearing and speech | `heare/voice.py`, `heare/channels/voice/` |
| Eyes: what is around | `heare/eyes.py`, `heare/surroundings/` |
| Intents, moments, delivery | `heare/engine/` |
| The plan | `docs/product/README.md` |
| The laws | `heare/laws.py`, `docs/laws.md` |

## WRITING CODE

Write Python the way it reads. Type hints on public functions; docstrings
only where the reason for a choice is not obvious — the code says what,
the docstring says why it is that way and not the other. No `Any` where a
real type exists. No `# type: ignore` without a reason in a comment beside
it.

## PLANS

The live plan is `docs/product/`: the overall one plus one file per app
(brain, body-desktop, body-web, hands, protocol). Session notes and
drafts are not committed.
