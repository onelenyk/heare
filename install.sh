#!/bin/sh
# heare — install every tool this machine can run (brain, keeper, voice,
# hands, chat; eyes on a Mac), write the config for its role, and keep the
# brain and the keeper running with launchd or systemd. Which parts run is
# not an install: it is a switch on the keeper's page, 127.0.0.1:9791.
#
#   ./install.sh                     # a Mac: the brain and the parts here
#   ./install.sh --role server       # a server: the brain; parts if you want
#   ./install.sh --role body --url https://brain.example --code ABCD1234
#
# --role R         mac (default on macOS), server (default on Linux), body
# --parts a,b      turn these parts on now (voice, hands, eyes); or on the page
# --from DIR|URL   where the wheels are (default: ./dist)
# --version V      the version the URL holds
# --port P         the brain's port (default 9790)
# --url URL        where the brain is (a body)
# --code CODE      pair this body with the brain at --url
# --deepseek-key K, --groq-key K   keep a key (Keychain on a Mac, else ~/.heare/keys.json)
# --no-service     install and configure; start nothing
#
# Keys also come from HEARE_DEEPSEEK_KEY and HEARE_GROQ_KEY, so they never
# have to appear on a command line. Run again to update: the role this
# machine already has is kept unless --role says another.
set -eu

ROLE=""
PARTS=""
FROM="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/dist"
VERSION=""
PORT=""
URL=""
CODE=""
DEEPSEEK_KEY="${HEARE_DEEPSEEK_KEY:-}"
GROQ_KEY="${HEARE_GROQ_KEY:-}"
SERVICE=1

usage() {
    awk 'NR > 1 && /^set -eu/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --role) ROLE=$2; shift 2 ;;
        --parts) PARTS=$(printf '%s' "$2" | tr ',' ' '); shift 2 ;;
        --from) FROM=$2; shift 2 ;;
        --version) VERSION=$2; shift 2 ;;
        --port) PORT=$2; shift 2 ;;
        --url) URL=$2; shift 2 ;;
        --code) CODE=$2; shift 2 ;;
        --deepseek-key) DEEPSEEK_KEY=$2; shift 2 ;;
        --groq-key) GROQ_KEY=$2; shift 2 ;;
        # The old way to get only the form. Everything is installed now,
        # and the form is always there.
        --setup) shift ;;
        --no-service) SERVICE=0; shift ;;
        -h|--help) usage 0 ;;
        *) echo "невідомий прапорець: $1" >&2; usage 1 ;;
    esac
done

say() { printf '  %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die() { printf 'спинився: %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
    Darwin) OS=mac ;;
    Linux) OS=linux ;;
    *) die "лише macOS і Linux" ;;
esac

[ -z "$ROLE" ] && [ -n "$CODE" ] && ROLE=body
case "$ROLE" in
    ''|mac|server|body) ;;
    connect) ROLE=body ;;
    *) die "роль — mac, server або body" ;;
esac
if [ "$ROLE" = "body" ]; then
    [ -n "$URL" ] && [ -n "$CODE" ] || die "тілу потрібні --url і --code (код — на сторінці мозку: ⚙ → Пристрої)"
fi
[ -n "$CODE" ] && [ -z "$URL" ] && die "для --code потрібен --url"
if [ -n "$PORT" ]; then
    case "$PORT" in ''|*[!0-9]*) die "порт має бути числом" ;; esac
    if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
        die "порт поза 1..65535"
    fi
fi

# One Python for every tool, the one the image and the tests use. Left to
# itself uv takes the newest, and on Linux the voice's audio packages had
# no wheel for it yet: the build failed on a stranger's server.
PYTHON="${HEARE_PYTHON:-3.12}"

# Every tool this system can run; eyes read macOS alone.
TOOLS="brain keeper voice hands chat"
[ "$OS" = "mac" ] && TOOLS="$TOOLS eyes"

command -v uv >/dev/null 2>&1 || {
    command -v curl >/dev/null 2>&1 || die "потрібен curl, щоб поставити uv"
    echo "  ставлю uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
}

WHEELS=$(mktemp -d)
trap 'rm -rf "$WHEELS"' EXIT

if [ -d "$FROM" ]; then
    cp "$FROM"/heare_*.whl "$WHEELS"/ || die "у $FROM немає wheel — збери: uv build --all-packages -o dist"
else
    [ -n "$VERSION" ] || die "з URL потрібен --version"
    for name in core reach $TOOLS; do
        curl -fsSL "$FROM/heare_${name}-${VERSION}-py3-none-any.whl" \
            -o "$WHEELS/heare_${name}-${VERSION}-py3-none-any.whl" \
            || die "не завантажив heare-${name}"
    done
