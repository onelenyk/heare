.PHONY: help brain chat voice keeper test coverage typecheck lint format check quality eval-review eval-judge fact-e2e clean app live rollback releases release

help:
	@echo "heare — the app (heare/), the tests (tests/heare/)"
	@echo ""
	@echo "  make brain     - the brain, and the page on 127.0.0.1:9790"
	@echo "  make chat      - the conversation in a terminal"
	@echo "  make voice     - speech in, speech out"
	@echo "  make keeper    - the keeper: parts from ~/.heare/parts, kept alive"
	@echo "  make test      - the core against stand-ins: no network, no keys"
	@echo "  make coverage  - test, with the coverage floor at 80%"
	@echo "  make typecheck - mypy over heare-memory"
	@echo "  make lint      - ruff"
	@echo "  make format    - ruff format"
	@echo "  make check     - lint + test"
	@echo "  make quality   - check + coverage + typecheck"
	@echo "  make eval-review - live quality eval for the daily review (manual, paid)"
	@echo "  make eval-judge - live quality eval for the judge of actions (manual, paid)"
	@echo "  make fact-e2e  - live fact loop end to end (manual, paid)"
	@echo "  make clean     - caches and bytecode"
	@echo "  make app       - dist/heare.app and dist/heare.dmg: the menubar app, Python inside"
	@echo ""
	@echo "  make live      - check, then put this checkout live on this Mac and wait"
	@echo "  make rollback  - the release before the live one (TO=name for another)"
	@echo "  make releases  - the kept releases; ● is the live one"
	@echo "  make release V=x.y.z - one version everywhere, checked, committed, tagged here"

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

coverage:
	uv run pytest tests/heare -q --cov=heare --cov-report=term-missing --cov-fail-under=80

typecheck:
	uv run mypy

lint:
	uv run ruff check packages/ tests/

format:
	uv run ruff format packages/ tests/

check: lint test

quality: check coverage typecheck

# Not part of check: a real model, real keys, real cost. The quality gate
# for the daily review prompt.
eval-review:
	uv run python scripts/review_eval.py

eval-judge:
	uv run python scripts/judge_eval.py

# Not part of check: two real model calls, real cost, a temporary state root.
fact-e2e:
	uv run python scripts/fact_e2e.py

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache

# A stranger's Mac: no uv, no terminal, no launchd plists.
app:
	sh apps/mac/build.sh

# The daily update on this Mac: never a red tree, and always a way back.
live: check
	uv run --no-project python scripts/live.py live

rollback:
	uv run --no-project python scripts/live.py rollback $(TO)

releases:
	uv run --no-project python scripts/live.py releases

# A public release: committed and tagged here, pushed by hand.
release:
	uv run --no-project python scripts/release.py $(V)
