#!/bin/sh
# dist/heare.app and dist/heare.dmg: the menubar shell, its own Python, and
# every heare part installed into that Python. A stranger runs it without
# uv, a terminal, or a launchd plist.
#
#     make app
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DIST="$ROOT/dist"
CACHE="$HERE/.cache"
APP="$DIST/heare.app"

# Pinned to one release and checked by hash: the app ships whatever this
# file is, so it must be the same file on every build.
PY_RELEASE=20250918
# Built for the machine it is built on: the wheels inside (numpy, the
# audio ones) are that machine's, so an Intel app is built on an Intel Mac.
case "$(uname -m)" in
    arm64) PY_ARCH=aarch64
           PY_SHA256=f7bd4b224b5257a2530a9f798612239d9f95043a8432d44c93761a01c58492e9 ;;
    x86_64) PY_ARCH=x86_64
            PY_SHA256=2fa58b2877effd507b481f69be6fe9f1a7a4bcb5b9dc8ab2e8acc02b927b8e19 ;;
    *) echo "не знаю архітектури $(uname -m)" >&2; exit 1 ;;
esac
PY_FILE="cpython-3.12.11+${PY_RELEASE}-${PY_ARCH}-apple-darwin-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${PY_FILE}"

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/packages/heare-core/pyproject.toml" | head -1)
[ -n "$VERSION" ] || { echo "no version in heare-core" >&2; exit 1; }

say() { printf '\n== %s\n' "$*"; }

say "python $PY_FILE"
mkdir -p "$CACHE"
if [ ! -f "$CACHE/$PY_FILE" ]; then
    curl -fL --retry 3 -o "$CACHE/$PY_FILE.part" "$PY_URL"
    mv "$CACHE/$PY_FILE.part" "$CACHE/$PY_FILE"
fi
echo "$PY_SHA256  $CACHE/$PY_FILE" | shasum -a 256 -c -

say "swift build"
swift build -c release --package-path "$HERE"
BIN=$(swift build -c release --package-path "$HERE" --show-bin-path)/heare

say "bundle $APP ($VERSION)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/heare"
sed "s/@VERSION@/$VERSION/g" "$HERE/Info.plist" > "$APP/Contents/Info.plist"
tar -xzf "$CACHE/$PY_FILE" -C "$APP/Contents/Resources"
PY="$APP/Contents/Resources/python/bin/python3"

say "wheels"
WHEELS="$CACHE/wheels"
rm -rf "$WHEELS"
(cd "$ROOT" && uv build --all-packages -o "$WHEELS" --wheel -q)
# Dependencies at the versions uv.lock holds, not whatever is newest today.
(cd "$ROOT" && uv export --all-packages --no-dev --no-emit-workspace --no-hashes \
    --frozen -q -o "$CACHE/requirements.txt")
uv pip install --python "$PY" --break-system-packages --no-cache \
    -r "$CACHE/requirements.txt" "$WHEELS"/*.whl

say "strip"
LIB="$APP/Contents/Resources/python/lib"
PYLIB="$LIB/python3.12"
# Scripts uv wrote carry this build's absolute path in their shebang; the
# app runs every part as `python -m heare.<name>` and needs none of them.
rm -f "$APP/Contents/Resources/python/bin/heare-"*
# Nothing in heare draws with Tk, and nobody runs the stdlib's own tests.
rm -rf "$PYLIB/test" "$PYLIB/idlelib" "$PYLIB/turtledemo" "$PYLIB/tkinter" \
    "$PYLIB/lib-dynload/_tkinter"*.so "$LIB"/libtcl* "$LIB"/libtk* \
    "$LIB"/tcl* "$LIB"/tk* "$LIB"/itcl* "$LIB"/thread* \
    "$APP/Contents/Resources/python/share" "$APP/Contents/Resources/python/include"
find "$PYLIB/site-packages" -type d -name tests -prune -exec rm -rf {} +
# Bytecode is written outside the bundle at run time (PYTHONPYCACHEPREFIX):
# an app may sit on a read-only volume, and a write inside would break its seal.
find "$APP" -type d -name __pycache__ -prune -exec rm -rf {} +

say "check"
"$PY" -I -B -c '
import importlib
for name in ("heare.keeper", "heare.brain", "heare.eyes", "heare.voice",
             "heare.hands", "heare.chat", "heare.machine"):
    importlib.import_module(name)
print("every part imports")'

say "sign (ad hoc)"
# Not a Developer ID: an ad hoc seal so macOS has one identity to hold the
# microphone permission for, the app and the Python it starts.
codesign --force --deep --sign - "$APP"

say "dmg"
STAGE="$CACHE/dmg"
rm -rf "$STAGE" "$DIST/heare.dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname heare -srcfolder "$STAGE" -format UDZO -ov "$DIST/heare.dmg"
rm -rf "$STAGE"

say "done"
du -sh "$APP" "$DIST/heare.dmg"
