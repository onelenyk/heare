## 2026-05-31T20:00:21Z - Wired build.py to system.py

- Changed import on line 47 from `src.agent.tools.schemas` to `src.agent.tools.system`
- Both `build_tools_schema()` and `register_all_tools()` have identical signatures in system.py
- Call sites (lines 670, 723-728) required zero changes
- Dynamic tool imports from schemas.py (lines 766, 775) untouched — those are separate concerns
- Verification: `uv run python -c "from src.pipeline.build import _assemble_native_stages"` passes
