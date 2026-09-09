#!/usr/bin/env bash
set -euo pipefail

REPO="lenyk/heare"
INSTALL_DIR="$HOME/.heare"

echo "▸ Heare — Proactive ambient voice AI assistant"
echo "▸ Installing from $REPO"
echo ""

# ── macOS check ────────────────────────────────────────────────
if [ "$(uname)" != "Darwin" ]; then
  echo "✖ Heare currently only supports macOS."
  exit 1
fi

# ── Homebrew ────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
  echo "▸ Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

echo "▸ Installing system dependencies (portaudio, libomp)..."
brew install portaudio libomp 2>/dev/null || brew upgrade portaudio libomp 2>/dev/null

# ── uv ─────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  echo "▸ Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Source it for the current shell
  if [ -f "$HOME/.cargo/env" ]; then
    . "$HOME/.cargo/env"
  fi
  # Ensure uv is on PATH for the remaining commands
  export PATH="$HOME/.cargo/bin:$PATH"
fi

echo "▸ Installing heare via uv..."
uv tool install --python 3.11 "git+https://github.com/$REPO" --force

# ── Runtime directories ────────────────────────────────────────
mkdir -p "$INSTALL_DIR"

# ── Done ────────────────────────────────────────────────────────
echo ""
echo "✅ Heare installed!"
echo ""
echo "   Quick start:"
echo "     1. Set your API keys:"
echo "        cp .env.example .env"
echo "        vi .env"
echo ""
echo "     2. Run onboarding:"
echo "        heare setup"
echo ""
echo "     3. Start the daemon:"
echo "        heare menubar"
echo ""
echo "     Dashboard: http://127.0.0.1:9780"
echo ""