fi
say "колеса на місці"

wheel() {
    found=$(ls "$WHEELS"/heare_"$1"-*.whl 2>/dev/null | head -n 1)
    [ -n "$found" ] || die "немає колеса heare-$1"
    printf '%s' "$found"
}
CORE=$(wheel core)
REACH=$(wheel reach)

# A tool's own environment, with the wheels it needs beside it. The brain
# and the keeper must install; a part that will not is said and skipped —
# the page then shows it as not on this machine.
tool() {
    name=$1
    case "$name" in
        brain|hands) set -- --with "$CORE" --with "$REACH" ;;
        *) set -- --with "$CORE" ;;
    esac
    if uv tool install --force --python "$PYTHON" "$(wheel "$name")" "$@" \
            >/dev/null 2>"$WHEELS/$name.log"; then
        say "встановлено: heare-$name"
        return 0
    fi
    case "$name" in
        brain|keeper) cat "$WHEELS/$name.log" >&2; die "не встановив heare-$name" ;;
        *) warn "heare-$name не встав на цій машині — $(tail -n 1 "$WHEELS/$name.log")" ;;
    esac
}

echo "heare: система $OS"
for name in $TOOLS; do
    tool "$name"
done

BIN=$(uv tool dir --bin)
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) say "додай $BIN до PATH: uv tool update-shell" ;;
esac
# The keeper's own Python: it has the core, and heare.machine writes a
# manifest exactly as the page does.
PY="$(uv tool dir)/heare-keeper/bin/python"
[ -x "$PY" ] || die "не знайшов Python наглядача: $PY"

# A run with no --role keeps the role this machine already has: an update
# must not turn a body into a brain.
if [ -z "$ROLE" ]; then
    KEPT=$("$PY" -c 'from heare.config import Settings; print(Settings().role or "")')
    case "$KEPT" in
        all) ROLE=mac ;;
        brain) ROLE=server ;;
        body) ROLE=body ;;
        *) if [ "$OS" = "mac" ]; then ROLE=mac; else ROLE=server; fi ;;
    esac
    [ -n "$KEPT" ] && say "роль лишаю: $ROLE"
fi
echo "heare: роль $ROLE"

# The page's names for the same three answers.
case "$ROLE" in
    mac) PAGE_ROLE=all ;;
    server) PAGE_ROLE=brain ;;
    body) PAGE_ROLE=body ;;
esac

if [ "$ROLE" = "body" ] && [ -n "$CODE" ]; then
    PAIR_CODE='import sys
import urllib.error
from pathlib import Path

from heare.config import Settings
from heare.pairing import pair

url = sys.argv[1].rstrip("/")
try:
    answer = pair(url, sys.argv[2], "machine", sys.argv[3])
except urllib.error.HTTPError as error:
    sys.stderr.write(
        "код невірний або прострочений (HTTP %s) — візьми новий: heare-brain code\n"
        % error.code)
    raise SystemExit(1)
except urllib.error.URLError as error:
    sys.stderr.write("не знайшов адресу мозку: %s\n" % error.reason)
    raise SystemExit(1)

settings = Settings()
path = Path(settings.machine_token_file)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(answer["token"], encoding="utf-8")
path.chmod(0o600)
settings.set("url", url)'
    "$PY" -c "$PAIR_CODE" "$URL" "$CODE" "$(uname -n)" \
        || die "не підключився до мозку — перевір адресу й код"
    say "тіло підключене до $URL"
elif [ "$ROLE" != "body" ]; then
    # The brain is here: forget one elsewhere.
    "$PY" -c 'from heare.config import Settings; Settings().set("url", "")'
fi
"$PY" -c 'import sys; from heare.config import Settings; Settings().set("role", sys.argv[1])' "$PAGE_ROLE"

if [ -n "$PORT" ]; then
    "$PY" -c 'import sys; from heare.config import Settings; Settings().set("api_port", int(sys.argv[1]))' "$PORT"
    say "порт мозку: $PORT"
fi

if [ -n "$DEEPSEEK_KEY" ] || [ -n "$GROQ_KEY" ]; then
    # The DeepSeek key is the brain's: giving it is choosing the provider,
    # as on the page.
    KEYCODE='import sys
from heare import keys
from heare.config import Settings
if sys.argv[1]:
    keys.store("DEEPSEEK_API_KEY", sys.argv[1])
    Settings().set("llm_provider", "deepseek")
