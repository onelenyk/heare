# heare Full Flow: Start to Finish

Complete journey of a voice interaction through heare's architecture.

## 1. Startup Flow

```
User runs: uv run python -m src.main start
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Load Configuration                                      │
│     - ~/.heare/config.toml (mode, voice, timeouts)         │
│     - Environment variables (GROQ_API_KEY, etc.)            │
│     - Settings object created                               │
└─────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Initialize Storage                                      │
│     - Open ~/.heare/heare.db (SQLite)                      │
│     - Run migrations if needed                              │
│     - Create tables: transcripts, decisions, actions        │
└─────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Load or Create Identity                                │
│     - Read ~/.heare/identity.json                          │
│     - If missing: call Claude to generate persona          │
│       (name, creature, vibe)                               │
└─────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  4. Load or Create Session                                 │
│     - Read ~/.heare/session.json (persistent Claude Code)  │
│     - If missing: start new session                        │
└─────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  5. Initialize Backends                                    │
│     - AgentSDKBackend (preferred) OR ClaudeCLIBackend      │
│     - Load MCP servers from ~/.heare/workspace/.mcp.json   │
│     - Create IntentQueue + ActionWorker                    │
└─────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  6. Build Pipeline                                         │
│     - Create transport (mic + speaker)                     │
│     - GroqSTTService (speech-to-text)                      │
│     - SmartTurnV3 (VAD + turn detection)                   │
│     - GeneratorProcessor (LLM streaming)                   │
│     - EdgeTTSService (text-to-speech)                      │
└─────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│  7. Start Background Tasks                                 │
│     - ActionWorker (async intent queue consumer)           │
│     - HeartbeatTask (periodic check-ins)                   │
│     - Watch task (config hot-reload)                       │
└─────────────────────────────────────────────────────────────┘
           │
           ▼
        [LISTENING]
```

---

## 2. Continuous Listening Loop (Idle State)

```
           ┌──────────────────────────────────────┐
           │  Mic → Silero VAD                    │
           │  (Voice Activity Detection)          │
           └──────────────────────────────────────┘
                        │
                        ▼ (silence)
              [Keep listening, no action]
                        │
                        ▼ (voice detected)
           ┌──────────────────────────────────────┐
           │  Accumulate audio until silence      │
           │  (SmartTurnV3 turn boundary)         │
           └──────────────────────────────────────┘
                        │
                        ▼
              Send to GroqSTTService
                        │
                        ▼
                    "Text transcript"
```

---

## 3. Decision Flow: Should heare respond?

```
     User speaks: "Heare, що там з погодою?"
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Store Transcript                                       │
│     - Write to SQLite (transcripts table)                  │
│     - Get transcript_id                                    │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Build Context                                          │
│     - Recent transcripts (last 5)                          │
│     - Conversation summary                                 │
│     - Active topics                                        │
│     - Recent actions                                       │
│     - Current mode (silent/focus/ambient)                  │
│     - Current time                                         │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Build Decider Prompt                                   │
│     - Persona (name, creature, vibe)                       │
│     - Context (from above)                                 │
│     - Current transcript                                   │
│     - Mode-specific instructions                           │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  4. Call Decider (Claude)                                  │
│     - AgentSDKBackend.call_decider(prompt)                 │
│     - Returns JSON: {"act": bool, "speak": str, ...}       │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  5. Store Decision                                         │
│     - Write to SQLite (decisions table)                    │
│     - Link to transcript_id                                │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
            Is act: true?
                  │
         ┌────────┴────────┐
         │                 │
        NO                YES
         │                 │
         ▼                 ▼
    [Don't speak]    [Check for intent]
    [Log only]       [Continue to generator]
```

---

## 4. Generator Flow: Creating the Response

