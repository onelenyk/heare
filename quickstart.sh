#!/bin/bash
# Quick start script for Heare development

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "🎧 Heare Quick Start"
echo ""

# Check dependencies
echo "📦 Checking dependencies..."

if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv not found. Install from: https://docs.astral.sh/uv/"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Python 3 not found"
    exit 1
fi

echo "✅ Dependencies OK"

# Check environment
echo ""
echo "🔑 Checking environment..."

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo "📝 Creating .env from .env.example..."
        cp .env.example .env
        echo "⚠️  Please edit .env and set GROQ_API_KEY"
        echo "   Then run: ./quickstart.sh"
        exit 1
    else
        echo "❌ No .env or .env.example found"
        exit 1
    fi
fi

if ! grep -q "GROQ_API_KEY=sk-" .env 2>/dev/null; then
    echo "⚠️  GROQ_API_KEY not set in .env"
    echo "   Get your key from: https://console.groq.com/keys"
fi

# Install dependencies
echo ""
echo "📦 Installing dependencies..."
uv sync

# Create necessary directories
echo ""
echo "📁 Creating directories..."
mkdir -p ~/.heare/logs
mkdir -p ~/.heare/workspace

# Check for portaudio (needed for audio)
if ! brew list portaudio 2>/dev/null | grep -q portaudio; then
    echo ""
    echo "🎵 Installing portaudio (for audio support)..."
    brew install portaudio
    uv sync --extra local
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start Heare:"
echo "  ./hearectl start      # Start daemon"
echo "  ./hearectl status     # Check status"
echo "  ./hearectl logs      # View logs"
echo ""
echo "To install as system service:"
echo "  ./hearectl install"