if sys.argv[2]:
    keys.store("GROQ_API_KEY", sys.argv[2])'
    "$PY" -c "$KEYCODE" "$DEEPSEEK_KEY" "$GROQ_KEY"
    if [ "$OS" = "mac" ]; then say "ключі в Keychain"; else say "ключі збережено"; fi
fi

if [ -n "$PARTS" ]; then
    # shellcheck disable=SC2086 — the names are words on purpose
    "$PY" -m heare.machine --bin "$BIN" on $PARTS || warn "не всі частини ввімкнулись — решту на сторінці"
fi

command -v ffmpeg >/dev/null 2>&1 || say "немає ffmpeg — голос візьме системний, або постав його"
command -v opencode >/dev/null 2>&1 || say "немає opencode — руки не матимуть агента"

LOGS="$HOME/.heare/logs"
BRAIN=0
[ "$ROLE" != "body" ] && BRAIN=1

page_hint() {
    if [ "$OS" = "linux" ]; then
        cat <<EOF

Налаштування — сторінка наглядача; вона слухає лише цю машину.
З іншого комп'ютера:
    ssh -L 9791:127.0.0.1:9791 $(id -un)@$(uname -n)
і відкрий http://127.0.0.1:9791
EOF
        if [ "$BRAIN" = "1" ]; then
            cat <<EOF
Сторінка мозку — так само:
    ssh -L ${PORT:-9790}:127.0.0.1:${PORT:-9790} $(id -un)@$(uname -n)
і відкрий http://127.0.0.1:${PORT:-9790}  (код входу: heare-brain code)
EOF
        fi
    else
        echo "Налаштування: http://127.0.0.1:9791/"
    fi
}

if [ "$SERVICE" = "0" ]; then
    echo
    echo "готово (нічого не запускав): heare-keeper — частини й сторінка налаштувань"
    [ "$BRAIN" = "1" ] && echo "                               heare-brain — мозок"
    page_hint
    exit 0
fi

mkdir -p "$LOGS"

case "$OS" in
mac)
    AGENTS="$HOME/Library/LaunchAgents"
    mkdir -p "$AGENTS"
    plist() {
        label=$1 program=$2 log=$3
        cat > "$AGENTS/$label.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$BIN/$program</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATH</string></dict>
  <key>StandardOutPath</key><string>$LOGS/$log</string>
  <key>StandardErrorPath</key><string>$LOGS/$log</string>
</dict></plist>
EOF
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
        sleep 1
        launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist" 2>/dev/null || {
            sleep 2
            launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
        }
        say "launchd: $label"
    }
    if [ "$BRAIN" = "1" ]; then
        plist com.heare.brain heare-brain brain.log
        launchctl bootout "gui/$(id -u)/com.heare.desktop" 2>/dev/null || true
    else
        # A body has its brain elsewhere: one left here from before must go.
        launchctl bootout "gui/$(id -u)/com.heare.brain" 2>/dev/null || true
        rm -f "$AGENTS/com.heare.brain.plist"
    fi
    plist com.heare.keeper heare-keeper parts.log
    ;;
linux)
    if ! systemctl --user show-environment >/dev/null 2>&1; then
        warn "немає systemd --user у цій сесії — запусти вручну: heare-keeper; heare-brain"
        page_hint
        exit 0
    fi
    UNITS="$HOME/.config/systemd/user"
    mkdir -p "$UNITS"
    unit() {
        name=$1 program=$2
        cat > "$UNITS/$name.service" <<EOF
[Unit]
Description=heare $name
After=network-online.target

[Service]
ExecStart=$BIN/$program
Environment=PATH=$PATH
Environment=HEARE_NO_BROWSER=1
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now "$name.service"
        systemctl --user restart "$name.service"
        say "systemd: $name"
    }
    if [ "$BRAIN" = "1" ]; then
        unit heare-brain heare-brain
    else
        systemctl --user disable --now heare-brain.service 2>/dev/null || true
    fi
    unit heare-keeper heare-keeper
    # A server is left by ssh: without lingering, the user's services stop
    # with the last session.
    if [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != "yes" ]; then
        loginctl enable-linger "$(id -un)" 2>/dev/null \
            || say "щоб служби жили без входу: sudo loginctl enable-linger $(id -un)"
    fi
    ;;
esac

echo
echo "готово. Логи: $LOGS (на Linux: journalctl --user -u heare-keeper)"
[ "$OS" = "mac" ] && [ "$BRAIN" = "1" ] && echo "Сторінка: http://127.0.0.1:${PORT:-9790}/"
page_hint
