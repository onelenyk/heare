# tests/

87 pytest files, no global conftest.py. All fixtures are defined inline per test file.

## STRUCTURE

```
tests/
├── test_<module>.py          # 84 unit test files
├── integration/              # 4 integration tests
├── spike/                    # Exploratory tests
└── fixtures/                 # Test data (e.g., cancel_stopwords.txt)
```

## RUN

```bash
uv run pytest -q              # Quick
uv run pytest -v              # Verbose
uv run pytest --cov=src       # Coverage
uv run pytest tests/test_storage.py -v              # Single file
uv run pytest tests/test_storage.py::test_function -v  # Single test
```

## FIXTURE PATTERNS

There is NO shared `conftest.py` or fixture module. All fixtures are per-file:

| Pattern | Example files | How |
|---------|---------------|-----|
| Temp DB | `test_storage.py`, `test_memory_backend.py` | `tempfile.TemporaryDirectory` + `Path` for SQLite |
| AsyncMock | `test_api.py`, `test_switchable_llm.py` | `MagicMock` with `AsyncMock` for async methods |
| Autouse | `test_capability_*.py`, `test_discovery.py` | `@pytest.fixture(autouse=True)` for state cleanup |

## CONFIG

All in `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--strict-markers --tb=short"
```

- `asyncio_mode = auto` — async test functions work without `@pytest.mark.asyncio`
- Coverage source = `src/`, omit = `tests/*, .venv/*, setup.py`
- No minimum coverage threshold (~63% current)

## GOTCHAS

- No conftest.py — don't create one unless you migrate ALL fixtures
- Integration tests (`tests/integration/`) excluded from `make test-cov`
- Mock patterns vary by file — check the test file's existing mocks before adding new ones
- Large test files: `test_direct_tools.py` (1162 lines, 63 tests), `test_agents_feature.py` (928 lines)
