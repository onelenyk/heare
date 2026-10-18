# heare

macOS voice assistant: it listens, decides when to answer, and acts
through an agent (opencode over ACP) without blocking the conversation.

The app is nine distributions under `packages/` — one core and five
kinds of part (input, output,
model, memory, executor; `packages/heare-core/heare/core/forms.py`). A channel (voice, text)
is an input, an output, or both. Parts connect to the brain's door over
HTTP, knock with a key, and wait for the person's approval on the page.

The old app that lived in `src/` was removed on 18 October 2026; its last
commit is tagged `legacy-app`.

## Requirements

- macOS with a microphone and speaker; `brew install portaudio`
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- keys for the model and the speech: set on the page, or in the Keychain
  (service `heare`), or in `~/.heare/.env`

## Run

```bash
uv sync
uv run heare            # by typing
uv run heare --voice    # aloud
uv run heare --serve    # the page and the API on 127.0.0.1:9790
```

The page arrives without a token baked in: it is handed one when served.
The parts are separate processes and knock on the brain:

```bash
uv run heare-hands      # the agent: files, commands, search
uv run heare-eyes       # what is around: the front app, sound
uv run heare-voice      # speech in, speech out
uv run heare-chat       # the conversation in a terminal
```

## Test

```bash
make test     # the core against stand-ins: no network, no keys, no audio
make check    # lint + test
```

`spine_live` tests leave the machine and cost money; they are excluded by
default.

## Where to read

- [AGENTS.md](AGENTS.md) — the shape of the code, the rules, the commands
- [docs/product/README.md](docs/product/README.md) — the plan: one mind,
  many parts
- [docs/laws.md](docs/laws.md) — the laws it works under