```
     Decision: act=true, need to respond
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Build Generator Prompt                                 │
│     - Persona (identity)                                   │
│     - Context (conversation memory, recent turns)          │
│     - MCP servers list (available tools)                   │
│     - Current transcript                                   │
│     - Generator instructions (Ukrainian response rule)     │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Call Generator (OpenRouter/Claude)                     │
│     - AgentSDKBackend.call_decider(generator_prompt)       │
│     - STREAMING response (text chunks)                     │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Stream Processing                                      │
│     - Receive chunks from LLM                              │
│     - IntentStreamParser extracts <intent> tags            │
│     - Split stream: [text for TTS] [intents for queue]     │
└─────────────────────────────────────────────────────────────┘
                  │
         ┌────────┴────────┐
         │                 │
    [Text chunks]    [Intent tags]
         │                 │
         ▼                 ▼
┌──────────────────┐  ┌──────────────────────────────────┐
│  Buffer for TTS  │  │  IntentQueue.submit()            │
└──────────────────┘  │  - Validate tool in allowlist    │
                      │  - Check args length             │
                      │  - Assign intent ID              │
                      └──────────────────────────────────┘
                                    │
                                    ▼
                              [ActionWorker picks up]
                                    │
                                    ▼
                           (See Action Flow below)
```

---

## 5. Text-to-Speech Flow (Parallel to Actions)

```
     Text buffer from Generator
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Check TTS Cache                                        │
│     - Hash text + voice                                    │
│     - Return cached PCM if exists                          │
└─────────────────────────────────────────────────────────────┘
                  │ (cache miss)
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  2. EdgeTTSService.call()                                  │
│     - Microsoft Edge TTS API                               │
│     - Stream MP3 audio                                    │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Transcode MP3 → PCM                                    │
│     - 16kHz, 16-bit, mono                                 │
│     - Chunk into ~100ms frames                            │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  4. Cache PCM                                              │
│     - Store in ~/.heare/tts_cache/                        │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  5. Emit AudioFrames                                       │
│     - Send to transport.output()                          │
│     - Speaker plays audio                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. Action Flow (Async, Parallel to Speech)

```
     Intent in Queue: {"tool": "bash", "args": "ls -la"}
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  1. ActionWorker picks intent (async FIFO)                 │
│     - Pop from IntentQueue                                 │
│     - Get intent_id, tool, args                            │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Route: Simple vs Complex                               │
│     - Simple (bash, read, write, web_fetch, web_search)    │
│       → execute_direct()                                   │
│     - Complex (edit, MCP tools)                            │
│       → claude_cli.call_action()                           │
│     - Special (workflow)                                   │
│       → execute_workflow()                                 │
└─────────────────────────────────────────────────────────────┘
                        │
            ┌───────────┴───────────┐
            │                       │
        [Simple]              [Complex]
            │                       │
            ▼                       ▼
┌──────────────────────┐  ┌──────────────────────────────┐
│  execute_direct()    │  │  Build action prompt:        │
│  - No Claude call    │  │  "Use the Bash tool: ls -la  │
│  - Fast execution    │  │   Reply in Ukrainian..."     │
│  - Returns result    │  └──────────────────────────────┘
└──────────────────────┘                │
            │                            ▼
            │              ┌──────────────────────────────┐
            │              │  Claude CLI with tools       │
            │              │  - Can reason about args     │
            │              │  - Returns summary           │
            │              └──────────────────────────────┘
            │                            │
            └───────────┬────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Store Action Outcome                                   │
│     - Write to SQLite (actions table)                      │
│     - Link to decision_id, transcript_id                   │
│     - Store: success, output, error                        │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  4. Update Conversation Memory                             │
│     - Add action to recent actions context                 │
│     - Extract entities/topics from result                  │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  5. Call on_result (speak summary)                         │
│     - If action succeeded: speak Ukrainian summary         │
│     - If action failed: speak error message                │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. Confirmation Flow (For Risky Actions)

