.PHONY: help install start stop restart status logs watch test clean mcp-list mcp-enable mcp-disable mcp-status mcp-edit-catalog test-recognizer

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
	@echo "  make watch      - Start watch dashboard"
	@echo ""
	@echo "MCP Servers:"
	@echo "  make mcp-list           - List all available MCP servers"
	@echo "  make mcp-status         - Show enabled MCP servers"
	@echo "  make mcp-enable NAME    - Enable an MCP server (e.g., make mcp-enable NAME=github)"
	@echo "  make mcp-disable NAME   - Disable an MCP server"
	@echo "  make mcp-edit-catalog   - Open custom catalog in editor"
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

watch:
	@./hearectl watch

test:
	@echo "Running tests..."
	uv run pytest -q

test-verbose:
	@echo "Running tests (verbose)..."
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
