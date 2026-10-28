#!/bin/sh
# heare — install every tool this machine can run (brain, keeper, voice,
# CLI hands, native hands, sources, chat; eyes on a Mac), write the config
# for its role, and keep the brain and the keeper running with launchd or
# systemd. Which parts run is not an install: it is a switch on the
# keeper's page, 127.0.0.1:9791.
#
#   ./install.sh                     # a Mac: the brain and the parts here
#   ./install.sh --role brain        # a server: the brain; parts if you want
#   ./install.sh --role body --url https://brain.example --code ABCD1234
#   ./install.sh --lang uk           # speak Ukrainian
#
# --role R         all (default on macOS), brain (default on Linux), body;
#                  mac is an alias for all, server for brain
# --parts a,b      turn these parts on now (voice, eyes, sources, hands,
#                  native-hands); or on the page
# --from DIR|URL   where the wheels are (default: ./dist)
# --version V      the version the URL holds
# --port P         the brain's port (default 9790)
# --url URL        where the brain is (a body)
# --code CODE      pair this body with the brain at --url
# --lang en|uk     what the installer says; en by default (or HEARE_LANG).
#                  A fresh config gets the same language; a run with no
#                  --lang keeps the language the machine already has.
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

# Every line a person reads comes from here, in both languages. A branch
# prints its line without a newline unless it is a block (usage, page
# hints); msg KEY [ARGS…] chooses the table, say/warn/die add the shape.
text_en() {
    case "$1" in
        stopped) printf 'stopped: %s' "$2" ;;
        unknown_flag) printf 'unknown flag: %s' "$2" ;;
        err_lang) printf 'language is en or uk, not: %s' "$2" ;;
        usage)
            printf '%s\n' \
                "heare — install every tool this machine can run, write the config" \
                "for its role, and keep the brain and the keeper running with launchd" \
                "or systemd. Which parts run is not an install: it is a switch on the" \
                "keeper's page, 127.0.0.1:9791." \
                "" \
                "  ./install.sh                     a Mac: the brain and the parts here" \
                "  ./install.sh --role brain        a server: the brain; parts if you want" \
                "  ./install.sh --role body --url https://brain.example --code ABCD1234" \
                "" \
                "--role R         all (default on macOS), brain (default on Linux), body;" \
                "                 mac is an alias for all, server for brain" \
                "--parts a,b      turn these parts on now (voice, eyes, sources, hands," \
                "                 native-hands); or on the page" \
                "--from DIR|URL   where the wheels are (default: ./dist)" \
                "--version V      the version the URL holds" \
                "--port P         the brain's port (default 9790)" \
                "--url URL        where the brain is (a body)" \
                "--code CODE      pair this body with the brain at --url" \
                "--lang en|uk     what the installer says; en by default (or HEARE_LANG)" \
                "--deepseek-key K, --groq-key K   keep a key (Keychain on a Mac, else ~/.heare/keys.json)" \
                "--no-service     install and configure; start nothing" \
                "" \
                "Keys also come from HEARE_DEEPSEEK_KEY and HEARE_GROQ_KEY, so they never" \
                "have to appear on a command line. Run again to update: the role this" \
                "machine already has is kept unless --role says another." \
                ;;
        os_only) printf 'only macOS and Linux' ;;
        role_wrong) printf 'role is all, brain or body (mac = all, server = brain)' ;;
        body_needs_pair) printf 'a body needs --url and --code (the code is on the brain page: gear → Devices)' ;;
        code_needs_url) printf '--code needs --url' ;;
        port_number) printf 'the port must be a number' ;;
        port_range) printf 'the port is outside 1..65535' ;;
        uv_needs_curl) printf 'curl is needed to install uv' ;;
        installing_uv) printf 'installing uv…' ;;
        no_wheels) printf 'no wheels in %s — build them: uv build --all-packages -o dist' "$2" ;;
        version_required) printf 'a URL needs --version' ;;
        download_failed) printf 'could not download %s' "$2" ;;
        wheels_ready) printf 'the wheels are here' ;;
        wheel_missing) printf 'no wheel for %s' "$2" ;;
        installed) printf 'installed: heare-%s' "$2" ;;
        install_failed) printf 'could not install heare-%s' "$2" ;;
        part_failed) printf 'heare-%s did not install on this machine — %s' "$2" "$3" ;;
        system) printf 'heare: system %s' "$2" ;;
        path_hint) printf 'add %s to PATH: uv tool update-shell' "$2" ;;
        keeper_python_missing) printf 'no Python for the keeper: %s' "$2" ;;
        role_kept) printf 'keeping the role: %s' "$2" ;;
        role_now) printf 'heare: role %s' "$2" ;;
        pair_http_error) printf 'the code is wrong or expired (HTTP %%s) — take a new one: heare-brain code' ;;
        pair_url_error) printf 'could not reach the brain: %%s' ;;
        pair_failed) printf 'did not pair with the brain — check the address and the code' ;;
        body_paired) printf 'the body is paired with %s' "$2" ;;
        port_set) printf 'the brain port: %s' "$2" ;;
        keys_keychain) printf 'the keys are in the Keychain' ;;
        keys_stored) printf 'the keys are saved' ;;
        parts_partial) printf 'not every part came on — the rest are on the page' ;;
        no_ffmpeg) printf 'no ffmpeg — voice will use the system one, or install it' ;;
        no_opencode) printf 'no opencode — the hands will have no agent' ;;
        page_linux)
            printf '\nThe settings page is the keeper’s; it listens on this machine only.\nFrom another computer:\n    ssh -L 9791:127.0.0.1:9791 %s@%s\nand open http://127.0.0.1:9791\n' "$2" "$3"
            ;;
        brain_page_linux)
            printf 'The brain page — the same way:\n    ssh -L %s:127.0.0.1:%s %s@%s\nand open http://127.0.0.1:%s  (sign-in code: heare-brain code)\n' "$2" "$2" "$3" "$4" "$2"
            ;;
        page_here) printf 'Settings: http://127.0.0.1:9791/' ;;
        no_service_keeper) printf 'done (nothing started): heare-keeper — parts and the settings page' ;;
        no_service_brain) printf '                               heare-brain — the brain' ;;
        launchd_line) printf 'launchd: %s' "$2" ;;
        no_systemd) printf 'no systemd --user in this session — start by hand: heare-keeper; heare-brain' ;;
        systemd_line) printf 'systemd: %s' "$2" ;;
        linger_hint) printf 'so the services live without a login: sudo loginctl enable-linger %s' "$2" ;;
        done_logs) printf 'done. Logs: %s (on Linux: journalctl --user -u heare-keeper)' "$2" ;;
        mac_page) printf 'Page: http://127.0.0.1:%s/' "$2" ;;
    esac
}

