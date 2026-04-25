# Intent / Action Pipeline — Research Findings

**Date:** 2026-04-18
**Scope:** Make the `<intent>` → action path in heare's Phase 2.1+ generator pipeline reliable and predictable.
**Sources:** `src/generator.py`, `src/actions.py`, `src/intent_parser.py`, `src/agent_sdk_cli.py`, `src/claude_cli.py`, `src/claude_backend_common.py`, `src/main.py`, `prompts/generator.txt`, `~/.heare/logs/daemon.log` (21:21:48 live run).

---

## 1. Current flow

```
transcript (TranscriptionFrame)
  └─> GeneratorProcessor._handle_transcription
        ├─ context_builder.build_for_generator(...)
        ├─ openrouter_cli.generate(prompt)        [Gemini 3.1 Flash Lite, 5s timeout]
        ├─ per chunk:
        │     IntentStreamParser.feed(chunk)       → (speech, intents)
        │     split on sentence terminators
        │     push_frame(TTSSpeakFrame(scrubbed))  [speech → speaker, immediately]
        │     for each intent: IntentQueue.submit  [fire-and-forget]
        ├─ flush parser, push tail
        └─ background: conversation_manager.update_summary(...)

ActionWorker.run (separate task)
  └─> IntentQueue.next → _process_one(intent)
        ├─ description = f"Use the {tool} tool: {args}"
        ├─ claude_cli.call_action(description)    [asyncio.wait_for, 120s]
        │     └─ AgentSDKCLI._run_query
        │           ClaudeSDKClient.query(prompt)
        │           iterate AssistantMessage.TextBlock → stdout_chunks
        │           on timeout: close iterator, raise
        └─ on_result(intent, summary)  → record + logger.info
            on_error(intent, exc)      → record + logger.info
```

Key facts:

- `allowed_tools=["Bash"]`, `permission_mode="bypassPermissions"` in SDK options.
- A single `ClaudeSDKClient` instance services both `call_decider` (topic extraction, identity) and `call_action` (intent execution). Calls are serialized by the SDK.
- Intent shape is `{"tool": str, "args": str}`. No additional fields.
- IntentQueue is bounded (`intent_queue_max_pending=32`), FIFO, with `cancel_latest()` for "скасуй"/"відміни".
- The generator speaks the reply text *before* action completion. No post-action speech exists.

---

## 2. Live evidence (2026-04-18 21:21:48 daemon.log)

```
21:21:48 transcript="Відкрій будь ласка терминал"
21:21:48 [INTENT SUBMITTED id=1 tool=bash]
21:21:48 [TIMING] generator ttft=1928ms chunks=1 intents=1 cancelled=none
21:21:50 tts text="Добре, вже відкриваю для тебе термінал."
21:21:54 [ACTION RESULT id=1] summary=            ← EMPTY
21:22:49 WARNING SDK attempt 1 failed: claude-agent-sdk timed out after 60s
```

`claude-1776536574306.log`:
```
rc=0
--- prompt ---
Use the bash tool: ls        ← "open terminal" mapped to "ls"
--- stdout ---
                             ← no assistant text captured
```

Facts extracted:
1. LLM extracted intent with wrong args (`ls` for "open terminal").
2. Action completed with empty summary — SDK path captured no assistant text.
3. Immediate follow-up SDK call timed out at 60s, implying resource contention or lingering action state.

---

## 3. Defects, severity-ranked

### D1 — LLM emits incorrect args (**CRITICAL**)
- **File:** `prompts/generator.txt`
- **Symptom:** "Відкрій терминал" → `args="ls"`.
- **Cause:** Prompt shows exactly one example (`echo hello`). No schema, no allowed-command list, no "fallback to speak if unsure" rule. Gemini Flash Lite is under-spec'd for ambiguous natural-language commands.
- **Risk:** Unpredictable commands. A weak model may invent dangerous args.

### D2 — Empty `summary` from Agent SDK (**CRITICAL**)
- **File:** `src/agent_sdk_cli.py:219-262`
- **Symptom:** `summary=""` even though bash presumably ran.
- **Cause:** `_drain` collects only `AssistantMessage.TextBlock`. When Claude calls a tool and stops (no final text assistant message), no text is captured. `ToolResultBlock` / `ToolUseBlock` are ignored.
- **Risk:** System cannot confirm whether the action ran or what happened. All downstream UX (feedback, memory, error reporting) is broken.

### D3 — No post-action audio feedback (**HIGH**)
- **File:** `src/main.py:175-185`
- **Symptom:** TTS says "вже відкриваю" *before* action runs; user hears nothing after.
- **Cause:** `_on_action_result` only calls `logger.info` and `conversation_manager.record_action_result`. Nothing pushes a `TTSSpeakFrame`.
- **Risk:** User has no idea if action worked. Silent success = silent failure from the user's POV.

### D4 — Confirmation gate bypassed (**HIGH**)
- **File:** Generator pipeline has no equivalent of `DeciderProcessor`'s `AWAITING_CONFIRMATION` state.
- **Symptom:** Every intent executes immediately, no "можна?" / "так/ні" handshake.
- **Cause:** Phase 2.1 replaced the decider FSM with a fire-and-forget intent queue. README still promises confirmation, docs diverge from code.
- **Risk:** Destructive commands (`rm`, `git push --force`, network requests) run without verbal approval. Single source of failure per one bad LLM call.

