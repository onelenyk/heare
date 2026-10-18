#!/bin/sh
# heare — one line: install the brain and the parts you choose, write their
# manifests, and keep them running with launchd or systemd.
#
#   ./install.sh --role mac --parts eyes,voice,hands
#   ./install.sh --role server
#
# --from DIR|URL   where the wheels are (default: ./dist)
# --version V      the version the URL holds
# --deepseek-key K, --groq-key K   store a key in the Keychain
# --no-service     install only; do not start anything
set -eu

ROLE=mac
PARTS="eyes voice hands"
FROM="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/dist"
VERSION=""
DEEPSEEK_KEY=""
GROQ_KEY=""
SERVICE=1

usage() {
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --role) ROLE=$2; shift 2 ;;
        --parts) PARTS=$(printf '%s' "$2" | tr ',' ' '); shift 2 ;;
        --from) FROM=$2; shift 2 ;;
        --version) VERSION=$2; shift 2 ;;
        --deepseek-key) DEEPSEEK_KEY=$2; shift 2 ;;
        --groq-key) GROQ_KEY=$2; shift 2 ;;
        --no-service) SERVICE=0; shift ;;
        -h|--help) usage 0 ;;
        *) echo "невідомий прапорець: $1" >&2; usage 1 ;;
    esac
done

say() { printf '  %s\n' "$*"; }
die() { printf 'спинився: %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
    Darwin) OS=mac ;;
    Linux) OS=linux ;;
    *) die "лише macOS і Linux" ;;
esac

[ "$ROLE" = "connect" ] && die "частини до віддаленого мозку ще не вміють (Ф6)"

echo "heare: роль $ROLE, система $OS"

command -v uv >/dev/null 2>&1 || {
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
    for name in core reach eyes voice hands chat brain desktop; do
        curl -fsSL "$FROM/heare_${name}-${VERSION}-py3-none-any.whl" \
            -o "$WHEELS/heare_${name}-${VERSION}-py3-none-any.whl" \
            || die "не завантажив heare-${name}"
    done
fi
say "колеса на місці"

CORE=$(ls "$WHEELS"/heare_core-*.whl)
REACH=$(ls "$WHEELS"/heare_reach-*.whl)

tool_install() {
    wheel=$1
    shift
    uv tool install --force "$wheel" "$@" >/dev/null || die "не встановив $(basename "$wheel")"
}

install_brain() {
    tool_install "$(ls "$WHEELS"/heare_brain-*.whl)" \
        --with "$CORE" --with "$REACH"
    say "мозок: heare-brain"
}

install_part() {
    case "$1" in
        eyes) tool_install "$(ls "$WHEELS"/heare_eyes-*.whl)" --with "$CORE" ;;
        voice) tool_install "$(ls "$WHEELS"/heare_voice-*.whl)" --with "$CORE" ;;
        chat) tool_install "$(ls "$WHEELS"/heare_chat-*.whl)" --with "$CORE" ;;
        hands) tool_install "$(ls "$WHEELS"/heare_hands-*.whl)" --with "$CORE" --with "$REACH" ;;
        *) die "не знаю частини «$1»" ;;
    esac
    say "частина: heare-$1"
}

BIN=$(uv tool dir --bin)
MANIFEST_DIR="$HOME/.heare/parts"

manifest() {
    name=$1 channel=$2 forms=$3 about=$4
    mkdir -p "$MANIFEST_DIR/$name"
    cat > "$MANIFEST_DIR/$name/heare.json" <<EOF
{"channel": "$channel", "forms": $forms, "about": "$about",
 "run": ["$BIN/heare-$name"]}
EOF
    say "маніфест: $name/heare.json"
}

if [ "$ROLE" = "mac" ] || [ "$ROLE" = "server" ]; then
    install_brain
fi

if [ "$ROLE" = "mac" ]; then
    for part in $PARTS; do
        install_part "$part"
    done
    tool_install "$(ls "$WHEELS"/heare_desktop-*.whl)" --with "$CORE"
    say "маяк: heare-desktop"
    for part in $PARTS; do
        case "$part" in
            eyes) manifest eyes eyes '["input"]' "очі: застосунок, простій, чи щось грає" ;;
            voice) manifest voice voice '["input", "output"]' "мікрофон і динамік" ;;
            hands) manifest hands opencode '["executor"]' "агент у терміналі: код, файли, команди, пошук в інтернеті" ;;
            chat) say "чат — окремо, він потребує термінала" ;;
        esac
    done
fi

if [ -n "$DEEPSEEK_KEY" ] || [ -n "$GROQ_KEY" ]; then
    KEYCODE='import sys
from heare.keys import store
if sys.argv[1]: store("DEEPSEEK_API_KEY", sys.argv[1])
if sys.argv[2]: store("GROQ_API_KEY", sys.argv[2])'
    uv run --no-project --with "$CORE" python -c "$KEYCODE" "$DEEPSEEK_KEY" "$GROQ_KEY"
    say "ключі в Keychain"
fi

command -v ffmpeg >/dev/null 2>&1 || say "немає ffmpeg — голос візьме системний, або постав його"
case "$OS" in
    mac) command -v opencode >/dev/null 2>&1 || say "немає opencode — руки не матимуть агента" ;;
esac

if [ "$SERVICE" = "0" ]; then
    echo "готово (нічого не запускав)"
    exit 0
fi

LOGS="$HOME/.heare/logs"
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
  <key>StandardOutPath</key><string>$LOGS/$log</string>
  <key>StandardErrorPath</key><string>$LOGS/$log</string>
</dict></plist>
EOF
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
        say "launchd: $label"
    }
    plist com.heare.brain heare-brain brain.log
    [ "$ROLE" = "mac" ] && plist com.heare.desktop heare-desktop parts.log
    ;;
linux)
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
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now "$name.service"
        say "systemd: $name"
    }
    unit heare-brain heare-brain
    [ "$ROLE" = "mac" ] && unit heare-desktop heare-desktop
    ;;
esac

cat <<EOF

готово. Сторінка: http://127.0.0.1:9790/
Логи: $LOGS
Ключі: сторінка → ⚙
EOF