text_uk() {
    case "$1" in
        stopped) printf 'спинився: %s' "$2" ;;
        unknown_flag) printf 'невідомий прапорець: %s' "$2" ;;
        err_lang) printf 'мова — en або uk, не: %s' "$2" ;;
        usage)
            printf '%s\n' \
                "heare — поставити всі інструменти, які вміє ця машина, записати" \
                "конфіг для її ролі й тримати мозок і наглядача під launchd чи" \
                "systemd. Які частини працюють — не установка, а перемикач на" \
                "сторінці наглядача, 127.0.0.1:9791." \
                "" \
                "  ./install.sh                     Mac: мозок і частини тут" \
                "  ./install.sh --role brain        сервер: мозок; частини за бажанням" \
                "  ./install.sh --role body --url https://brain.example --code ABCD1234" \
                "" \
                "--role R         all (типово на macOS), brain (типово на Linux), body;" \
                "                 mac — аліас all, server — brain" \
                "--parts a,b      увімкнути ці частини зараз (voice, eyes, sources," \
                "                 hands, native-hands); або на сторінці" \
                "--from DIR|URL   де колеса (типово: ./dist)" \
                "--version V      версія, яку тримає URL" \
                "--port P         порт мозку (типово 9790)" \
                "--url URL        де мозок (для тіла)" \
                "--code CODE      підключити це тіло до мозку за --url" \
                "--lang en|uk     мова інсталятора; типово en (або HEARE_LANG)" \
                "--deepseek-key K, --groq-key K   зберегти ключ (Keychain на Mac, інакше ~/.heare/keys.json)" \
                "--no-service     поставити й налаштувати; нічого не запускати" \
                "" \
                "Ключі також беруться з HEARE_DEEPSEEK_KEY і HEARE_GROQ_KEY, тож їм" \
                "не треба з'являтися в командному рядку. Запусти ще раз, щоб оновити:" \
                "роль, яку машина вже має, лишається, якщо --role не каже інше." \
                ;;
        os_only) printf 'лише macOS і Linux' ;;
        role_wrong) printf 'роль — all, brain або body (mac = all, server = brain)' ;;
        body_needs_pair) printf 'тілу потрібні --url і --code (код — на сторінці мозку: ⚙ → Пристрої)' ;;
        code_needs_url) printf 'для --code потрібен --url' ;;
        port_number) printf 'порт має бути числом' ;;
        port_range) printf 'порт поза 1..65535' ;;
        uv_needs_curl) printf 'потрібен curl, щоб поставити uv' ;;
        installing_uv) printf 'ставлю uv…' ;;
        no_wheels) printf 'у %s немає wheel — збери: uv build --all-packages -o dist' "$2" ;;
        version_required) printf 'з URL потрібен --version' ;;
        download_failed) printf 'не завантажив %s' "$2" ;;
        wheels_ready) printf 'колеса на місці' ;;
        wheel_missing) printf 'немає колеса %s' "$2" ;;
        installed) printf 'встановлено: heare-%s' "$2" ;;
        install_failed) printf 'не встановив heare-%s' "$2" ;;
        part_failed) printf 'heare-%s не встав на цій машині — %s' "$2" "$3" ;;
        system) printf 'heare: система %s' "$2" ;;
        path_hint) printf 'додай %s до PATH: uv tool update-shell' "$2" ;;
        keeper_python_missing) printf 'не знайшов Python наглядача: %s' "$2" ;;
        role_kept) printf 'роль лишаю: %s' "$2" ;;
        role_now) printf 'heare: роль %s' "$2" ;;
        pair_http_error) printf 'код невірний або прострочений (HTTP %%s) — візьми новий: heare-brain code' ;;
        pair_url_error) printf 'не знайшов адресу мозку: %%s' ;;
        pair_failed) printf 'не підключився до мозку — перевір адресу й код' ;;
        body_paired) printf 'тіло підключене до %s' "$2" ;;
        port_set) printf 'порт мозку: %s' "$2" ;;
        keys_keychain) printf 'ключі в Keychain' ;;
        keys_stored) printf 'ключі збережено' ;;
        parts_partial) printf 'не всі частини ввімкнулись — решту на сторінці' ;;
        no_ffmpeg) printf 'немає ffmpeg — голос візьме системний, або постав його' ;;
        no_opencode) printf 'немає opencode — руки не матимуть агента' ;;
        page_linux)
            printf '\nНалаштування — сторінка наглядача; вона слухає лише цю машину.\nЗ іншого комп'"'"'ютера:\n    ssh -L 9791:127.0.0.1:9791 %s@%s\nі відкрий http://127.0.0.1:9791\n' "$2" "$3"
            ;;
        brain_page_linux)
            printf 'Сторінка мозку — так само:\n    ssh -L %s:127.0.0.1:%s %s@%s\nі відкрий http://127.0.0.1:%s  (код входу: heare-brain code)\n' "$2" "$2" "$3" "$4" "$2"
            ;;
        page_here) printf 'Налаштування: http://127.0.0.1:9791/' ;;
        no_service_keeper) printf 'готово (нічого не запускав): heare-keeper — частини й сторінка налаштувань' ;;
        no_service_brain) printf '                               heare-brain — мозок' ;;
        launchd_line) printf 'launchd: %s' "$2" ;;
        no_systemd) printf 'немає systemd --user у цій сесії — запусти вручну: heare-keeper; heare-brain' ;;
        systemd_line) printf 'systemd: %s' "$2" ;;
        linger_hint) printf 'щоб служби жили без входу: sudo loginctl enable-linger %s' "$2" ;;
        done_logs) printf 'готово. Логи: %s (на Linux: journalctl --user -u heare-keeper)' "$2" ;;
        mac_page) printf 'Сторінка: http://127.0.0.1:%s/' "$2" ;;
    esac
}