### D5 — No intent validation / allowlist (**MEDIUM**)
- **File:** `src/actions.py:44-61`, dispatch at `src/actions.py:117`.
- **Symptom:** `IntentQueue.submit` accepts any non-empty `tool` string. Dispatch format is `f"Use the {tool} tool: {args}"` — if LLM emits `tool="SuperTool"`, SDK receives `Use the SuperTool tool: ...` with only `Bash` in `allowed_tools`, silently no-ops.
- **Missing:** tool allowlist (`{"bash"}` only), args length cap, shell meta-char review, deny-patterns for obvious destructive commands.
- **Risk:** Unknown intent shape → silent no-ops; malformed args → unpredictable SDK behavior.

### D6 — SDK serialization stall (**MEDIUM**)
- **File:** `src/agent_sdk_cli.py:76` (one `_client` for both call types).
- **Symptom:** `claude-agent-sdk timed out after 60s — retry in 2s` right after action.
- **Cause:** Single `ClaudeSDKClient`. When an action is in flight holding the session, decider-class calls (topic extraction) queue behind it. Under load, topic extraction hits its own 60s timeout.
- **Risk:** Cascading timeouts, memory update loss, wasted SDK retries.

### D7 — Pre-speech optimism (**MEDIUM**)
- **File:** `src/generator.py:263-288`.
- **Symptom:** Generator streams "вже зробив" while the intent is still being parsed/submitted.
- **Cause:** The LLM reply and the intent tag share one stream; the prompt encourages "confirm before acting" phrasing, so the user hears success before the action has even started.
- **Risk:** False-success feeling. Failures feel dishonest.

### D8 — `decider returned non-JSON` warnings spam logs (**LOW**)
- **File:** `src/claude_backend_common.py:78-91`.
- **Symptom:** Every topic-extraction call logs `decider payload is not a dict, treating as nothing`.
- **Cause:** Topic extraction returns `["phrase 1", "phrase 2"]` — a list, not a decider envelope. `parse_decider_response` is mis-applied.
- **Risk:** Log noise; masks real decider errors.

---

## 4. Why "predictable" is currently impossible

The system has **no observability of action outcomes** (D2) and **no user-facing signal that the action finished** (D3). Every other improvement — better prompts, confirmation gates, safer tools — is a guess until we can see what the action actually did.

Observability must come first.

---

## 5. Proposed work plan

### Phase A — Observable actions (fix D2 + D3)
Acceptance criteria:
1. `AgentSDKCLI._run_query` captures tool-use output (not only assistant text). A bash `echo hi` returns a non-empty summary containing `"hi"`.
2. `_on_action_result` pushes a `TTSSpeakFrame` with a short Ukrainian summary of the result (or silent if summary is empty *after* best-effort capture).
3. `_on_action_error` pushes a `TTSSpeakFrame` with a short Ukrainian error hint.
4. Integration test (`tests/integration/test_intent_flow.py`) asserts: when action returns `ran: echo hi`, a `TTSSpeakFrame` containing a summary phrase is pushed to the pipeline.
5. `make test` stays green.

Estimated size: ~2 files (`agent_sdk_cli.py`, `main.py`), 1 test update. Ralph-sized.

### Phase B — Prompt + validation hardening (fix D1 + D5)
Acceptance criteria:
1. `prompts/generator.txt` contains an explicit intent schema, allowed-tool list (`bash` only), 3+ Ukrainian examples, and a "якщо не впевнений — не додавай intent" rule.
2. `IntentQueue.submit` rejects tools not in `{"bash"}` and logs a warning; the frame flow is unaffected.
3. New unit tests cover both.
4. Run the scripted transcript "Відкрий термінал" through the fake-OpenRouter harness and confirm the model is either given a correct example or routed to speak-only.

### Phase C — Confirmation gate (fix D4)
Acceptance criteria:
1. Introduce a lightweight "destructive-command" classifier (regex on args) plus a confirmation state machine inside `ActionWorker` or `GeneratorProcessor`.
2. Destructive intents TTS a confirmation prompt and wait for "так"/"ні"/30s timeout.
3. Whitelisted safe commands (echo, ls, pwd, cat, date) execute without gate.
4. Tests for both paths.

### Phase D — SDK split (fix D6)
Acceptance criteria:
1. Two separate `ClaudeSDKClient` sessions: one for actions, one for decider/memory calls.
2. Action timeouts no longer block topic extraction; regression test reproduces old cascade and shows it is fixed.

D7 and D8 are small polish items that land inside Phases A/B naturally.

---

## 6. Open questions for the user

1. **Prompt source of truth:** does the assistant's confirm-first phrasing ("вже відкриваю") come from persona or generator.txt? Want the generator to say "зараз спробую" while running and only confirm success on result?
2. **Which actions warrant confirmation?** Default: everything except a whitelist. Or invert (confirm only destructive)?
3. **Model upgrade for generator?** Gemini Flash Lite's cheap but weak. Sonnet 4.6 as the generator would fix D1 largely without prompt gymnastics. Cost tradeoff.
4. **Separate SDK for actions vs decider?** Simpler: one client, serialize. Faster: two clients, parallel. Two clients need more identity/session management.

---

## 7. Next step

Ralph Phase A (D2 + D3) with a concrete PRD. Everything else waits until we can actually see what actions did.
