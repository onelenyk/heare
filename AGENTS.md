# heare

macOS voice assistant: it listens, decides when to answer, and acts
through an agent (opencode over ACP) without blocking the conversation.
The app is twelve distributions under `packages/` sharing one `heare`
namespace; the old app that lived in `src/` was removed on
18 October 2026 and its last commit is tagged `legacy-app`.

## THE APP (`packages/`, twelve distributions)

One core and five kinds of part: **input, output, model, memory,
executor** (`packages/heare-core/heare/core/forms.py`). A channel (voice, text) is an input,
an output, or both. `packages/heare-brain/heare/api/server.py` builds the
door — the page, the events, the API the parts knock on.

HARD RULES:

- **The core imports only itself and the standard library.** No
  `aiohttp`, no `httpx`, no model SDK. The core's language is `Message`,
  `Port`, `Output`, `Executor` — everything else is a part.
- **A part imports only the core and itself.** Two parts never import
  each other. `tests/heare/test_dependencies.py` enforces both; run it
  after any import change.
- Import style: `from heare.core.conversation import Core` — always
  absolute, always the `heare.` prefix.

### LANGUAGE

- **What a model reads is English**: prompts, context blocks, part and
  skill descriptions, states, tool answers. Just write it in English.
- **What the person hears and sees goes through one catalog**: `_()` /
  `N_()` in Python, `tr()` / `data-t` in the pages, `tr()` in Swift; the key
  is the English line. Then `make i18n` and the Ukrainian in
  `packages/heare-core/heare/core/locale/uk/LC_MESSAGES/heare.po`.
- **The person's words are never translated**; «…» quotes them.
- `tests/heare/test_language_rule.py` fails on a word typed into the code,
  a marked line with no translation, a stale catalog. How and why:
  `docs/product/language.md`.

### THE RULE OF 2

When wiring a new capability, count the participants: one caller + one
implementation = the core calls it directly. If a third participant
needs it (a CLI, an agent, a second part), introduce the boundary —
protocol first, implementation second. Never abstract for one.

## Commands

```bash
uv sync
uv run heare-brain      # the brain, and the page on 127.0.0.1:9790
uv run heare-chat       # the conversation in a terminal
uv run heare-voice      # speech in, speech out

make test                         # the core against stand-ins: no network, no keys
make lint                         # ruff
make format                       # ruff format
make check                        # lint + test

make live                         # check, then put this checkout live on this Mac and wait
make i18n                         # find marked lines, merge into the catalogs, compile
make prompt                       # the system prompt the running brain gives its model now
make rollback                     # the release before (TO=name for another)
make releases                     # what is kept; ● is live
```

Never copy files into the installed tools by hand: `make live` is the
update, and the release it leaves behind is the way back.

Parts — separate processes that knock and wait for approval on the page:

```bash
uv run heare-cli-hands     # руки через агентний CLI (opencode)
uv run heare-native-hands  # швидкі руки: дії з тілами, без агента
uv run heare-eyes
uv run heare-voice
uv run heare-chat
uv run heare-keeper     # наглядач: піднімає частини з ~/.heare/machine/parts
```

Tests marked `spine_live` leave the machine — the real recogniser, the real
model — and are excluded by default.

## WHERE TO LOOK

| Task | Location |
|---|---|
| A spoken sentence becomes a reply | `packages/heare-core/heare/core/conversation.py` |
| The door: HTTP, events, page, parts registry | `packages/heare-brain/heare/api/server.py`, `packages/heare-brain/heare/api/page.html` |
| The canvas: what the model shows | `packages/heare-brain/heare/api/canvas.py` |
| Hands: an agent over ACP | `packages/heare-cli-hands/heare/cli_hands.py`, `packages/heare-reach/heare/reach/` |
| Native hands: quick acts on the parts, no agent | `packages/heare-native-hands/heare/native_hands.py`; the hands' life `heare-core/heare/hand.py`; a model for parts `heare-core/heare/talk.py` |
| Skills: what a part can do, calls with answers | `packages/heare-brain/heare/api/skills.py`; protocol §6; who gets the work `heare-brain/heare/model/route.py` |
| A bench for hands and bodies | `make bench` (`tests/heare/test_bench.py`), virtual boards `packages/heare-sim/heare/sim.py`; live: `make eval-route` |
| Hands' runner: which CLI, sign-in, models | `packages/heare-reach/heare/reach/runner.py` — the hands', never the brain's |
| How a part lives: knock, key, stream, reports, outliving the brain | `packages/heare-core/heare/part.py` — `part.live(about, work)`; every part uses it |
| Which parts the brain knows, trust, presence, who hears what | `packages/heare-brain/heare/api/registry.py` |
| Voice: hearing and speech | `packages/heare-voice/heare/voice.py`, `packages/heare-voice/heare/channels/voice/` |
| Eyes: what is around | `packages/heare-eyes/heare/eyes.py`, `packages/heare-eyes/heare/surroundings/` |
| Intents, reminders, moments, delivery | `packages/heare-brain/heare/engine/` |
| What the model sees: persona, laws, turn budget | `packages/heare-brain/heare/prompt/` |
| What the model can call: recall, delegate, remind | `packages/heare-brain/heare/tools/` |
| Profile, journal, recall, memory CLI | `packages/heare-memory/heare/memory/` |
| The plan | `docs/product/README.md`; what we do now: `docs/product/clock.md`; memory and activity: `docs/product/memory.md` |
| Language: the catalog, the page words, the Mac's | `packages/heare-core/heare/core/i18n.py`, `packages/heare-core/heare/words.py`, `apps/mac/Sources/heare/Words.swift`; `docs/product/language.md` |
| What the voice knows of its body | `packages/heare-brain/heare/prompt/body.py`; `docs/product/voice-context.md` |
| The laws | `packages/heare-core/heare/laws.py`, `docs/laws.md` |

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