```
     Intent requires confirmation (e.g., "delete files")
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Decision: act=true, needs_confirmation=true            │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Generator: Ask permission (Ukrainian)                  │
│     - "Хочу видалити ці файли, можна?"                     │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
                   [Speak to user]
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Enter AWAITING_CONFIRMATION state                      │
│     - Suppress heartbeat ticks                             │
│     - Wait for user response                               │
│     - 30s timeout → auto-cancel                            │
└─────────────────────────────────────────────────────────────┘
                        │
         ┌───────────────┼───────────────┐
         │               │               │
      [Yes]           [No]         [Timeout]
         │               │               │
         ▼               ▼               ▼
    [Execute        [Cancel         [Cancel
     intent]        intent]        intent]
         │               │               │
         └───────────────┴───────────────┘
                        │
                        ▼
                   [Speak result]
```

---

## 8. Conversation Memory Update

```
     Action complete / Response spoken
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Aggregate Turn                                          │
│     - User transcript + heare response + actions           │
│     - Create Turn object                                   │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Update ConversationManager                             │
│     - Add turn to history                                  │
│     - Update summary (periodic)                            │
│     - Extract and track topics                             │
│     - Extract entities                                     │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Context for Next Turn                                   │
│     - Next prompt includes:                                │
│       - Recent transcripts                                 │
│       - Conversation summary                               │
│       - Active topics                                      │
│       - Recent actions                                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 9. Heartbeat Flow (Periodic, Ambient Mode Only)

```
     Every N minutes (configurable)
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Heartbeat Triggered                                     │
│     - Only in ambient mode                                 │
│     - Suppressed during confirmation                       │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Build Heartbeat Prompt                                  │
│     - "Is there something useful to say?"                  │
│     - Context: time, recent activity                       │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Decider: Should speak?                                 │
│     - If no: stay silent, don't interrupt                 │
│     - If yes: generate proactive message                   │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
            (Continue to Generator)
```

---

## 10. Shutdown Flow

```
     SIGTERM received (stop command)
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Stop Pipeline                                          │
│     - Cancel transport.input/output                        │
│     - Flush remaining audio                                │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Stop ActionWorker                                      │
│     - Cancel running actions                               │
│     - Drain intent queue (optional)                        │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Close Backends                                          │
│     - Agent SDK graceful shutdown                          │
│     - Close SQLite connection                              │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  4. Cleanup                                                │
│     - Remove PID file                                     │
│     - Final log entries                                   │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
              [Exit]
```

---

## 11. Full Timing Diagram (One Interaction)

```
User speaks     │  │  │  │  │  │  │  │  │
                ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼
VAD: detects voice ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                       │
STT: transcribes  ~~~~~│~~~~~~~~~~~~~│
                           │
Decider: decides  ~~~~~~~~│~~~~~~~~~~~~│
                               │
Generator: streams ~~~~~~~~~~~~~~~~~~~│ ← Intent emitted here
                     │            │
TTS: speaks      ~~~~~~~~~~~~~~~~~│
                                │
Action: runs   ~~~~~~~~~~~~~~~~~~~~~~~│
                                    │
Memory: update ~──────────────────────│
```

---

## 12. Parallel Processing

```
┌─────────────────────────────────────────────────────────────┐
│  Main Thread: Pipeline                                    │
│  - Mic input → STT → Decider → Generator → TTS → Speaker  │
└─────────────────────────────────────────────────────────────┘
                           │
                           │ (emits intent)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Async Task: ActionWorker                                 │
│  - Intent queue → Route → Execute → Store → Speak summary │
│  - Runs in parallel, never blocks speech                  │
└─────────────────────────────────────────────────────────────┘
                           │
                           │ (reads context)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Background Tasks:                                        │
│  - Heartbeat (periodic check-ins)                        │
│  - Watch (config hot-reload)                             │
│  - Conversation memory aggregation                       │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Interaction Points

1. **Transcript → Decision → Generator** is the core flow
2. **Actions run async** — speech never waits for tool completion
3. **Memory updates after each turn** — next prompt has full context
4. **Confirmation blocks heartbeat** — no interruptions during prompts
5. **All paths lead to SQLite** — everything is persisted for debugging
