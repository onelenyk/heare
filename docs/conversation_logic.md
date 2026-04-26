# heare Conversation Logic

How heare tracks context, remembers conversations, and maintains state.

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Conversation State                       │
├─────────────────────────────────────────────────────────────┤
│  • conversation_id — Persistent session (30min timeout)    │
│  • summary — What we're discussing                         │
│  • active_topics — Topics mentioned in last 2 turns         │
│  • entities — People, places, things extracted              │
│  • recent_turns — Last 3 turns with topics                 │
│  • recent_actions — Last 5 actions (in-memory, bounded)     │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. Conversation Lifecycle

### Starting a Conversation

```
User speaks first time
    │
    ▼
ConversationManager.get_or_create_active()
    │
    ├─→ Is there an active conversation?
    │   ├─→ Yes, and < 30min old → Use it
    │   └─→ No or > 30min old → Create new
    │
    ▼
Store: INSERT INTO conversations (mode, start_ts)
    │
    ▼
Return conversation_id
```

### Conversation Timeout

```
Conversation idle for > 30 minutes
    │
    ▼
ConversationManager.get_or_create_active()
    │
    ▼
Store: UPDATE conversations SET end_ts = NOW()
    │
    ▼
Create new conversation
```

---

## 2. Turn Aggregation (What is a "turn"?)

A **turn** = user utterance + heare's response + any actions taken.

```
User: "погода в Києві?"
Heare: "У Києві зараз 12°C, ясно."
Actions: (none)
    │
    ▼
TurnAggregator combines:
    - Transcript text
    - Heare's response
    - Action results
    │
    ▼
Store: INSERT INTO turns (conversation_id, aggregated_text, ...)
    │
    ▼
Extract topics from aggregated_text
    │
    ▼
Store: UPDATE turns SET topic_tags = [...]
```

---

## 3. Context Building

### What flows into each prompt?

**Decider Prompt** (Should heare respond?):
```python
{
    "time": "2026-04-23 14:30:00",
    "mode": "ambient",
    "recent_transcripts": "- [14:28] погода в Києві\n- [14:29] і як там?",
    "conversation_active": "yes",
    "conversation_summary": "Earlier: [1] User asked about... | Recent: ...",
    "active_topics": "weather, Kyiv",
    "entities": "  - mentioned: Kyiv",
    "recent_turns": "- [14:28] погода в Києві (topics: weather)",
    "silence_block": "Silence since last utterance: 45s. Conversation active: yes.",
    "proactivity_block": "PROACTIVITY OVERRIDE: high — be very engaged...",
}
```

**Generator Prompt** (What to say?):
```python
{
    "time": "2026-04-23 14:30:00",
    "timezone": "EET",
    "persona": "...",  # Heare's identity
    "transcript": "і як там?",
    "conversation_summary": "Earlier: ... | Recent: ...",
    "active_topics": "weather, Kyiv",
    "entities": "...",
    "recent_turns": "...",
    "recent_actions": "- [14:28] ✓ search: weather in Kyiv\n- [14:29] ⋯ bash: ...",
    "mcp_servers": "...",  # Available tools
}
```

---

## 4. Topic Extraction

```
Turn aggregated: "User asked about weather in Kyiv. I responded with current temperature."
    │
    ▼
Claude.extract_topics(text)
    │
    ▼
Prompt: "Extract 3-5 topic phrases from the following text.
         Return ONLY a JSON array of short phrases (2-4 words each).
         Text: User asked about weather in Kyiv..."
    │
    ▼
Response: ["weather", "Kyiv forecast", "temperature"]
    │
    ▼
Store: UPDATE turns SET topic_tags = ["weather", "Kyiv forecast", "temperature"]
```

### Active Topics

- Topics from **last 2 turns** are considered "active"
- Flows into both decider and generator prompts
- Helps heare remember current discussion thread

---

## 5. Entity Extraction

Simple regex-based extraction (current implementation):

```python
# Find capitalized phrases
capitalized = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b", text)

# Example: "Погода в Києві та Львові"
# → ["Києві", "Львові"]  (if capitalized in Ukrainian context)
```

Stored in `entity_map`:
```json
{
  "topics": ["weather", "Kyiv forecast"],
  "mentioned": ["Kyiv", "Lviv"]
}
```

---

## 6. Action Log (In-Memory, Phase 2.2)

Actions are tracked in a bounded deque (max 16 entries):

```python
_action_log: deque = [
    {"id": 1, "tool": "bash", "args": "echo hello", "status": "done", "result": "...", "ts": ...},
    {"id": 2, "tool": "search", "args": "weather Kyiv", "status": "pending", "ts": ...},
]
```

### Lifecycle

```
Intent submitted
    │
    ▼
record_action_pending(intent_id, tool, args)
    │
    ▼
_action_log.append({"status": "pending", ...})
    │
    ├─→ Action succeeds → record_action_result(intent_id, summary)
    │                       └─→ Update entry to {"status": "done", "result": ...}
    │
    └─→ Action fails → record_action_error(intent_id, error)
                        └─→ Update entry to {"status": "error", "error": ...}
```

