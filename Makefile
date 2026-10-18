.PHONY: help brain chat voice keeper test lint format check clean

help:
	@echo "heare — the app (heare/), the tests (tests/heare/)"
	@echo ""
	@echo "  make brain     - the brain, and the page on 127.0.0.1:9790"
	@echo "  make chat      - the conversation in a terminal"
	@echo "  make voice     - speech in, speech out"
	@echo "  make keeper    - the keeper: parts from ~/.heare/parts, kept alive"
	@echo "  make test      - the core against stand-ins: no network, no keys"
	@echo "  make lint      - ruff"
	@echo "  make format    - ruff format"
	@echo "  make check     - lint + test"
	@echo "  make clean     - caches and bytecode"

brain:
	uv run heare-brain

chat:
	uv run heare-chat

voice:
	uv run heare-voice

keeper:
	uv run heare-keeper

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
