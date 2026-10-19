.PHONY: help brain chat voice keeper test lint format check clean live rollback releases

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
	@echo ""
	@echo "  make live      - check, then put this checkout live on this Mac and wait"
	@echo "  make rollback  - the release before the live one (TO=name for another)"
	@echo "  make releases  - the kept releases; ● is the live one"

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

# The daily update on this Mac: never a red tree, and always a way back.
live: check
	uv run --no-project python scripts/live.py live

rollback:
	uv run --no-project python scripts/live.py rollback $(TO)

releases:
	uv run --no-project python scripts/live.py releases