### Display in Generator Prompt

```
recent_actions:
  - [14:28] ✓ bash: додав хліб
  - [14:29] ⋯ search: пошук рейсів (pending)
  - [14:30] ✗ bash: помилка — command not found
```

---

## 7. Summary Maintenance

Strategy: Keep recent verbatim, summarize older.

```
Turns: [1] [2] [3] [4] [5] [6]
                 │
                 ▼
    ┌────────────┴────────────┐
    │                         │
 Older (1,2,3)         Recent (4,5,6)
    │                         │
    ▼                         ▼
Summarized              Verbatim
"[1] User asked..."    "User said X. I said Y."
"[2] Then discussed..."  "User said Z. I did action."
"[3) Mentioned..."       "User asked follow-up."
    │                         │
    └────────────┬────────────┘
                 ▼
    summary = "Earlier: [1]... | [2]... | [3]... |
               Recent: User said X. I said Y. User said Z. |
               Latest: User asked follow-up."
```

---

## 8. Context Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  User speaks: "і як там?"                                  │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Store Transcript                                       │
│     - transcripts table                                    │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Build Context (for Decider)                            │
│     ├── recent_transcripts (last 5 raw)                   │
│     ├── conversation_active (yes/no)                      │
│     ├── conversation_summary (what we're discussing)       │
│     ├── active_topics (last 2 turns)                      │
│     ├── entities (people, places)                          │
│     ├── recent_turns (last 3 with topics)                  │
│     └── recent_actions (last 5, in-memory)                │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Decider: Should respond?                               │
│     - Uses context to understand situation                 │
│     - Returns {"act": true, "speak": "Так, сонячно..."}    │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  4. Build Context (for Generator)                          │
│     ├── persona (Heare's identity)                         │
│     ├── transcript (current utterance)                     │
│     ├── conversation_summary                               │
│     ├── active_topics                                      │
│     ├── entities                                           │
│     ├── recent_turns                                       │
│     └── recent_actions                                     │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  5. Generator: Create response                             │
│     - Uses context to maintain conversation thread         │
│     - Returns: "Сонячно, 18 градусів. Щось ще?"           │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  6. Aggregate Turn                                         │
│     - User: "і як там?"                                    │
│     - Heare: "Сонячно, 18 градусів. Щось ще?"             │
│     - Actions: (none)                                      │
│     └─→ aggregated_text = "User asked how it is. I responded sunny, 18C."
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  7. Extract Topics                                         │
│     - Claude: ["weather", "temperature"]                  │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  8. Store Turn + Update Summary                            │
│     - INSERT INTO turns                                    │
│     - UPDATE conversations summary                         │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
              [Ready for next interaction]
```

---

## 9. Memory Retention

| Component | Location | Duration | Size Limit |
|-----------|----------|----------|------------|
| Transcripts | SQLite | Forever | Unlimited |
| Turns | SQLite | Forever | Unlimited |
| Conversations | SQLite | Forever | Unlimited |
| Action Log | In-memory (deque) | Runtime only | 16 entries |
| Recent Transcripts | SQLite | Read last 5 | 5 |
| Recent Turns | SQLite | Read last 3 | 3 |
| Active Topics | SQLite | Last 2 turns | ~10 topics |

---

## 10. Key Files

| File | Responsibility |
|------|----------------|
| `src/conversation.py` | ConversationManager, topics, summaries |
| `src/context.py` | ContextBuilder, prompt rendering |
| `src/storage.py` | SQLite operations |
| `src/decider.py` | Decision logic using context |
| `src/generator.py` | Response generation using context |

---

## 11. Example: Full Conversation Thread

```
[14:28] User: "погода в Києві?"
        │
        ▼
ConversationManager: Create new conversation (id=42)
        │
        ▼
Decider: act=true (direct question)
        │
        ▼
Generator: "У Києві зараз 12°C."
        │
        ▼
Action: web_search weather Kyiv → result
        │
        ▼
Turn: "User asked about Kyiv weather. I said 12°C."
Topics: ["weather", "Kyiv"]
        │
        ▼
Update summary: "User asked about weather in Kyiv. I responded with current temperature."

[14:29] User: "а завтра?"
        │
        ▼
Context includes:
- conversation_summary: "User asked about weather in Kyiv..."
- active_topics: ["weather", "Kyiv"]
- recent_turns: ["User asked about Kyiv weather..."]
        │
        ▼
Decider: Understands continuation (topic=weather)
        │
        ▼
Generator: "Завтра прогнозують 15°C, невеликий дощ."
        │
        ▼
Turn: "User asked about tomorrow. I forecasted 15C, light rain."
Topics: ["weather forecast", "tomorrow"]
        │
        ▼
Update summary: "Earlier: User asked about weather in Kyiv. |
                  Recent: User asked about tomorrow. I forecasted..."
```

---

## 12. Conversation State Reset

Conversation resets naturally after 30 minutes of silence. Manual reset:

```bash
uv run python -m src.main reset-session  # Backs up session.json
```

This doesn't erase conversation history (SQLite), just starts a fresh conversation thread.
