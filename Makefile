.PHONY: help run serve voice listener desktop test lint format check clean

help:
	@echo "heare — the app (heare/), the tests (tests/heare/)"
	@echo ""
	@echo "  make run       - the app, by typing"
	@echo "  make serve     - the page and the API on 127.0.0.1:9790"
	@echo "  make voice     - the app, aloud"
	@echo "  make listener  - real speech in, the reply listened to (costs cents)"
	@echo "  make desktop   - the beacon: parts from ~/.heare/parts, kept alive"
	@echo "  make test      - the core against stand-ins: no network, no keys"
	@echo "  make lint      - ruff"
	@echo "  make format    - ruff format"
	@echo "  make check     - lint + test"
	@echo "  make clean     - caches and bytecode"

run:
	uv run python -m heare

serve:
	uv run python -m heare --serve

voice:
	uv run python -m heare --voice

listener:
	uv run python -m heare.listener

desktop:
	uv run python -m heare.desktop

test:
	uv run pytest tests/heare -q

lint:
	uv run ruff check packages/ tests/

format:
	uv run ruff format packages/ tests/

check: lint test

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