msg() {
    _msg_key=$1
    shift
    "text_$SPOKEN" "$_msg_key" "$@"
}

say() { printf '  %s\n' "$(msg "$@")"; }
warn() { printf '  ! %s\n' "$(msg "$@")" >&2; }
die() { printf '%s\n' "$(msg stopped "$*")" >&2; exit 1; }

# The language is chosen before anything is said: a bad value is reported
# in English, and a --lang later on still speaks for the whole run.
SPOKEN="${HEARE_LANG:-en}"
EXPLICIT_LANGUAGE=0
[ -n "${HEARE_LANG:-}" ] && EXPLICIT_LANGUAGE=1
_prev=
for _arg in "$@"; do
    case "$_prev" in
        --lang) SPOKEN=$_arg; EXPLICIT_LANGUAGE=1 ;;
    esac
    case "$_arg" in
        --lang=*) SPOKEN=${_arg#*=}; EXPLICIT_LANGUAGE=1 ;;
    esac
    _prev=$_arg
done
case "$SPOKEN" in
    en|uk) ;;
    *)
        _bad=$SPOKEN
        SPOKEN=en
        die "$(msg err_lang "$_bad")"
        ;;
esac

usage() {
    msg usage
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
        --lang) shift 2 ;;
        --lang=*) shift ;;
        # The old way to get only the form. Everything is installed now,
        # and the form is always there.
        --setup) shift ;;
        --no-service) SERVICE=0; shift ;;
        -h|--help) usage 0 ;;
        *) printf '  ! %s\n' "$(msg unknown_flag "$1")" >&2; usage 1 ;;
    esac
