# Why 30 000 lines for simple features

Measured 2026-08-09.

```
src/*.py         30 602 lines
tests/*.py       22 929
frontend js/jsx   3 386
63 enabled tools
27 pipeline stages
```

## The volume is not the features

Take `volume` — "make it quieter", one number. Nine files know about it:

```
system.py:771     ToolSpec — name, description, handler
system.py:1057    serializer table
system.py:1125    name → function table
registry.py:597   category listing
direct.py:214     elif in the dispatcher
direct.py:3721    the implementation, 41 lines
config.py         field + TOML mapping + env mapping + range clamp  (4 places)
state.py          the state key
api.py            the /state endpoint
gain_control.py   the stage that reads the key      (19 mentions)
build.py          wiring                            (14 mentions)
context_injector.py  telling the model the current value
```

And the implementation itself (`direct.py:3743`):

```python
async with httpx.AsyncClient() as client:
    resp = await client.post(
        "http://127.0.0.1:9778/state",
        json={"key": "output_volume", "value": str(level)},
        timeout=5,
    )
```

A tool running *inside* the daemon opens an HTTP client and posts to
itself to change a variable in its own process.

The substance of the feature is `samples *= level`. One line. Around it,
~120 lines of ceremony: hand-rolled JSON parsing with its own try/except,
range validation, bilingual phrases inlined in two places, and a network
request to localhost.

## Where it adds up

Tool declaration alone:

```
system.py     1 467
schemas.py      927
registry.py     678
              ─────
              3 072 lines for 63 tools
```

Forty-nine lines of pure declaration per tool before it does anything.
Plus `config.py` — 878 lines to hold about forty numbers, because each
field is declared four times: dataclass field, TOML mapping, env mapping,
range clamp.

So the intuition "the features are simple, the codebase is huge" is
correct. What is expensive is not the feature. It is the procedure for
adding one.

## The measured cut

Two independent reductions.

### Cutting features

Three criteria decide, rather than taste:

1. **Anything that needs a screen and a logged-in desktop.** The target
   is a headless mini-server. Menubar, web frontend, canvas, sound cues,
   notifications.
2. **Anything that extends the agent instead of doing the work.** Skills
   marketplace, MCP servers, on-the-fly tool creation, capability index.
3. **Anything already broken or never verified.** Browser bridge (auth
   fails with close 4001), `workflow`, `batch_operation`, `cancel`.

Measured by AST, not estimated:

```
files deleted outright                        3 761
src/skills/                                   2 507
src/voice/indication/                         1 027
src/frontend/ (js/jsx/css)                    4 405
tools 63 → 11 (direct/system/schemas/registry) 4 325
api.py: 38 of 48 handlers                     1 171
                                            ───────
                                     ~12 800 Python + 4 400 JS
```

Remaining: ~17 800 of 30 600. Tests drop by roughly 9 400 of 22 900.
`nodejs_wheel` (213 MB, present only to build the frontend) falls away;
the other ~450 MB is pipecat's dependency closure and stays.

A convenient side effect: all four macOS-only files (`menubar.py`,
`browser_bridge.py`, `daemon/browser.py`, `indication/backends/
notification.py`) are already on that list. Cutting for simplicity
delivers Linux for free.

### Cutting ceremony

Estimated, not measured — the code does not exist yet:

```
tool layer      ~2 250 → ~250     a decorator instead of four tables
config.py          878 → ~150     one structure, one toml.load
state.py           147 → ~30      an object in the process, not REST
store + memory   3 008 → ~1 500   two stores merged into one
pipeline         5 271 → ~3 000   minus screen stages, gates become flags
```

Together: roughly 11 500 instead of 30 600.

## What was actually done instead

Neither. `src/core/` was **assembled alongside** rather than carved out —
1 601 lines that never import `modes`, `indication`, `skills`,
`mcp_bridge`, `subagent_manager`, `browser_bridge` or `dashboard_data`,
so none of them had to be untangled.

The reason is practical: `modes` and `indication` are woven into nine
modules each, and unpicking them was the longest and riskiest part of the
deletion plan. Building beside them costs nothing, keeps the old daemon
runnable for comparison, and makes `git rm` safe at the end instead of
risky at the start. What the new entry point never imports is dead by
demonstration rather than by judgement.

In `src/core/`, `volume` would be five lines in one file.
