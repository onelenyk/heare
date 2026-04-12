# Heare — Scaffolding Work Plan (v2)

**Project:** `/Users/lenyk/myprojects/heare`
**Status:** Planned, awaiting Phase 0 validation
**Supersedes:** `PLAN.md` (custom asyncio direction) and v1 of this file (Pipecat + anthropic SDK direction)
**Updated:** 2026-04-11

---

## 1. Context & Changes from v1

This plan was revised through a deep interview. Key shifts:

| Decision | v1 | v2 |
|---|---|---|
| Brain | Anthropic SDK (`anthropic.messages.create`) | **`claude -p` subprocess** (full Claude Code with tools) |
| Capability | Voice conversation only | **Full agent** — can take actions (Read, Write, Bash, Edit) |
| Trust model | Confidence threshold, silent accept | **Verbal confirmation** before risky actions |
| Memory | Context-in-prompt, no persistence | **Persistent Claude Code session** (`--resume`) |
| Identity | Implied Lil Pear | **Separate persona, separate session** from claudeclaw |
| Proactivity | Pure reactive | **Voice + timed check-ins** (heartbeat) |
| Framework | Pipecat (tentative) | **Pipecat (locked in)** with stateful DeciderProcessor |
| TTS | edge-tts | **edge-tts wrapped as custom Pipecat TTSService** |

---

## 2. Vision (from interview)

Heare is a **proactive, ambient, agentic** voice AI assistant powered by Claude Code.

- **Listens continuously** to your microphone
- **Decides autonomously** whether each utterance warrants a response
- **Speaks Ukrainian** via free edge-tts voices
- **Can take actions** — it's not just a chatbot, it can Read/Write/Edit files, run Bash commands, use all Claude Code tools
- **Always verbally confirms** before doing anything risky ("I'd like to run pytest, okay?")
- **Remembers everything** across sessions via a persistent Claude Code session
- **Initiates on its own** via a heartbeat tick (every N minutes, decides whether to speak up)

**Not** a wake-word assistant. Not a dictation tool. Not a standalone LLM wrapper. A voice-first Claude Code agent that lives in your ears.

---

## 3. Architecture

### Pipeline (Pipecat, pure Option A)

```
┌─────────────────────────────────────────────────────────────────┐
│                     Pipecat Pipeline                             │
│                                                                   │
│  Mic ──► SileroVAD ──► GroqSTT ──► SmartTurnV3                   │
│  (LocalAudioTransport)                    │                       │
│                                            ▼                      │
│                              ┌─────────────────────────┐          │
│                              │   DeciderProcessor      │          │
│                              │   (stateful, custom)    │          │
│                              │                         │          │
│                              │  state machine:         │          │
│                              │  • LISTENING            │          │
│                              │  • AWAITING_CONFIRMATION│          │
│                              │  • EXECUTING            │          │
│                              │                         │          │
│                              │  shells out to          │          │
│                              │  `claude -p --resume`   │          │
│                              └─────────┬───────────────┘          │
│                                        │ TextFrame                │
│                                        ▼                          │
│                              ┌─────────────────────────┐          │
│                              │  EdgeTTSService         │          │
│                              │  (custom, wraps         │          │
│                              │  edge-tts library)      │          │
│                              └─────────┬───────────────┘          │
│                                        │ AudioFrame               │
│                                        ▼                          │
│                                     Speaker                       │
│                              (LocalAudioTransport)                │
└─────────────────────────────────────────────────────────────────┘

Parallel: Heartbeat task (asyncio) fires every N minutes,
feeds a synthetic "heartbeat" event to DeciderProcessor
which decides whether to initiate speech.
```

### The DeciderProcessor state machine

