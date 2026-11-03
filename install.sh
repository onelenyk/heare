#!/bin/sh
# heare — install the brain, the keeper and the parts on a Linux machine or
# a body, write the config for its role, and keep the brain and the keeper
# running with systemd. A Mac has one install path: the app (heare.app), or
# `make live` in a checkout. Which parts run is not an install: it is a
# switch on the keeper's page, 127.0.0.1:9791.
#
#   ./install.sh                     # a Linux machine: the brain and the parts here
#   ./install.sh --role brain        # a server: the brain; parts if you want
#   ./install.sh --role body --url https://brain.example --code ABCD1234
#   ./install.sh --lang uk           # speak Ukrainian
#
# --role R         all (default on Linux), brain (default on Linux), body;
#                  server is an alias for brain
# --parts a,b      turn these parts on now (voice, sources, hands,
#                  native-hands); or on the page
# --from DIR|URL   where the wheels are (default: ./dist)
# --version V      the version the URL holds
# --port P         the brain's port (default 9790)
# --url URL        where the brain is (a body)
# --code CODE      pair this body with the brain at --url
# --lang en|uk     what the installer says; en by default (or HEARE_LANG).
#                  A fresh config gets the same language; a run with no
#                  --lang keeps the language the machine already has.
# --keys-file PATH keys from a file (DEEPSEEK_API_KEY=…, GROQ_API_KEY=… lines)
# --no-service     install and configure; start nothing
#
# Keys come from HEARE_DEEPSEEK_KEY and HEARE_GROQ_KEY, or from --keys-file,
# and never from a command line. Run again to update: the role this machine
# already has is kept unless --role says another.
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
KEYS_FILE=""
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
                "heare — install every tool a Linux machine or a body can run, write" \
                "the config for its role, and keep the brain and the keeper running" \
                "with systemd. A Mac uses the app: heare.app, or make live in a checkout." \
                "Which parts run is not an install: it is a switch on the keeper's" \
                "page, 127.0.0.1:9791." \
                "" \
                "  ./install.sh                     a Linux machine: the brain and the parts here" \
                "  ./install.sh --role brain        a server: the brain; parts if you want" \
                "  ./install.sh --role body --url https://brain.example --code ABCD1234" \
                "" \
                "--role R         all (default on Linux), brain (default on Linux), body;" \
                "                 server is an alias for brain" \
                "--parts a,b      turn these parts on now (voice, sources, hands," \
                "                 native-hands); or on the page" \
                "--from DIR|URL   where the wheels are (default: ./dist)" \
                "--version V      the version the URL holds" \
                "--port P         the brain's port (default 9790)" \
                "--url URL        where the brain is (a body)" \
                "--code CODE      pair this body with the brain at --url" \
                "--lang en|uk     what the installer says; en by default (or HEARE_LANG)" \
                "--keys-file PATH keys from a file (DEEPSEEK_API_KEY=…, GROQ_API_KEY=… lines)" \
                "--no-service     install and configure; start nothing" \
                "" \
                "Keys come from HEARE_DEEPSEEK_KEY and HEARE_GROQ_KEY, or from --keys-file," \
                "and never from a command line. Run again to update: the role this" \
                "machine already has is kept unless --role says another." \
                ;;
        use_app) printf 'this is a Mac: heare is the app — install it from the dmg, or run make live in a checkout' ;;
        keys_file_missing) printf 'no key file: %s' "$2" ;;
        keys_failed) printf 'the keys did not save' ;;
        no_sums) printf 'no SHA256SUMS.txt beside the wheels in %s' "$2" ;;
        sum_missing) printf 'SHA256SUMS.txt has no sum for %s' "$2" ;;
        sum_wrong) printf 'the download of %s does not match SHA256SUMS.txt — not installing' "$2" ;;
        staging_failed) printf 'the tools were not swapped — the ones already here still run' ;;
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
        no_service_keeper) printf 'done (nothing started): heare-keeper — parts and the settings page' ;;
        no_service_brain) printf '                               heare-brain — the brain' ;;
        no_systemd) printf 'no systemd --user in this session — start by hand: heare-keeper; heare-brain' ;;
        systemd_line) printf 'systemd: %s' "$2" ;;
        linger_hint) printf 'so the services live without a login: sudo loginctl enable-linger %s' "$2" ;;
        done_logs) printf 'done. Logs: %s (on Linux: journalctl --user -u heare-keeper)' "$2" ;;
    esac
}

