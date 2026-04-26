# Action Execution Flow

How heare executes actions, from intent to result.

## 1. High-Level Flow

```
User speaks: "відкрий Chrome і зайди на Журу"
        │
        ▼
Generator emits: <intent>{"tool":"workflow","args":"run jira-check"}</intent>
        │
        ▼
IntentParser extracts intent
        │
        ▼
IntentQueue.submit(intent)
        │
        ▼
ActionWorker (async background task)
        │
        ▼
Route: workflow? direct? claude?
        │
        └─→ Execute steps
            │
            ▼
Speak summary to user
```

---

## 2. Intent Submission

```python
# In GeneratorProcessor (streaming LLM response)
intent = {
    "tool": "bash",
    "args": "open -a 'Google Chrome'"
}

# Submit to queue
intent_id = await intent_queue.submit(
    payload=intent,
    decision_id=123,
    transcript_id=456,
)
```

**Validation in IntentQueue.submit():**
```python
# Checks before accepting:
- Tool in ALLOWED_TOOLS?
- Args length < MAX_ARGS_LEN (2000 chars)?
- Queue not full (max 32 pending)?
```

---

## 3. ActionWorker Routing

```
Intent picked from queue
        │
        ▼
Is it "workflow"?
        │
   ┌────┴────┐
  YES        NO
   │         │
   ▼         ▼
Workflow   Is it simple?
path       │
           ├─→ YES (bash, read, write, web_fetch, web_search)
           │        │
           │        ▼
           │    execute_direct()
           │        │
           │        └─→ Done in <1s, no Claude
           │
           └─→ NO (edit, MCP tools)
                   │
                   ▼
              claude_cli.call_action()
                   │
                   └─→ Claude reasons, executes tool
```

### Simple Tools (Direct Path)

```python
async def _execute_direct_path(intent):
    # No Claude involved, direct execution
    result = await execute_direct(
        intent.tool,  # "bash"
        intent.args,  # "echo hello"
        settings
    )
    # Returns: {"success": True, "output": "hello"}
```

**Direct execution:**
- `bash` → subprocess.run()
- `read` → Path.read_text()
- `write` → Path.write_text()
- `web_fetch` → httpx.get()
- `web_search` → Brave Search API

### Complex Tools (Claude Path)

```python
async def _execute_claude_path(intent):
    # Build prompt for Claude
    description = f"""Use the {tool} tool: {args}

After the tool completes, reply with ONE concise sentence in
Ukrainian (українською мовою) describing the outcome."""

    # Call Claude with tools enabled
    result = await claude_cli.call_action(description)
    # Returns: {"summary": "Файл створено успішно"}
```

---

## 4. Workflow Execution (Step-by-Step)

When you run a workflow with multiple steps:

```json
{
  "name": "jira-daily",
  "description": "Open Jira and check my tasks",
  "steps": [
    {"tool": "bash", "args": "open -a 'Google Chrome'"},
    {"tool": "mcp__chrome-devtools__navigate", "args": "https://jira.atlassian.net"},
    {"tool": "mcp__chrome-devtools__click", "args": "#my-issues"}
  ]
}
```

### Execution Flow

```
ActionWorker: workflow run jira-daily
        │
        ▼
Load workflow from ~/.heare/workflows/jira-daily.json
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  Step 1/3: bash "open -a 'Google Chrome'"           │
├───────────────────────────────────────────────────────┤
│  Route: simple → execute_direct()                    │
│  Result: {"success": true, "output": ""}            │
│  Time: ~0.5s                                         │
└───────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  Step 2/3: mcp__chrome-devtools__navigate            │
├───────────────────────────────────────────────────────┤
│  Route: complex (MCP) → claude_cli.call_action()     │
│  Prompt: "Use the mcp__chrome-devtools__navigate..." │
│  Claude: Calls MCP tool, navigates to URL            │
│  Result: {"summary": "Сторінку відкрито"}            │
│  Time: ~3-5s                                         │
└───────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  Step 3/3: mcp__chrome-devtools__click               │
├───────────────────────────────────────────────────────┤
│  Route: complex (MCP) → claude_cli.call_action()     │
│  Claude: Finds element, clicks                       │
│  Result: {"summary": "Клікнуто на мої завдання"}     │
│  Time: ~2-3s                                         │
└───────────────────────────────────────────────────────┘
        │
        ▼
Summary: "Виконано 3/3 кроків робочого потоку 'jira-daily'"
        │
        ▼
Speak to user
```

### Key Points About Workflow Execution

1. **Sequential** — Steps run one by one, not parallel
2. **Continue on failure** — If step 2 fails, step 3 still runs
3. **Mixed routing** — Simple tools use fast path, MCP uses Claude
4. **Timeout applies** — Entire workflow has `action_timeout_seconds` (default 120s)
5. **Results tracked** — Each step result logged, final summary spoken

---

## 5. Error Handling

### Direct Tool Error

```python
result = await execute_direct("bash", "invalid-command")
# Returns: {"success": False, "error": "command not found"}

ActionWorker:
    if not result["success"]:
        await on_error(intent, RuntimeError(result["error"]))
        → User hears: "Сталася помилка: command not found"
```

### Timeout

```python
# Action takes too long (> action_timeout_seconds)
asyncio.TimeoutError

ActionWorker:
    await on_error(intent, TimeoutError())
    → User hears: "Дія перевищила ліміт часу"
```

### Workflow Step Failure

```python
# Step 2 fails, Step 3 continues
results = [
    {"step": 1, "success": true},
    {"step": 2, "success": false, "error": "..."},
    {"step": 3, "success": true}
]

Final summary: "Виконано 2/3 кроків робочого потоку"
Each error logged separately
```

---

## 6. Callbacks: What Happens After Execution?

```python
# When action completes:
await on_result(intent, summary)
    │
    ▼
ConversationManager.record_action_result(intent_id, summary)
    │
    ▼
Update _action_log (in-memory)
    │
    ▼
Next generator prompt includes recent_actions
    │
    ▼
Heare can refer to completed actions:
    "Так, я вже відкрив Chrome"
```

---

## 7. Parallel Execution Note

**Important:** ActionWorker runs **sequentially** (one intent at a time).

```
Intent queue: [A, B, C]
                │
                ▼
ActionWorker processes A
                │
                ▼
ActionWorker processes B (only after A completes)
                │
                ▼
ActionWorker processes C (only after B completes)
```

This is intentional — prevents resource contention and makes behavior predictable.

If you submit multiple intents quickly (e.g., workflow with multiple steps):
- They queue up in IntentQueue
- Processed FIFO
- Max 32 pending, then drops

---

## 8. Example Timeline

```
T+0s    User: "запусти jira-check"
T+0.5s  Generator emits intent
T+0.6s  Intent queued
T+0.7s  ActionWorker picks intent
T+0.8s  Step 1: bash open Chrome (direct) → Done
T+1.5s  Step 2: MCP navigate (Claude) → Working...
T+4.0s  Step 2 complete
T+4.1s  Step 3: MCP click (Claude) → Working...
T+6.5s  Step 3 complete
T+6.6s  Summary: "Виконано 3/3 кроків"
T+6.7s  Heare speaks: "Виконав jira-check, відкрив Журу і мої завдання"
```

---

## 9. Current Limitations

1. **No parallel steps** — All sequential
2. **No conditional branching** — Can't do "if X then Y"
3. **No loops** — Can't repeat actions
4. **No variables** — Can't pass output of step 1 to step 2
5. **No rollback** — If step 3 fails, steps 1-2 not undone

These could be added later if needed.