done

case "$(uname -s)" in
    Darwin) OS=mac ;;
    Linux) OS=linux ;;
    *) die "$(msg os_only)" ;;
esac

[ -z "$ROLE" ] && [ -n "$CODE" ] && ROLE=body
case "$ROLE" in
    ''|all|body|brain|mac|server) ;;
    connect) ROLE=body ;;
    *) die "$(msg role_wrong)" ;;
esac
case "$ROLE" in
    mac) ROLE=all ;;
    server) ROLE=brain ;;
esac
if [ "$ROLE" = "body" ]; then
    [ -n "$URL" ] && [ -n "$CODE" ] || die "$(msg body_needs_pair)"
fi
[ -n "$CODE" ] && [ -z "$URL" ] && die "$(msg code_needs_url)"
if [ -n "$PORT" ]; then
    case "$PORT" in ''|*[!0-9]*) die "$(msg port_number)" ;; esac
    if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
        die "$(msg port_range)"
    fi
fi

# One Python for every tool, the one the image and the tests use. Left to
# itself uv takes the newest, and on Linux the voice's audio packages had
# no wheel for it yet: the build failed on a stranger's server.
PYTHON="${HEARE_PYTHON:-3.12}"

# Every tool this system can run; eyes read macOS alone.
TOOLS="brain keeper voice hands sources native-hands chat"
[ "$OS" = "mac" ] && TOOLS="$TOOLS eyes"

command -v uv >/dev/null 2>&1 || {
    command -v curl >/dev/null 2>&1 || die "$(msg uv_needs_curl)"
    say installing_uv
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
}

WHEELS=$(mktemp -d)
trap 'rm -rf "$WHEELS"' EXIT

# The wheel a part comes in. Part names are not package names: the CLI
# hands are heare-cli-hands, and hyphens become underscores in a wheel.
wheel_base() {
    case "$1" in
        hands) printf 'heare_cli_hands' ;;
        *) printf 'heare_%s' "$(printf '%s' "$1" | tr '-' '_')" ;;
    esac
}

if [ -d "$FROM" ]; then
    cp "$FROM"/heare_*.whl "$WHEELS"/ || die "$(msg no_wheels "$FROM")"
else
    [ -n "$VERSION" ] || die "$(msg version_required)"
    # memory is not a tool: the brain's wheel depends on it.
    for name in core reach memory $TOOLS; do
        base=$(wheel_base "$name")
        curl -fsSL "$FROM/$base-${VERSION}-py3-none-any.whl" \
            -o "$WHEELS/$base-${VERSION}-py3-none-any.whl" \
            || die "$(msg download_failed "$base")"
    done
fi
say wheels_ready

wheel() {
    base=$(wheel_base "$1")
    found=$(ls "$WHEELS"/"$base"-*.whl 2>/dev/null | head -n 1)
    [ -n "$found" ] || die "$(msg wheel_missing "$base")"
    printf '%s' "$found"
}
CORE=$(wheel core)
REACH=$(wheel reach)
MEMORY=$(wheel memory)

# A tool's own environment, with the wheels it needs beside it. The brain
# and the keeper must install; a part that will not is said and skipped —
# the page then shows it as not on this machine.
tool() {
    name=$1
    case "$name" in
        brain|hands) set -- --with "$CORE" --with "$REACH" ;;
        *) set -- --with "$CORE" ;;
    esac
    # The brain keeps its memory in heare-memory; the other tools do not.
    [ "$name" = "brain" ] && set -- "$@" --with "$MEMORY"
    if uv tool install --force --python "$PYTHON" "$(wheel "$name")" "$@" \
            >/dev/null 2>"$WHEELS/$name.log"; then
        say installed "$name"
        return 0
    fi
    case "$name" in
        brain|keeper)
            cat "$WHEELS/$name.log" >&2
            die "$(msg install_failed "$name")"
            ;;
        *) warn part_failed "$name" "$(tail -n 1 "$WHEELS/$name.log")" ;;
    esac
}