text_uk() {
    case "$1" in
        stopped) printf 'спинився: %s' "$2" ;;
        unknown_flag) printf 'невідомий прапорець: %s' "$2" ;;
        err_lang) printf 'мова — en або uk, не: %s' "$2" ;;
        usage)
            printf '%s\n' \
                "heare — поставити мозок і його частини на Linux чи тілі, записати" \
                "конфіг для ролі й тримати мозок і наглядача під systemd. На Mac" \
                "один шлях — застосунок heare.app або make live у робочій копії." \
                "Які частини працюють — не установка, а перемикач на сторінці" \
                "наглядача, 127.0.0.1:9791." \
                "" \
                "  ./install.sh                     Linux: мозок і частини тут" \
                "  ./install.sh --role brain        сервер: мозок; частини за бажанням" \
                "  ./install.sh --role body --url https://brain.example --code ABCD1234" \
                "" \
                "--role R         all (типово на Linux), brain (типово на Linux), body;" \
                "                 server — аліас brain" \
                "--parts a,b      увімкнути ці частини зараз (voice, sources, hands," \
                "                 native-hands); або на сторінці" \
                "--from DIR|URL   де колеса (типово: ./dist)" \
                "--version V      версія, яку тримає URL" \
                "--port P         порт мозку (типово 9790)" \
                "--url URL        де мозок (для тіла)" \
                "--code CODE      підключити це тіло до мозку за --url" \
                "--lang en|uk     мова інсталятора; типово en (або HEARE_LANG)" \
                "--keys-file PATH ключі з файлу (рядки DEEPSEEK_API_KEY=…, GROQ_API_KEY=…)" \
                "--no-service     поставити й налаштувати; нічого не запускати" \
                "" \
                "Ключі беруться з HEARE_DEEPSEEK_KEY і HEARE_GROQ_KEY або з --keys-file," \
                "і ніколи з командного рядка. Запусти ще раз, щоб оновити: роль, яку" \
                "машина вже має, лишається, якщо --role не каже інше." \
                ;;
        use_app) printf 'це Mac: heare — це застосунок — постав його з dmg або запусти make live у робочій копії' ;;
        keys_file_missing) printf 'немає файлу ключів: %s' "$2" ;;
        keys_failed) printf 'ключі не збереглися' ;;
        no_sums) printf 'немає SHA256SUMS.txt поруч із колесами в %s' "$2" ;;
        sum_missing) printf 'у SHA256SUMS.txt немає суми для %s' "$2" ;;
        sum_wrong) printf 'завантажене %s не збігається з SHA256SUMS.txt — не ставлю' "$2" ;;
        staging_failed) printf 'інструменти не замінені — ті, що вже є, далі працюють' ;;
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
        no_service_keeper) printf 'готово (нічого не запускав): heare-keeper — частини й сторінка налаштувань' ;;
        no_service_brain) printf '                               heare-brain — мозок' ;;
        no_systemd) printf 'немає systemd --user у цій сесії — запусти вручну: heare-keeper; heare-brain' ;;
        systemd_line) printf 'systemd: %s' "$2" ;;
        linger_hint) printf 'щоб служби жили без входу: sudo loginctl enable-linger %s' "$2" ;;
        done_logs) printf 'готово. Логи: %s (на Linux: journalctl --user -u heare-keeper)' "$2" ;;
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
        --keys-file) KEYS_FILE=$2; shift 2 ;;
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
    Darwin)
        # One supervisor on a Mac: the app holds its keeper and brain as
        # children. A second supervisor beside it would run every part twice.
        OS=mac
        die "$(msg use_app)"
        ;;
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

# Every tool a Linux machine or a body can run. Eyes read macOS alone and
# travel in the app, not here.
TOOLS="brain keeper voice hands sources native-hands chat"

command -v uv >/dev/null 2>&1 || {
    command -v curl >/dev/null 2>&1 || die "$(msg uv_needs_curl)"
    say installing_uv
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
}

WHEELS=$(mktemp -d)
STAGE=""
cleanup() {
    rm -rf "$WHEELS"
    if [ -n "$STAGE" ]; then
        rm -rf "$STAGE"
    fi
}
trap cleanup EXIT

# The wheel a part comes in. Part names are not package names: the CLI
# hands are heare-cli-hands, and hyphens become underscores in a wheel.
wheel_base() {
    case "$1" in
        hands) printf 'heare_cli_hands' ;;
        *) printf 'heare_%s' "$(printf '%s' "$1" | tr '-' '_')" ;;
    esac
}

sum_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# A wheel from a URL is only installed when the release's own sums name
# the file that was published. A local build is the person's own tree.
verify_wheel() {
    file=$1
    name=$(basename "$file")
    expected=$(awk -v n="$name" '{f = $2; sub(/^\*/, "", f); if (f == n) {print $1; exit}}' "$SUMS")
    [ -n "$expected" ] || die "$(msg sum_missing "$name")"
    [ "$(sum_of "$file")" = "$expected" ] || die "$(msg sum_wrong "$name")"
}

