# heare

heare is a voice assistant you talk to out loud. It listens, decides when
it is being spoken to, answers in a voice, and can hand real work — files,
commands, searches — to a coding agent on your computer while the
conversation goes on. It runs on your own machine, with your own keys.

## Install

### Mac

1. Download the app from the latest release:
   <https://github.com/onelenyk/heare/releases/latest> —
   `heare-arm64.dmg` for an Apple Silicon Mac (M1 and later),
   `heare-x86_64.dmg` for an Intel one.
2. Open it and drag heare to Applications.
3. Open heare. The app is not signed by Apple, so the first time macOS
   will refuse to open it. Either:
   - right-click (or Control-click) heare in Applications → **Open** →
     **Open**, or
   - open **System Settings → Privacy & Security**, scroll down to the
     message about heare and click **Open Anyway**.

   You only need to do this once.

### Linux server

The brain alone — no microphone needed — on a Linux machine you keep on:

```bash
curl -sSL https://raw.githubusercontent.com/onelenyk/heare/v0.1.0/install.sh | sh -s -- --role server --from https://github.com/onelenyk/heare/releases/download/v0.1.0 --version 0.1.0
```

It installs [uv](https://docs.astral.sh/uv/) if it is missing, puts every
tool this system can run in its own environment, and keeps the brain and the
keeper alive with systemd. Which parts run is a switch on the setup page,
http://127.0.0.1:9791 — on a server, reach it with
`ssh -L 9791:127.0.0.1:9791 user@server`. Other roles:
`--role body --url URL --code CODE` makes this machine parts only, paired
with a brain elsewhere; `--parts voice,hands` turns parts on at install;
`--no-service` starts nothing. Without a system keyring (a server) keys live
in `~/.heare/keys.json`, mode 0600; the environment wins.

The brain alone in Docker, state in a volume, keys from the environment:

```bash
DEEPSEEK_API_KEY=… docker compose -f docker/compose.yaml up -d --build
docker compose -f docker/compose.yaml exec brain heare-brain code   # pair a browser
```

More in [docker/](docker/).

## First run

The first time, heare opens its setup: on a Mac in the app's own window,
on a server in the browser (<http://127.0.0.1:9791/>). It asks three things:

- **Role** — this computer as the whole assistant, as the brain only
  (memory and conversation), or as a body that connects to a brain
  elsewhere with an address and a pairing code from that brain's page.
- **Parts** — what heare may use here: *voice* (microphone and speaker),
  *eyes* (which app is in front, whether something is playing),
  *hands* (a coding agent that does the work).
- **Keys** — heare needs two:
  - **DeepSeek**, for thinking: create a key at
    <https://platform.deepseek.com/api_keys>
  - **Groq**, for hearing (speech to text): create a key at
    <https://console.groq.com/keys>

  A key is written once and never shown again. Thinking needs DeepSeek;
  hearing needs Groq only where the voice part runs.

**Hands** need a coding agent already installed and signed in on this
computer: [opencode](https://opencode.ai) or
[Claude Code](https://docs.anthropic.com/en/docs/claude-code). Without one,
heare still talks; it just cannot do things for you.

On a Mac, heare lives in the menu bar: **Відкрити heare** opens the
assistant's page, **Частини** turns parts on and off, **Налаштування…** is the
same setup again. On a server the page is at <http://127.0.0.1:9790/>. Parts
from other machines appear there and wait until you approve them.

## Update

- **Mac:** download the new `.dmg` from
  [Releases](https://github.com/onelenyk/heare/releases/latest) and replace
  the app.
- **Linux:** run the install command again with the new version number.

Your conversations, keys and settings stay where they are. The page shows
which version is running beside the uptime, and marks any part that is on
a different version from the brain.

## Uninstall

**Mac:** choose **Вийти** in heare's menu, then move `/Applications/heare.app`
to the Trash. The app runs everything itself — there are no services to stop.

(If you installed heare the developer way, with `install.sh`, also run
`launchctl bootout gui/$(id -u)/com.heare.brain` and `…/com.heare.keeper`,
remove `~/Library/LaunchAgents/com.heare.*.plist`, and uninstall the
`heare-*` uv tools.)

**Linux**

```bash
systemctl --user disable --now heare-brain heare-keeper
rm ~/.config/systemd/user/heare-*.service
uv tool list | grep '^heare' | cut -d' ' -f1 | xargs -n1 uv tool uninstall
```

Everything heare remembers — conversations, settings, logs — is in
`~/.heare`. Delete that folder to forget it all. On a Mac, the keys are in
Keychain Access under the name `heare`; delete those entries too.

## Develop

From a checkout, with Python 3.11+ and uv (`brew install portaudio` for
the voice on a Mac):

```bash
uv sync
make brain        # the brain, and its page on 127.0.0.1:9790
make chat         # the conversation in a terminal
make voice        # speech in, speech out

make check        # lint + tests: no network, no keys
make live         # check, then put this checkout live on this Mac
make rollback     # back to the release before
make release V=x.y.z   # one version everywhere, committed and tagged locally
```

Pushing a `v*` tag makes CI build the wheels and publish the release.

- [AGENTS.md](AGENTS.md) — the shape of the code and its rules
- [docs/product/README.md](docs/product/README.md) — the plan
- [docs/laws.md](docs/laws.md) — the laws it works under

## License

heare is source-available under the
[PolyForm Shield License 1.0.0](LICENSE): you may read, run, change and
share it for any purpose except providing a product that competes with it.