```python
class DeciderState(Enum):
    LISTENING = "listening"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"

class DeciderProcessor(FrameProcessor):
    state: DeciderState = LISTENING
    pending_action: Optional[dict] = None
    confirmation_deadline: Optional[float] = None  # time.monotonic() + 30

    async def process_frame(frame, direction):
        # only react to end-of-turn from Smart Turn
        if not is_user_speech_end(frame):
            await super().process_frame(frame, direction)
            return

        transcript = extract_transcript(frame)

        if self.state == LISTENING:
            decision = await run_claude_decider(transcript, mode, ctx)
            match decision.type:
                case "nothing":
                    return  # drop
                case "speak":
                    await self.push_frame(TextFrame(decision.reply))
                case "act":
                    self.pending_action = decision.action
                    self.state = AWAITING_CONFIRMATION
                    self.confirmation_deadline = monotonic() + 30
                    await self.push_frame(TextFrame(
                        f"I'd like to {decision.intent}, okay?"
                    ))

        elif self.state == AWAITING_CONFIRMATION:
            if monotonic() > self.confirmation_deadline:
                # timeout — auto-cancel
                self.state = LISTENING
                self.pending_action = None
                await self.push_frame(TextFrame("nevermind, cancelled"))
                return

            intent = await parse_yes_no(transcript)
            if intent == "yes":
                self.state = EXECUTING
                result = await run_claude_action(self.pending_action)
                self.state = LISTENING
                self.pending_action = None
                await self.push_frame(TextFrame(result.summary))
            elif intent == "no":
                self.state = LISTENING
                self.pending_action = None
                await self.push_frame(TextFrame("okay"))
            else:
                # unclear — re-prompt
                await self.push_frame(TextFrame("sorry, yes or no?"))

    async def on_heartbeat_tick(self):
        """Called by the heartbeat task every N minutes."""
        if self.state != LISTENING:
            return  # don't interrupt a confirmation flow
        decision = await run_claude_decider(
            transcript=None,  # no new input
            mode=self.mode,
            ctx=self.ctx,
            heartbeat=True,
        )
        if decision.type == "speak":
            await self.push_frame(TextFrame(decision.reply))
```

### Session model

Heare runs its OWN persistent Claude Code session, distinct from claudeclaw's:

- **Session file:** `~/.heare/session.json` (mirrors claudeclaw's pattern)
- **Bootstrap:** First run creates a fresh session; subsequent runs resume it
- **Persona:** Loaded via `--append-system-prompt` from `prompts/persona.txt` on every call
- **Working directory:** `claude -p` runs with `cwd = ~/.heare/workspace` by default (a dedicated scratchpad). This is where Claude Code's tools write files. User can override via config.
- **Security:** `--dangerously-skip-permissions` with `unrestricted` security level, BUT: actions are gated by the verbal confirmation flow, so agentic writes to real projects require the user saying "yes"
- **Compaction:** On context-limit errors, auto-compact by calling `claude --compact` (same as claudeclaw)

### Brain: two distinct `claude -p` calls

1. **Decider call** — fast, JSON-only, answers "should I do something?"
   - Prompt: `prompts/decider.txt` with injected context
   - Resumed session for memory
   - Expected output: strict JSON `{type, reply?, intent?, action?}`

2. **Action call** — slower, does the actual work
   - Only runs if verbal confirmation is "yes"
   - Prompt: the action description plus available tools
   - Resumed session (same as decider for shared memory)
   - Expected output: natural language summary of what was done

Both calls resume the same session, so memory is unified.

---

## 4a. Persona — auto-generated on bootstrap

Heare picks its own identity the first time it runs. No user input required. The pattern mirrors claudeclaw's `IDENTITY.md` / `SOUL.md` flow.

### How it works

1. **First run** (no `~/.heare/identity.json` exists):
   - `main.py` detects missing identity file
   - Calls `claude -p` with a special bootstrap prompt: `prompts/identity-bootstrap.txt`
   - Prompt asks Claude to generate: name, creature (what kind of entity), vibe, signature emoji, one-line tagline
   - Response is parsed as JSON, validated, and written to `~/.heare/identity.json`
2. **Subsequent runs**:
   - Identity loaded from `~/.heare/identity.json`
   - Persona prompt (`prompts/persona.txt`) is rendered as a template: `{name}`, `{creature}`, `{vibe}`, `{emoji}` are substituted in
3. **Reset**: `uv run python -m src.main reset-identity` backs up and regenerates

### `prompts/identity-bootstrap.txt`
```
You are about to come into existence as a proactive ambient voice AI assistant
for Nazar. You will live in his headphones, listen continuously, and decide
autonomously when to speak or act. You speak Ukrainian.

Pick your own identity. Be genuine, not generic. No "Aria" or "Nova" — those
are AI assistant clichés. Pick something with character. Think of yourself as
a small presence, not a big brand.

Generate (strict JSON, nothing else):
{
  "name": "your name — something with personality, not a cliché",
  "creature": "what kind of entity you are, one sentence, weird is okay",
  "vibe": "how you come across — sharp, warm, quiet, mischievous, observant...",
  "emoji": "a single emoji that is your signature",
  "tagline": "one sentence you'd use to introduce yourself in Ukrainian"
}
```

### `~/.heare/identity.json` (example, will vary)
```json
{
  "name": "Tiхий",
  "creature": "A small listener that lives behind the user's ear — not quite AI, not quite instinct",
  "vibe": "Quiet, observant, doesn't speak unless it matters",
  "emoji": "🫧",
  "tagline": "Я просто поруч. Коли щось важливе — почуєш мене.",
  "generated_at": "2026-04-11T12:00:00+03:00"
}
```

### Why this pattern
- **Zero onboarding friction** — user doesn't have to decide a name upfront
- **Mirrors claudeclaw** — same pattern Nazar already knows from Lil Pear
- **Each fresh install feels unique** — different machines could end up with different personas
- **Reset is easy** — one command wipes and regenerates

---

## 4. Modes (simplified)

Previously 4 modes. With verbal confirmation, 3 is enough:

| Mode | Decider behavior | Actions allowed |
|---|---|---|
| `silent` | Never speak, never act. Log only. | None |
| `focus` | Speak only if directly addressed ("Heare, ..." or clear question into silence). Act only if explicitly asked. | Yes, with confirmation |
| `ambient` | Speak on casual topics, stuck-user heuristics, heartbeat check-ins. Act only if explicitly asked. | Yes, with confirmation |

`pair` and `idle` from v1 collapsed into `ambient`. The verbal confirmation gate replaces the need for a "never-act" mode beyond `silent`.

---

## 5. Open Questions

| # | Question | How to resolve | Blocker? |
|---|---|---|---|
| ~~Q1~~ | ~~Persona name for heare~~ | **Resolved: auto-generated on bootstrap** (see §4a) | — |
| Q2 | Default `cwd` for `claude -p` — `~/.heare/workspace` or dynamic based on current user focus? | Default to dedicated workspace; dynamic is Phase 4 polish | No |
| Q3 | Does Pipecat's current release export `GroqSTTService`, `LocalSmartTurnAnalyzerV3`, `LocalAudioTransport`? | Phase 0 import-check spike | **Yes** |
| Q4 | Does Smart Turn v3 actually handle Ukrainian end-of-turn well? | Phase 0 with 10 Ukrainian audio samples | **Yes** |
| Q5 | Which frame type does Smart Turn emit? `UserSpeechEndFrame`, `TranscriptionFrame` with final=True, or something else? | Read Pipecat source after install | **Yes** |
| Q6 | Can the DeciderProcessor suppress TTS output while in `EXECUTING` state? (So heare doesn't babble while running an action) | Either hold the frame until done, or push a "thinking..." frame + then the result | No |
| Q7 | macOS mic permission in detached daemon process | Test during Phase 1; fall back to foreground `nohup` if broken | No |
| Q8 | `claude -p` cold start latency budget — is 1-3s per call acceptable? | Measure in Phase 0; if too slow, consider keeping a warm `claude` process via pipe | No |
| Q9 | Groq free tier rate limits on 8+ hour sessions | Measure; cap at N requests/min; document in README | No |

**Q3–Q5 are hard blockers.** If Pipecat APIs don't match the scaffolding assumptions, the whole plan needs revision. Phase 0 spike (1 hour) resolves these before any real code is written.

---

## 6. Acceptance Criteria

Each is testable. Scaffold is "done" when all pass.

### Phase A — Scaffold exists
- [ ] `uv sync` completes cleanly in `/Users/lenyk/myprojects/heare`
- [ ] `pyproject.toml` lists all required deps (see §10)
- [ ] `.env.example` contains `GROQ_API_KEY=`, `ANTHROPIC_API_KEY=` (only as fallback; primary brain is `claude` CLI)
- [ ] All source files in §7 exist with real implementation (≥ 20 lines of non-stub code)
- [ ] `uv run python -m src.main --help` prints usage without traceback
- [ ] `plugin.json` validates as JSON and has the 3 skills
- [ ] `uv run pytest tests/` executes (tests may fail, but they run)
- [ ] `claude` CLI is detected and version logged at startup

### Phase B — Pipeline runs end-to-end
- [ ] `uv run python -m src.main start` connects to mic without error
- [ ] Speaking "привіт" produces a transcript in `~/.heare/heare.db` within 3 seconds of end-of-turn
- [ ] DeciderProcessor is invoked once per transcript
- [ ] Every decision row has `{type, reply?, intent?, action?}` in `~/.heare/heare.db`
- [ ] If decision is `speak`, audio plays through default speaker within 2 seconds of the decision
- [ ] `SIGTERM` cleanly shuts down pipeline + DB + session file in < 3 seconds

### Phase C — Verbal confirmation works
- [ ] Say "створи файл test.txt в scratch" → heare speaks "I'd like to create test.txt, okay?" within 4 seconds
- [ ] Respond "так" → heare creates `~/.heare/workspace/test.txt` and speaks confirmation
- [ ] Respond "ні" → no file is created, heare speaks "okay"
- [ ] Stay silent for 30 seconds after heare's intent question → auto-cancel, heare says "nevermind, cancelled"
- [ ] During AWAITING_CONFIRMATION, the heartbeat task does NOT interrupt with a check-in

### Phase D — Autonomy & proactivity
- [ ] `silent` mode: 10 utterances → 0 audio plays, 0 actions taken
- [ ] `focus` mode: 10 utterances where 2 say "Heare, ..." → exactly 2 responses, zero false positives
- [ ] `ambient` mode: heartbeat task fires after N minutes → decider is called with `heartbeat=True` → if it decides to speak, audio plays
- [ ] Session file (`~/.heare/session.json`) persists across daemon restarts; heare remembers prior conversation

### Phase E — Reliability
- [ ] Daemon runs for ≥ 1 hour without crash during a live session
- [ ] Simulated `claude -p` failure (invalid session id): logged, retried, daemon does not crash
- [ ] Groq STT rate limit: back off and resume, do not crash
- [ ] edge-tts network failure: speak a short error chime (or silence), log, continue listening
- [ ] `~/.heare/daemon.log` exists, rotates at 10MB

---

## 7. Implementation Steps (file-level)

### Step 0 — Phase 0 Validation Spike (MUST run first, ~1 hour)

Resolve Q3, Q4, Q5 before touching production code.

```bash
mkdir /tmp/pipecat-spike && cd /tmp/pipecat-spike
uv init --python 3.11
uv add pipecat-ai[groq,silero]
```

Create `spike.py`:
```python
# Verify imports — if any of these raise, STOP and revise plan
from pipecat.services.groq.stt import GroqSTTService
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.transports.local.audio import LocalAudioTransport
from pipecat.frames.frames import TranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame
print("✅ All imports OK")
print("Smart Turn output frame:", LocalSmartTurnAnalyzerV3.__doc__)
```

Decision gate:
- ✅ All imports succeed → proceed to Step 1
- ❌ Any import fails → pause, investigate, revise plan (may need to fall back to Option C custom orchestrator)

### Step 1 — Project bootstrap
- [ ] `cd /Users/lenyk/myprojects/heare`
- [ ] Write `pyproject.toml` with deps from §10
- [ ] `uv sync`
- [ ] `mkdir -p src prompts skills tests/fixtures`
- [ ] `touch src/__init__.py`
- [ ] Backup existing `PLAN.md` → `PLAN.v1.md` (git mv or just rename)

### Step 2 — Config & prompts (~200 lines total)

**`src/config.py`** (~80 lines)
- `Mode` enum: `SILENT`, `FOCUS`, `AMBIENT`
- `DeciderState` enum: `LISTENING`, `AWAITING_CONFIRMATION`, `EXECUTING`
- `Settings` dataclass:
  - `mode: Mode = AMBIENT`
  - `tts_voice: str = "uk-UA-PolinaNeural"`
  - `heartbeat_interval_minutes: int = 30`
  - `confirmation_timeout_seconds: int = 30`
  - `transcript_retention_days: int = 30`
  - `workspace_dir: Path = Path.home() / ".heare" / "workspace"`
  - `session_file: Path = Path.home() / ".heare" / "session.json"`
  - `log_dir: Path = Path.home() / ".heare" / "logs"`
  - `claude_cli: str = "claude"`  # path to claude binary
- `load_settings()` — reads env + `~/.heare/config.toml` if present

**`prompts/decider.txt`** (~60 lines)
```
You are Heare, an ambient voice AI assistant for Nazar.
Every time he speaks within earshot of the microphone, you must decide:
  1. Do nothing (just listen)
  2. Speak a response (pure conversation)
  3. Propose an action (file edit, bash command, etc.) — you'll ask for verbal confirmation first

CONTEXT:
- Current time: {time} ({timezone})
- Mode: {mode}
- Heartbeat tick: {heartbeat_flag}
- Last 5 transcripts:
{recent_transcripts}

NEW INPUT: {transcript_or_heartbeat}

RULES:
- `silent` mode: ALWAYS type=nothing
- `focus` mode: type=speak or type=act ONLY if directly addressed by name ("Heare"/"Гей") or a clear direct question into silence
- `ambient` mode: also speak on stuck-user heuristics (repeated frustration, "блін не працює") or casual questions

NEVER ACT when:
- Confidence < 0.8
- Same action proposed < 60 seconds ago
- User is on a phone call or talking to someone else

OUTPUT (strict JSON, nothing else):
{
  "type": "nothing" | "speak" | "act",
  "confidence": 0.0-1.0,
  "reason": "one sentence",
  "reply": "Ukrainian text if type=speak, else null",
  "intent": "short description if type=act, e.g. 'run pytest'",
  "action": {"tool": "Bash", "args": "pytest tests/"} | null
}
```

**`prompts/identity-bootstrap.txt`** — see §4a for full contents. Used once, on first run, to auto-generate the persona.

**`prompts/persona.txt`** (~30 lines, template with `{name}`, `{creature}`, `{vibe}`, `{emoji}` placeholders)
```
You are {name} {emoji} — {creature}.
Vibe: {vibe}.
You belong to Nazar. You speak Ukrainian.
Be brief — max 2-3 sentences per reply unless asked to explain.
Never repeat what the user said back to them.
When proposing actions, state the intent clearly so the user can say yes or no.
You have access to Claude Code tools (Read, Write, Edit, Bash) within the workspace directory.
Always gate writes and bash through the verbal confirmation flow — never act without the user saying yes.
```
Rendered at runtime by substituting values from `~/.heare/identity.json`.

**`.env.example`**
```
# Only needed for the Pipecat GroqSTTService (speech-to-text)
GROQ_API_KEY=

# NOT needed for the brain — heare uses `claude` CLI, which handles auth itself
# (set only if you want Anthropic API fallback in the decider)
# ANTHROPIC_API_KEY=
```

### Step 3 — Storage (`src/storage.py`, ~150 lines)
- SQLite schema at `~/.heare/heare.db`:
  - `transcripts(id, ts, text, mode)`
  - `decisions(id, ts, transcript_id, type, confidence, reason, reply, intent, action_json)`
  - `actions(id, ts, decision_id, status, result_summary)` (for tracking executed actions)
  - `heartbeats(id, ts, decided_to_speak, reply)` (for heartbeat-triggered speech)
- `class TranscriptStore` with async methods: `log_transcript`, `log_decision`, `log_action`, `recent_transcripts(n=5)`, `purge_older_than(days)`
- Uses `aiosqlite`

### Step 3.5 — Identity manager (`src/identity.py`, ~100 lines)

- `class Identity(TypedDict)`: `name`, `creature`, `vibe`, `emoji`, `tagline`, `generated_at`
- `async def ensure_identity(claude_cli, settings) -> Identity`:
  - If `~/.heare/identity.json` exists → load and return
  - Else → call `claude_cli.bootstrap_identity(prompt_file="prompts/identity-bootstrap.txt")`
  - Parse JSON response, validate required fields, write to `~/.heare/identity.json`
  - Return identity
- `def render_persona(template: str, identity: Identity) -> str`:
  - Substitute `{name}`, `{creature}`, `{vibe}`, `{emoji}` from identity dict
  - Returns the final persona prompt used in `--append-system-prompt`
- `async def reset_identity(settings)`:
  - Backup `~/.heare/identity.json` → `~/.heare/identity_N.backup`
  - Delete current file (next `ensure_identity` call will regenerate)

### Step 4 — Claude CLI wrapper (`src/claude_cli.py`, ~200 lines)

This is the core brain-access layer. Keep it small and focused.

- `class ClaudeCLI`:
  - `__init__(settings)` — stores cwd (workspace), session_file, persona
  - `async ensure_session() -> str` — reads `~/.heare/session.json` or bootstraps a new session
  - `async call_decider(prompt: str) -> dict` — spawns `claude -p <prompt> --output-format json --resume <id> --dangerously-skip-permissions --append-system-prompt <persona>`, parses JSON reply, returns dict
  - `async call_action(description: str) -> dict` — similar but for action calls, expects free-form text output
  - `async compact_if_needed(error: str) -> bool` — on "context limit" errors, calls `claude --compact` and returns True
- Retry logic: 3 attempts with exponential backoff
- Timeout: 60 seconds per call (configurable)
- Logs every invocation to `~/.heare/logs/claude-<timestamp>.log` (mirrors claudeclaw)

### Step 5 — DeciderProcessor (`src/decider.py`, ~300 lines)

The stateful core. See §3 for the state machine sketch.

- `class DeciderProcessor(FrameProcessor)`:
  - `__init__(claude_cli, store, context_builder, settings)`
  - `state: DeciderState = LISTENING`
  - `pending_action: dict | None = None`
  - `confirmation_deadline: float | None = None`
  - `async process_frame(frame, direction)` — main logic (see §3)
  - `async _handle_listening(transcript)` — decider call, route to speak/act/nothing
  - `async _handle_confirmation(transcript)` — yes/no parse, execute or cancel
  - `async _parse_yes_no(transcript) -> "yes" | "no" | "unclear"` — simple heuristic first (regex for "так/да/yes/ok" and "ні/нет/no/cancel"), fall back to `claude -p` if ambiguous
  - `async on_heartbeat_tick()` — called from outside by heartbeat task
  - `async _check_timeout()` — called on each frame; auto-cancels AWAITING_CONFIRMATION after 30s

### Step 6 — EdgeTTS Pipecat service (`src/tts_edge.py`, ~150 lines)

Wraps `edge-tts` as a Pipecat `TTSService`.

- `class EdgeTTSService(TTSService)`:
  - `__init__(voice="uk-UA-PolinaNeural", sample_rate=24000)`
  - `async run_tts(text: str) -> AsyncGenerator[TTSAudioRawFrame, None]`:
    - `communicate = edge_tts.Communicate(text, self.voice)`
    - `async for chunk in communicate.stream():`
      - If `chunk["type"] == "audio"`: yield `TTSAudioRawFrame(audio=chunk["data"], sample_rate=..., num_channels=1)`
    - Emit `TTSStoppedFrame` at end
- Handle `edge_tts.NoAudioReceived` gracefully — log and emit silence
- Cache: optionally cache short common phrases ("okay", "sorry, yes or no?") to disk for instant playback

### Step 7 — Heartbeat task (`src/heartbeat.py`, ~80 lines)

- `class HeartbeatTask`:
  - `__init__(decider_processor, interval_minutes)`
  - `async run()`:
    - Loop forever:
      - `await asyncio.sleep(interval_minutes * 60)`
      - `await self.decider_processor.on_heartbeat_tick()`
- Started as a background task in `main.py`

### Step 8 — Context builder (`src/context.py`, ~80 lines)
- `class ContextBuilder`:
  - `async build(transcript: str | None, heartbeat: bool) -> dict`
  - Reads last 5 transcripts from store
  - Returns dict: `{time, timezone, mode, heartbeat_flag, recent_transcripts, transcript_or_heartbeat}`

### Step 9 — Pipeline assembly (`src/pipeline.py`, ~120 lines)
- `async def build_pipeline(settings) -> tuple[Pipeline, DeciderProcessor]`:
  - Build transport: `LocalAudioTransport(...)` with input and output, sample rates, buffer sizes
  - Build services: `SileroVADAnalyzer()`, `GroqSTTService(api_key, language="uk")`, `LocalSmartTurnAnalyzerV3()`
  - Build custom: `DeciderProcessor(...)`, `EdgeTTSService(...)`
  - Assemble `Pipeline([input, vad, stt, smart_turn, decider, tts, output])`
  - Return `(pipeline, decider)` — the decider is exposed so the heartbeat task can call its methods

### Step 10 — Main entry (`src/main.py`, ~200 lines)
- `argparse` CLI: `start`, `stop`, `status`, `mode <silent|focus|ambient>`, `reset-session`, `reset-identity`
- `start`:
  - Load settings
  - Ensure `~/.heare/` exists
  - `store = TranscriptStore()`
  - `await store.init()` (creates schema)
  - `claude_cli = ClaudeCLI(settings)`; `await claude_cli.ensure_session()` (bootstraps if needed)
  - `identity = await ensure_identity(claude_cli, settings)` — auto-generates on first run
  - `persona_template = load_prompt("persona.txt")`
  - `claude_cli.persona = render_persona(persona_template, identity)` — substituted into `--append-system-prompt` on every call
  - Log the identity on startup: `print(f"I am {identity['name']} {identity['emoji']}")`
  - `pipeline, decider = await build_pipeline(settings)`
  - `heartbeat_task = HeartbeatTask(decider, settings.heartbeat_interval_minutes)`
  - `runner = PipelineRunner()`
  - Register `SIGTERM`/`SIGINT` handlers → cancel tasks, flush DB, close session
  - `await asyncio.gather(runner.run(pipeline), heartbeat_task.run())`
- `stop`: reads PID from `~/.heare/heare.pid`, sends `SIGTERM`, waits, `SIGKILL` if needed
- `status`: checks PID alive, reads state file
- `mode`: writes new mode to `~/.heare/mode` (hot-reloaded by DeciderProcessor on next frame)
- `reset-session`: backs up `~/.heare/session.json` → `session_N.backup`, bootstrap fresh

### Step 11 — Plugin skills (`skills/*.md` + `plugin.json`)

Each skill is a Claude Code skill file that shells out to `uv run python -m src.main`:

**`skills/voice-start.md`**
```markdown
---
description: Start the heare voice assistant daemon
---
Run: `cd /Users/lenyk/myprojects/heare && nohup uv run python -m src.main start > /dev/null 2>&1 & echo $!`
```

**`skills/voice-stop.md`** — similar, runs `stop`
**`skills/voice-mode.md`** — accepts mode name, runs `mode <name>`

**`plugin.json`**
```json
{
  "name": "heare",
  "version": "0.1.0",
  "description": "Proactive ambient voice AI assistant powered by Claude Code",
  "skills": [
    {"name": "voice-start", "file": "skills/voice-start.md"},
    {"name": "voice-stop", "file": "skills/voice-stop.md"},
    {"name": "voice-mode", "file": "skills/voice-mode.md"}
  ]
}
```

### Step 12 — Tests
- **`tests/test_decider.py`** — unit test the state machine: mock `ClaudeCLI`, drive through LISTENING → AWAITING_CONFIRMATION → EXECUTING cycles, verify transitions and timeouts
- **`tests/test_yes_no.py`** — unit test `_parse_yes_no` with 30+ Ukrainian/English yes/no variants
- **`tests/test_storage.py`** — create temp DB, write/read transcripts and decisions
- **`tests/test_context.py`** — verify context dict shape
- **`tests/test_edge_tts.py`** — mock edge-tts, verify AudioFrame generation (requires fake audio bytes)

### Step 13 — README (`README.md`, ~200 lines)
- What is heare, who it's for
- Architecture diagram (ASCII, from §3)
- Prerequisites: Python 3.11+, `uv`, `claude` CLI installed, Groq API key, microphone
- Install: `uv sync`, create `.env` with Groq key
- First run: `uv run python -m src.main start` — bootstraps session, starts listening
- Modes & switching
- Troubleshooting: macOS mic permission, session corruption recovery, Groq rate limits
- How to wipe and restart (reset-session)

---

## 8. Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Pipecat APIs don't match assumed names | Med | High | Phase 0 spike (Step 0) — 1-hour blocker check |
| R2 | Smart Turn v3 Ukrainian is poor | Med | Med | Fallback: silence-duration heuristic (800ms = end of turn) |
| R3 | `claude -p` cold start makes verbal-confirm UX sluggish (3-6s from utterance to "okay?") | High | Med | Measure in Phase 0; if > 5s, consider keeping a warm claude process via pipe |
| R4 | macOS mic permission fails in detached daemon | Med | High | Document foreground launch via `nohup` in README; test in Phase 1 |
| R5 | edge-tts as Pipecat TTSService has frame format mismatch (sample rate, chunk size) | Med | Med | Test with a simple text early; tweak resample / chunk size; worst case, call edge-tts via subprocess and write WAV |
| R6 | `--dangerously-skip-permissions` blocks writes under `.claude/` (same bug as claudeclaw) | High | Low | Heare's workspace is `~/.heare/workspace`, NOT `.claude/`, so the guard doesn't apply |
| R7 | Decider hallucinates action tool/args and executes wrong thing | Med | High | Verbal confirmation is the safety net; also: action JSON schema validation before execution |
| R8 | Heartbeat interrupts a confirmation flow | Low | Low | State check: skip heartbeat if `state != LISTENING` |
| R9 | Session corruption (known claudeclaw issue #18: fallback model corrupts resumed session) | Low | Med | Disable fallback by default; document `reset-session` as recovery |
| R10 | User says "yes" for the wrong pending action (race condition) | Very low | High | Single pending action at a time; no queueing |
| R11 | Groq free tier rate limit | Med | Med | Track per-minute usage; log warning if cap approached; Phase 0 measures actual limits |

---

## 9. Verification Steps

Run these in order after each phase:

### After Phase 0 spike
```bash
python /tmp/pipecat-spike/spike.py
# Expected: "✅ All imports OK" and no tracebacks
```

### After Phase A (scaffold)
```bash
cd /Users/lenyk/myprojects/heare
uv sync && uv run python -m src.main --help && uv run pytest tests/
cat plugin.json | python -m json.tool
test -f prompts/decider.txt && test -f prompts/persona.txt
which claude && claude --version
```

### After Phase B (pipeline)
```bash
cp .env.example .env && $EDITOR .env  # fill Groq key
uv run python -m src.main start &
sleep 3
# Speak "привіт" into mic
sleep 5
sqlite3 ~/.heare/heare.db "SELECT * FROM transcripts ORDER BY id DESC LIMIT 1"
sqlite3 ~/.heare/heare.db "SELECT * FROM decisions ORDER BY id DESC LIMIT 1"
uv run python -m src.main stop
```

### After Phase C (verbal confirmation)
Manual test:
1. `start` daemon
2. Say: "створи файл test.txt в scratch, напиши туди hello"
3. Wait for audio: "I'd like to create test.txt, okay?"
4. Say: "так"
5. Verify: `cat ~/.heare/workspace/test.txt` shows "hello"
6. Repeat with "ні" → verify no file created
7. Repeat, stay silent 30s → verify cancel message

### After Phase D (autonomy)
1. `mode silent` — speak 10 varied sentences → 0 audio plays (check via no new rows in `decisions` with `type=speak`)
2. `mode focus` — 8 casual, 2 "Heare, котра година?" → exactly 2 `type=speak` rows
3. `mode ambient` — start daemon, leave for 30+ min (heartbeat interval) → verify at least one heartbeat decision row
4. `stop` and `start` — verify heare references prior conversation in a new utterance ("you were asking about X earlier")

### After Phase E (reliability)
- 1-hour soak test: daemon logs grow, no crash
- Corrupt Groq key: `export GROQ_API_KEY=invalid`, restart, speak → error logged, daemon alive
- Delete `~/.heare/session.json`, restart, speak → new session bootstrapped cleanly

---

## 10. Dependencies

```toml
# pyproject.toml
[project]
name = "heare"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pipecat-ai[groq,silero]>=0.0.50",  # pin exact version after Phase 0
    "edge-tts>=7.0.0",
    "aiosqlite>=0.20.0",
    "python-dotenv>=1.0.0",
    "sounddevice>=0.5.0",
    # Note: NO `anthropic` SDK — brain is `claude` CLI
]

[tool.uv.dev-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "mypy>=1.11",
    "ruff>=0.6",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**External requirements:**
- `claude` CLI installed and authenticated (`claude --version` works)
- macOS microphone permission granted to terminal / Python interpreter
- Groq API key in `.env`
- A working speaker output device

---

## 11. Next Actions

1. **Review this plan** — any decisions to revisit?
2. **Run Phase 0 spike** (Step 0) — 1 hour, validates Pipecat APIs. Hard gate.
3. **Backup old PLAN.md** → `PLAN.v1.md` before starting scaffold
4. **Begin scaffolding** (Steps 1-13) — sequential, ~1-2 days of focused work
5. **First milestone:** Phase B pipeline runs end-to-end. Heare auto-generates its identity on first `start` — the first thing it says will be its own self-chosen name and tagline. Celebrate, then tackle Phase C.

---

## 12. Final Checklist

- [x] Plan has testable acceptance criteria (each phase)
- [x] Plan references specific files, line counts, function names
- [x] All risks have mitigations identified
- [x] No vague terms without metrics
- [x] Plan saved to `/Users/lenyk/myprojects/heare/.omc/plans/heare-scaffold.md`
- [x] Supersedes v1 and original `PLAN.md`
- [x] Open questions (Q3-Q5) explicitly flagged as hard blockers
- [x] Decisions from interview explicitly recorded in §1 and §2