if [ -d "$FROM" ]; then
    cp "$FROM"/heare_*.whl "$WHEELS"/ || die "$(msg no_wheels "$FROM")"
else
    [ -n "$VERSION" ] || die "$(msg version_required)"
    SUMS="$WHEELS/SHA256SUMS.txt"
    curl -fsSL "$FROM/SHA256SUMS.txt" -o "$SUMS" \
        || die "$(msg no_sums "$FROM")"
    # memory is not a tool: the brain's wheel depends on it.
    for name in core reach memory $TOOLS; do
        base=$(wheel_base "$name")
        curl -fsSL "$FROM/$base-${VERSION}-py3-none-any.whl" \
            -o "$WHEELS/$base-${VERSION}-py3-none-any.whl" \
            || die "$(msg download_failed "$base")"
    done
    for file in "$WHEELS"/heare_*.whl; do
        verify_wheel "$file"
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

# Every tool is unpacked into a staging tree first. Only when they all fit
# does the tree move into place, each old environment kept in a sibling
# folder (uv would read `<name>.previous` as another tool): a failed install
# leaves the tools that already run alone.
REAL_TOOLS=$(uv tool dir)
REAL_BIN=$(uv tool dir --bin)
PREVIOUS="$(dirname "$REAL_TOOLS")/heare-tool-previous"
mkdir -p "$REAL_TOOLS" "$REAL_BIN" "$PREVIOUS"
STAGE=$(mktemp -d "$REAL_TOOLS/.heare-stage.XXXXXX")
STAGE_TOOLS="$STAGE/tools"
STAGE_BIN="$STAGE/bin"
mkdir -p "$STAGE_TOOLS" "$STAGE_BIN"

tool() {
    name=$1
    case "$name" in
        brain|hands) set -- --with "$CORE" --with "$REACH" ;;
        *) set -- --with "$CORE" ;;
    esac
    # The brain keeps its memory in heare-memory; the other tools do not.
    [ "$name" = "brain" ] && set -- "$@" --with "$MEMORY"
    if UV_TOOL_DIR="$STAGE_TOOLS" UV_TOOL_BIN_DIR="$STAGE_BIN" \
            uv tool install --force --python "$PYTHON" "$(wheel "$name")" "$@" \
            >/dev/null 2>"$WHEELS/$name.log"; then
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
INSTALLED_TOOLS=""
for name in $TOOLS; do
    if tool "$name"; then
        INSTALLED_TOOLS="$INSTALLED_TOOLS $name"
    fi
done

# The folder uv gives a tool is its package's name, not the part's: brain
# lives in heare-brain, hands in heare-cli-hands. 3 November: the swap moved
# folders named by part, found none, and a fresh install left no tools.
tool_dir() {
    case "$1" in
        hands) printf 'heare-cli-hands' ;;
        *) printf 'heare-%s' "$1" ;;
    esac
}

restore_tools() {
    for name in $INSTALLED_TOOLS; do
        dir=$(tool_dir "$name")
        if [ -d "$PREVIOUS/$dir" ]; then
            rm -rf "${REAL_TOOLS:?}/$dir"
            mv "$PREVIOUS/$dir" "$REAL_TOOLS/$dir" || true
        else
            rm -rf "${REAL_TOOLS:?}/$dir"
        fi
    done
}

swap_tools() {
    for name in $INSTALLED_TOOLS; do
        dir=$(tool_dir "$name")
        if [ -d "$REAL_TOOLS/$dir" ]; then
            rm -rf "${PREVIOUS:?}/$dir"
            mv "$REAL_TOOLS/$dir" "$PREVIOUS/$dir" || return 1
        fi
    done
    for name in $INSTALLED_TOOLS; do
        dir=$(tool_dir "$name")
        [ -d "$STAGE_TOOLS/$dir" ] || return 1
        mv "$STAGE_TOOLS/$dir" "$REAL_TOOLS/$dir" || return 1
        moved_shebangs "$REAL_TOOLS/$dir"
    done
}