printf '%s\n' "$(msg system "$OS")"
for name in $TOOLS; do
    tool "$name"
done

BIN=$(uv tool dir --bin)
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) say path_hint "$BIN" ;;
esac
# The keeper's own Python: it has the core, and heare.machine writes a
# manifest exactly as the page does.
PY="$(uv tool dir)/heare-keeper/bin/python"
[ -x "$PY" ] || die "$(msg keeper_python_missing "$PY")"

# A run with no --role keeps the role this machine already has: an update
# must not turn a body into a brain.
if [ -z "$ROLE" ]; then
    KEPT=$("$PY" -c 'from heare.config import Settings; print(Settings().role or "")')
    case "$KEPT" in
        all|mac) ROLE=all ;;
        brain|server) ROLE=brain ;;
        body) ROLE=body ;;
        *) if [ "$OS" = "mac" ]; then ROLE=all; else ROLE=brain; fi ;;
    esac
    [ -n "$KEPT" ] && say role_kept "$ROLE"
fi
printf '%s\n' "$(msg role_now "$ROLE")"

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
    sys.stderr.write((sys.argv[4] + "\n") % error.code)
    raise SystemExit(1)
except urllib.error.URLError as error:
    sys.stderr.write((sys.argv[5] + "\n") % error.reason)
    raise SystemExit(1)

settings = Settings()
path = Path(settings.machine_token_file)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(answer["token"], encoding="utf-8")
path.chmod(0o600)
settings.set("url", url)'
    "$PY" -c "$PAIR_CODE" "$URL" "$CODE" "$(uname -n)" \
        "$(msg pair_http_error)" "$(msg pair_url_error)" \
        || die "$(msg pair_failed)"
    say body_paired "$URL"
elif [ "$ROLE" != "body" ]; then
    # The brain is here: forget one elsewhere.
    "$PY" -c 'from heare.config import Settings; Settings().set("url", "")'
fi
"$PY" -c 'import sys; from heare.config import Settings; Settings().set("role", sys.argv[1])' "$ROLE"

# The language the person asked for, written where every process reads it.
# With no --lang and a config already here, its own choice is left alone.
CONFIG="$HOME/.heare/settings/config.toml"
if [ "$EXPLICIT_LANGUAGE" = "1" ] || [ ! -f "$CONFIG" ]; then
    "$PY" -c 'import sys; from heare.config import Settings; Settings().set("language", sys.argv[1])' "$SPOKEN"
fi

if [ -n "$PORT" ]; then
    "$PY" -c 'import sys; from heare.config import Settings; Settings().set("api_port", int(sys.argv[1]))' "$PORT"
    say port_set "$PORT"
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
    if [ "$OS" = "mac" ]; then say keys_keychain; else say keys_stored; fi
fi

if [ -n "$PARTS" ]; then
    # shellcheck disable=SC2086 — the names are words on purpose
    "$PY" -m heare.machine --bin "$BIN" on $PARTS || warn parts_partial
fi

command -v ffmpeg >/dev/null 2>&1 || say no_ffmpeg
command -v opencode >/dev/null 2>&1 || say no_opencode

LOGS="$HOME/.heare/logs"
BRAIN=0
[ "$ROLE" != "body" ] && BRAIN=1

page_hint() {
    if [ "$OS" = "linux" ]; then
        msg page_linux "$(id -un)" "$(uname -n)"
        if [ "$BRAIN" = "1" ]; then
            msg brain_page_linux "${PORT:-9790}" "$(id -un)" "$(uname -n)"
        fi
    else
        msg page_here
        printf '\n'
    fi
}

if [ "$SERVICE" = "0" ]; then
    printf '\n'
    msg no_service_keeper
    printf '\n'
    [ "$BRAIN" = "1" ] && { msg no_service_brain; printf '\n'; }
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
        say launchd_line "$label"
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
        warn no_systemd
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
        say systemd_line "$name"
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
            || say linger_hint "$(id -un)"
    fi
    ;;
esac

printf '\n'
printf '%s\n' "$(msg done_logs "$LOGS")"
[ "$OS" = "mac" ] && [ "$BRAIN" = "1" ] && printf '%s\n' "$(msg mac_page "${PORT:-9790}")"
page_hint
