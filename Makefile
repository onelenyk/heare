.PHONY: help install start stop restart status logs launch test clean mcp-list mcp-enable mcp-disable mcp-status mcp-edit-catalog test-recognizer reset-identity reset-session menubar build frontend dmg

help:
	@echo "Heare Voice AI Assistant - Control Commands"
	@echo ""
	@echo "Development:"
	@echo "  make quickstart  - Run initial setup"
	@echo "  make dev        - Start development server"
	@echo "  make test        - Run tests"
	@echo "  make test-recognizer - Interactive speaker recognition tester"
	@echo "  make clean       - Clean build artifacts"
	@echo ""
	@echo "Daemon Control:"
	@echo "  make start      - Start daemon in background"
	@echo "  make stop       - Stop daemon"
	@echo "  make restart    - Restart daemon"
	@echo "  make status     - Check daemon status"
	@echo "  make logs       - Tail daemon logs"
	@echo "  make launch     - Start daemon + open desktop in browser"
	@echo "  make menubar    - Launch macOS menu bar controller"
	@echo ""
	@echo "MCP Servers:"
	@echo "  make mcp-list           - List all available MCP servers"
	@echo "  make mcp-status         - Show enabled MCP servers"
	@echo "  make mcp-enable NAME    - Enable an MCP server (e.g., make mcp-enable NAME=github)"
	@echo "  make mcp-disable NAME   - Disable an MCP server"
	@echo "  make mcp-edit-catalog   - Open custom catalog in editor"
	@echo ""
	@echo "Build & Distribution:"
	@echo "  make build      - Build Heare.app with PyInstaller (needs frontend built first)"
	@echo "  make dmg        - Create Heare.dmg from Heare.app"
	@echo ""
	@echo "Infrastructure:"
	@echo "  make install    - Install as systemd service"
	@echo "  make uninstall  - Remove systemd service"

quickstart:
	@./quickstart.sh

dev:
	@echo "Starting development server..."
	uv run python -m src.main start

start:
	@./hearectl start

stop:
	@./hearectl stop

restart:
	@./hearectl restart

status:
	@./hearectl status

logs:
	@./hearectl logs

launch:
	@./hearectl launch

e2e:
	@echo "E2E: the whole daemon, a simulated room, real endpoints."
	@echo "Stop the daemon first — the scenarios need the database."
	uv run python -m src.pipeline.room all

test:
	uv run pytest -q

test-cov:
	uv run pytest --cov=src --cov-report=term --cov-report=html --ignore=tests/integration

test-full:
	uv run pytest -v

clean:
	@echo "Cleaning build artifacts..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	@echo "Clean complete"

install: heare.service
	@echo "Installing systemd service..."
	@./hearectl install

uninstall:
	@echo "Uninstalling systemd service..."
	@./hearectl uninstall

lint:
	@echo "Running linter..."
	uv run ruff check src/ tests/

lint-fix:
	@echo "Running linter with auto-fix..."
	uv run ruff check --fix src/ tests/

format:
	@echo "Formatting code..."
	uv run ruff format src/ tests/

check:
	@echo "Running all checks..."
	$(MAKE) lint
	$(MAKE) test
	@echo "✅ All checks passed"

# MCP Server Management
mcp-list:
	@uv run python -m src.main mcp list

mcp-status:
	@uv run python -m src.main mcp status

mcp-enable:
	@if [ -z "$(NAME)" ]; then \
		echo "Usage: make mcp-enable NAME=github"; \
		exit 1; \
	fi
	@uv run python -m src.main mcp enable $(NAME)

mcp-disable:
	@if [ -z "$(NAME)" ]; then \
		echo "Usage: make mcp-disable NAME=github"; \
		exit 1; \
	fi
	@uv run python -m src.main mcp disable $(NAME)

mcp-edit-catalog:
	@uv run python -m src.main mcp edit-catalog

# Speaker Recognition Testing
test-recognizer:
	@uv run python -m src.main test-recognizer

# Identity / Session Reset
reset-identity:
	@uv run python -m src.main reset-identity

reset-session:
	@uv run python -m src.main reset-session

menubar:
	@uv run python -m src.main menubar

build: frontend
	@echo "Building Heare.app with PyInstaller..."
	rm -rf dist/Heare build/HeareMenubar
	uv run pyinstaller HeareMenubar.spec --noconfirm
	@echo "✅ dist/Heare.app built"

frontend:
	@echo "Building frontend..."
	cd src/frontend && npm ci && npm run build
	@echo "✅ Frontend built"

dmg: build
	@echo "Creating Heare.dmg..."
	hdiutil create -volname Heare -srcfolder dist/Heare.app -ov -format UDZO Heare.dmg
	@echo "✅ Heare.dmg created"

# Acceptance battery for the spine engine — compresses "a week of living
# with it" into ~15 minutes + a soak. Requires API keys in ~/.heare/.env.
spine-acceptance:
	uv run pytest tests/ -q -k spine
	uv run pytest tests/test_spine_golden.py tests/test_spine_acoustic.py -m spine_live -q
	uv run python scripts/spine_soak.py --turns 100