# uv writes each script's Python as an absolute path, the staging one: after
# the move it named a folder the cleanup removes, and no command ran.
moved_shebangs() {
    for script in "$1"/bin/*; do
        [ -f "$script" ] && [ ! -L "$script" ] || continue
        first=$(head -n 1 "$script" 2>/dev/null || true)
        case "$first" in
            "#!$STAGE_TOOLS/"*)
                {
                    printf '#!%s/%s\n' "$REAL_TOOLS" "${first#"#!$STAGE_TOOLS/"}"
                    tail -n +2 "$script"
                } > "$script.heare-new" && chmod --reference="$script" "$script.heare-new" 2>/dev/null \
                    || chmod 755 "$script.heare-new"
                mv "$script.heare-new" "$script"
                ;;
        esac
    done
}

link_tools() {
    for shim in "$STAGE_BIN"/*; do
        [ -e "$shim" ] || [ -L "$shim" ] || continue
        command=$(basename "$shim")
        target=$(readlink "$shim" 2>/dev/null || true)
        case "$target" in
            "$STAGE_TOOLS"/*) target="$REAL_TOOLS/${target#"$STAGE_TOOLS/"}" ;;
        esac
        if [ -n "$target" ]; then
            ln -sf "$target" "$REAL_BIN/$command"
        else
            cp -p "$shim" "$REAL_BIN/$command"
        fi
    done
}

if ! swap_tools; then
    restore_tools
    die "$(msg staging_failed)"
fi
link_tools
for name in $INSTALLED_TOOLS; do
    say installed "$name"
done

BIN="$REAL_BIN"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) say path_hint "$BIN" ;;
esac
# The keeper's own Python: it has the core, and heare.machine writes a
# manifest exactly as the page does.
PY="$REAL_TOOLS/heare-keeper/bin/python"
[ -x "$PY" ] || die "$(msg keeper_python_missing "$PY")"

# A run with no --role keeps the role this machine already has: an update
# must not turn a body into a brain.
if [ -z "$ROLE" ]; then
    KEPT=$("$PY" -c 'from heare.config import Settings; print(Settings().role or "")')
    case "$KEPT" in
        all|mac) ROLE=all ;;
        brain|server) ROLE=brain ;;
        body) ROLE=body ;;
        *) ROLE=brain ;;
    esac
    [ -n "$KEPT" ] && say role_kept "$ROLE"
fi
printf '%s\n' "$(msg role_now "$ROLE")"

if [ "$ROLE" = "body" ] && [ -n "$CODE" ]; then
    PAIR_CODE='import os
import sys
import urllib.error
from pathlib import Path

from heare.config import Settings
from heare.pairing import pair

url = os.environ["HEARE_PAIR_URL"].rstrip("/")
try:
    answer = pair(url, os.environ["HEARE_PAIR_CODE"], "machine", sys.argv[1])
except urllib.error.HTTPError as error:
    sys.stderr.write((sys.argv[2] + "\n") % error.code)
    raise SystemExit(1)
except urllib.error.URLError as error:
    sys.stderr.write((sys.argv[3] + "\n") % error.reason)
    raise SystemExit(1)

settings = Settings()
path = Path(settings.machine_token_file)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(answer["token"], encoding="utf-8")
path.chmod(0o600)
settings.set("url", url)'
    HEARE_PAIR_URL="$URL" HEARE_PAIR_CODE="$CODE" \
        "$PY" -c "$PAIR_CODE" "$(uname -n)" \
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

if [ -n "$KEYS_FILE" ]; then
    [ -r "$KEYS_FILE" ] || die "$(msg keys_file_missing "$KEYS_FILE")"
    DEEPSEEK_KEY=$(sed -n 's/^[[:space:]]*DEEPSEEK_API_KEY[[:space:]]*=[[:space:]]*//p' \
        "$KEYS_FILE" | sed 's/\r$//' | tail -n 1)
    GROQ_KEY=$(sed -n 's/^[[:space:]]*GROQ_API_KEY[[:space:]]*=[[:space:]]*//p' \
        "$KEYS_FILE" | sed 's/\r$//' | tail -n 1)
fi

if [ -n "$DEEPSEEK_KEY" ] || [ -n "$GROQ_KEY" ]; then
    # The keys reach Python through a file only its owner reads, never argv.
    KEYFILE="$WHEELS/keys"
    (umask 077; printf '%s\n%s\n' "$DEEPSEEK_KEY" "$GROQ_KEY" > "$KEYFILE")
    # The DeepSeek key is the brain's: giving it is choosing the provider,
    # as on the page.
    KEYCODE='import sys
from pathlib import Path

from heare import keys
from heare.config import Settings

deepseek, groq = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()[:2]
Path(sys.argv[1]).unlink(missing_ok=True)
if deepseek:
    keys.store("DEEPSEEK_API_KEY", deepseek)
    Settings().set("llm_provider", "deepseek")
if groq:
    keys.store("GROQ_API_KEY", groq)'
    "$PY" -c "$KEYCODE" "$KEYFILE" || die "$(msg keys_failed)"
    say keys_stored
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
    msg page_linux "$(id -un)" "$(uname -n)"
    if [ "$BRAIN" = "1" ]; then
        msg brain_page_linux "${PORT:-9790}" "$(id -un)" "$(uname -n)"
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
# A service has no one at the screen to unlock a desktop keyring: asking
# it would hang the brain. The keys live in ~/.heare/machine/keys.json.
Environment=PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring
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
page_hint
