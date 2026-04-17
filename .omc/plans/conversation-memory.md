# Conversation Memory System - Plan

## Overview
Design and implement a conversation memory system for Heare that enables coherent multi-turn dialogue, context awareness, and optimized API usage through intelligent utterance aggregation.

## Current State Analysis

### Existing Architecture
- **VAD (Silero)**: Waits 0.5s silence before end-of-speech
- **SmartTurn V3**: ML-based anti-fragmentation (1.0s stop) - handles micro-pauses within sentences
- **STT (Groq)**: Processes each utterance independently
- **Decider**: Gets last 5 raw transcripts as context
- **Pipeline**: Frame-based processing, no conversation state
- **Database**: Stores transcripts, decisions, actions, heartbeats

### SmartTurn V3 Integration Strategy

**Understanding SmartTurn V3's Role**:
SmartTurn V3 operates at the micro-fragmentation level (1.0s stop) to prevent sentence breaks like "Hello [pause] world" from being split. This is fundamentally different from conversation-level turn detection.

**Why TurnAggregator is Still Needed**:
SmartTurn V3 solves the "micro-pause" problem but not the "conversation turn" problem:
- **SmartTurn V3**: "Hello [0.8s pause] world" → single utterance "Hello world" ✓
- **TurnAggregator**: "I'm thinking about [2s pause] the project [1s pause] and we should focus on X" → single turn with context ✓

**Complementary Operation**:
```
VAD (Silero) → SmartTurn V3 (anti-fragmentation) → TurnAggregator (conversation turns) → Decider
    ↓                ↓                                  ↓                        ↓
  0.5s silence    1.0s ML stop                     0.5-3.0s mode-aware      Context-aware
                  (micro-pauses)                   silence detection         decision
```

**Integration Approach**:
TurnAggregator is implemented as a **frame processor** in the Pipecat pipeline, positioned after STT but before the Decider:

```
VAD (Silero) → SmartTurn V3 (anti-fragmentation) → STT (Groq) → TurnAggregator (frame processor) → Decider
    ↓                ↓                                  ↓                ↓                            ↓
  0.5s silence    1.0s ML stop                     Utterance        Buffers & emits              Context-aware
                  (micro-pauses)                   frames           AggregatedTranscriptFrame    decision
```

**Frame-based integration** (not event-driven):
1. **TurnAggregator as FrameProcessor**: Implements Pipecat's `FrameProcessor` interface
2. **Intercepts TranscriptionFrame**: Consumes utterance frames from STT
3. **Buffers or emits**: Either buffers the frame OR emits `AggregatedTranscriptFrame`
4. **No event subscription**: Works with Pipecat's actual frame-based pipeline architecture

**Implementation**:
```python
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import TranscriptionFrame
from dataclasses import dataclass

# Define custom frame type for aggregated turns
@dataclass
class AggregatedTranscriptFrame(TranscriptionFrame):
    """Frame containing aggregated utterances from a conversation turn."""
    utterance_count: int
    turn_start_ts: float
    turn_end_ts: float

class TurnAggregator(FrameProcessor):
    """Frame processor that aggregates utterances into conversation turns."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # Only handle TranscriptionFrame objects
        if isinstance(frame, TranscriptionFrame):
            should_submit, aggregated_text = await self.add_utterance(
                frame.text, time.time()
            )

            if not should_submit:
                # Don't push frame downstream yet (buffering)
                return

            # Turn complete! Emit aggregated frame to decider
            await self.push_frame(AggregatedTranscriptFrame(
                text=aggregated_text,
                utterance_count=len(self.buffer),
                turn_start_ts=self.turn_start_ts,
                turn_end_ts=time.time(),
            ))
        else:
            # Pass through all other frames
            await self.push_frame(frame, direction)
```

**Pipeline insertion** (in `src/pipeline.py`):
```python
from src.turn_aggregator import TurnAggregator

# After STT, insert TurnAggregator
turn_aggregator = TurnAggregator(
    mode=settings.mode,
    focus_timeout=0.5,
    ambient_timeout=3.0,
)

pipeline = [
    transport.input(),
    audio_buffer_processor,
    stt_service,
    speaker_tagger,  # Speaker ID must tag BEFORE aggregation
    turn_aggregator,  # NEW: Insert here (after speaker_tagger)
    decider,
    # ... rest of pipeline
]
```

### Identified Issues
1. **Fragmented Utterances**: User speech broken into multiple transcripts
2. **No Conversation Memory**: Each decision made in isolation
3. **Lost Context**: Can't reference previous topics effectively
4. **API Inefficiency**: Many small STT calls instead of aggregated ones
5. **Raw Transcripts Only**: No topic extraction or summarization

## RALPLAN-DR Summary

### Principles
1. **Conversation Continuity**: Maintain coherent thread across multiple turns
2. **Contextual Intelligence**: Remember topics, entities, and user preferences
3. **Performance Optimization**: Reduce API calls and latency through aggregation
4. **Flexible Architecture**: Configurable strategies for different modes (silent/focus/ambient)
5. **Privacy-First**: User control over what's stored and for how long

### Decision Drivers
1. **User Experience**: Natural, human-like conversation flow
2. **Cost Efficiency**: Minimize Groq STT API calls through intelligent batching
3. **Latency vs Accuracy**: Balance responsiveness (quick replies) vs completeness (wait for full thought)
4. **Resource Constraints**: Memory usage, database size, processing overhead
5. **Mode Adaptation**: Different strategies for silent/focus/ambient modes

### Viable Options

#### Option 1: Turn-Based Aggregation with Conversation State (RECOMMENDED)

**Approach**: Aggregate utterances within a "turn" (conversation segment) and maintain conversation state.

**Key Features**:
- Turn detector: Wait for user pause (2-3s silence) to mark turn boundary
- Conversation state: Active topics, entity memory, user intent tracking
- Tiered context: Recent transcripts (5) + conversation summary + entity map
- Mode-aware aggregation: Immediate in focus mode, patient in ambient mode

**Pros**:
- Natural conversation flow (multi-sentence thoughts processed together)
- Significant API cost reduction (3-5x fewer STT calls)
- Rich context for AI decisions (topics, entities, not just raw text)
- Flexible strategy per mode
- Proven pattern (LLM chat assistants use similar approach)

**Cons**:
- Increased latency in ambient mode (waiting for turn completion)
- More complex state management
- Requires topic extraction logic
- Risk of "too long" turns in monologue scenarios

**Invalidation Rationale for Alternative**: Option 2's streaming approach is too complex for current needs. Option 3's simple buffer lacks context richness. Option 1 balances naturalness, performance, and implementability.

#### Option 2: Streaming Context with Sliding Window

**Approach**: Process utterances immediately but maintain streaming conversation context.

**Key Features**:
- No waiting: Process each utterance immediately (current behavior)
- Streaming context: Continuously updated conversation summary
- Sliding window: Weighted context (recent = high weight, older = decay)
- Incremental topic updates: Extract and merge topics per utterance

**Incremental Topic Merging Strategy**:
```python
class StreamingConversationManager:
    async def update_context(self, new_utterance: str) -> dict:
        """
        Update conversation context incrementally.

        Strategy:
        1. Extract topics from new utterance
        2. Merge with existing topics:
           - If topic exists: bump weight +0.2
           - If new topic: add with weight 1.0
           - Decay all topics by 0.05 (old topics fade)
        3. Update summary: append key points, truncate if > 500 chars
        4. Build sliding window context (last 5 utterances weighted 2.0x)
        """
        new_topics = await self.extract_topics(new_utterance)

        # Merge with topic weights
        for topic in new_topics:
            if topic in self.topic_weights:
                self.topic_weights[topic] += 0.2
            else:
                self.topic_weights[topic] = 1.0

        # Decay all topics
        self.topic_weights = {t: w * 0.95 for t, w in self.topic_weights.items()}

        # Prune low-weight topics
        self.topic_weights = {t: w for t, w in self.topic_weights.items() if w > 0.3}

        return self.build_context()
```

**Pros**:
- Low latency (immediate processing)
- Responsive to interruptions
- Progressive context building
- No "waiting" feeling

**Cons**:
- No API cost reduction (still many small STT calls)
- Complexity in incremental topic merging (weight tuning, decay rates)
- Risk of fragmented understanding (no "complete thought" detection)
- Higher complexity for same UX benefit
- Topic drift: Gradual weight changes may lose sharp context shifts

**When This Shines**:
- Real-time transcription scenarios where latency is critical
- Situations where user frequently interrupts themselves
- Systems where API cost is not a concern

**Why Rejected**: Streaming adds significant complexity without solving the core inefficiency (too many API calls) and doesn't provide better conversation experience than Option 1. The incremental topic merging is complex to tune (decay rates, weight thresholds) and may not capture sharp context shifts as well as turn-based aggregation.

#### Option 3: Simple Utterance Buffer with Timeout

**Approach**: Buffer utterances for N seconds, then process as batch.

**Key Features**:
- Fixed timeout: 2-3s buffer window
- No topic extraction: Just concatenate transcripts
- Minimal state: Simple buffer + timer
- No conversation memory: Process and discard

**Implementation**:
```python
class SimpleBuffer:
    def __init__(self, timeout: float = 2.0):
        self.buffer: list[str] = []
        self.timeout = timeout
        self._timer: asyncio.Task | None = None

    async def add_utterance(self, text: str) -> tuple[bool, str]:
        """Add to buffer, reset timer. Returns (should_submit, aggregated)."""
        self.buffer.append(text)

        if self._timer:
            self._timer.cancel()

        self._timer = asyncio.create_task(self._timeout())
        return False, ""  # Don't submit yet

    async def _timeout(self) -> None:
        await asyncio.sleep(self.timeout)
        # Submit buffer
        aggregated = " ".join(self.buffer)
        self.buffer.clear()
        await self.process(aggregated)
```

**Pros**:
- Simple implementation (~50 lines vs 200+ for Option 1)
- Reduces API calls (same benefit as Option 1)
- Low complexity, easy to debug
- Minimal memory overhead

**Cons**:
- Dumb concatenation (no understanding of topic shifts)
- Rigid timeout (can't adapt to user pace or mode)
- No conversation memory beyond recent transcripts
- Poor UX in focus mode (user expects quick replies)
- No topic/entity tracking
- Can't handle "What did you say about X?" queries

**When This Shines**:
- **Silent mode logging**: Buffer utterances for logging, don't process
- **Background transcription**: Capture full speech for later review
- **API reduction only**: When cost is the only concern, not conversation quality
- **MVP scenarios**: Quick prototype before building full conversation system

**Why Rejected**: Too simplistic for the user's request of "best conversation experience we possible to do." Doesn't provide conversation intelligence, just text batching. Option 1 provides this benefit plus rich context (topics, entities, conversation summaries). However, this could be a useful fallback mode if Option 1 proves too complex.

### Selected Approach: Option 1 - Turn-Based Aggregation

**Rationale**: Best balance of natural conversation flow, API efficiency, and rich context. Aligns with user's request for "best conversation experience we possible to do" while optimizing calls through intelligent waiting ("wait some time to let user finish speech").

## Implementation Plan

### Phase 1: Database Schema & Conversation State

**File**: `src/storage.py`

Add new tables:

### Conversation Lifecycle Management

**What Starts a Conversation?**
- **Automatic**: First utterance after system startup or after previous conversation ended
- **Mode switch**: User switches from SILENT to FOCUS/AMBIENT (ends previous, starts new)
- **Explicit**: User command "Heare start conversation" (future enhancement)
- **Timeout**: No activity for 30 minutes → auto-end conversation

**What Ends a Conversation?**
- **Mode switch**: User switches from FOCUS/AMBIENT to SILENT
- **Explicit**: User command "Heare end conversation" (future enhancement)
- **Timeout**: No activity for 30 minutes
- **System shutdown**: Clean shutdown saves conversation state

**Implementation**:
```python
class ConversationManager:
    async def get_or_create_active(self) -> int:
        """
        Get active conversation or create new one.

        Logic:
        1. Check for active conversation (end_ts IS NULL)
        2. If found and < 30min old → reuse
        3. If found but > 30min old → end it, create new
        4. If none → create new
        """
        active = await self.store.get_active_conversation()
        if active:
            age = time.time() - active["start_ts"]
            if age > (30 * 60):  # 30 minutes
                await self.store.end_conversation(active["id"])
            else:
                return active["id"]

        # Create new conversation
        return await self.store.start_conversation(mode=settings.mode)

    async def on_mode_change(self, new_mode: Mode) -> None:
        """
        Handle mode switch.

        Logic:
        - SILENT → FOCUS/AMBIENT: Start new conversation
        - FOCUS/AMBIENT → SILENT: End current conversation
        - FOCUS ↔ AMBIENT: Continue current conversation (update mode)
        """
        if new_mode == Mode.SILENT:
            await self.end_current_conversation()
        elif settings.mode == Mode.SILENT:
            # Was silent, now active → start new
            await self.get_or_create_active()
        else:
            # Mode switch within active modes → just update
            await self.store.update_conversation_mode(active_id, new_mode)
```

### State Synchronization with FSM

**FSM States**: `LISTENING` | `AWAITING_CONFIRMATION` | `EXECUTING`

**Conversation State**: `active` | `ended`

**Synchronization Plan**:

1. **LISTENING + Active Conversation**:
   - Normal operation: Utterances → TurnAggregator → Decider
   - Conversation state updated after each turn

2. **AWAITING_CONFIRMATION + Active Conversation**:
   - Pause turn aggregation (don't submit new turns)
   - Keep conversation active (will resume after confirmation)
   - Buffer new utterances but don't process

3. **EXECUTING + Active Conversation**:
   - Pause turn aggregation (action in progress)
   - Keep conversation active
   - Resume after action completes

4. **Mode Switch → FSM Reset**:
   - If SILENT mode: End conversation, FSM → LISTENING (but don't process)
   - If FOCUS/AMBIENT mode: Start conversation, FSM → LISTENING

**Implementation**:
```python
class DeciderProcessor:
    async def _handle_listening(self, transcript: str, frame: TranscriptionFrame) -> None:
        """Only process if in LISTENING state with active conversation."""
        if self.fsm_state != FSMState.LISTENING:
            logger.debug(f"Not in LISTENING state, ignoring transcript")
            return

        if settings.mode == Mode.SILENT:
            logger.debug("Silent mode, not processing")
            return

        # Normal processing
        should_submit, aggregated = await self.turn_aggregator.add_utterance(transcript, frame.timestamp)
        # ...
```

### Database Schema

```sql
-- Conversation sessions (coherent discussion threads)
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts REAL NOT NULL,
    end_ts REAL,  -- NULL if active
    mode TEXT NOT NULL,  -- silent/focus/ambient
    summary TEXT,  -- Updated summary of topics discussed
    entity_map TEXT  -- JSON: entities mentioned (people, places, things)
);

-- Conversation turns (aggregated utterances)
CREATE TABLE turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    aggregated_text TEXT NOT NULL,  -- Combined utterances in turn
    utterance_count INTEGER NOT NULL,  -- How many fragments were combined
    topic_tags TEXT,  -- JSON: extracted topics
    PRIMARY KEY (conversation_id, id),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

-- Link transcripts to turns
ALTER TABLE transcripts ADD COLUMN turn_id INTEGER REFERENCES turns(id);
```

**Files to modify**:
- `src/storage.py` (20 lines): Add `ConversationStore` class with methods:
  - `start_conversation(mode)` → conversation_id
  - `end_conversation(conversation_id)`
  - `create_turn(conversation_id, aggregated_text, topic_tags)`
  - `get_active_conversation()`
  - `update_conversation_summary(conversation_id, summary, entity_map)`
  - `get_recent_context(conversation_id, n=3)` → Last N turns with topics

### Phase 2: Turn Detection & Aggregation Logic

**File**: `src/turn_aggregator.py` (NEW, ~200 lines)

Create turn detector that buffers utterances and decides when to submit:

```python
class TurnAggregator:
    """Buffers utterances and aggregates them into conversation turns."""

    def __init__(
        self,
        mode: Mode,
        focus_timeout: float = 0.5,
        ambient_timeout: float = 3.0,
        max_turn_duration: float = 30.0,
        max_buffer_size: int = 50,  # Max utterances per turn
        on_turn_complete: Callable[[str, float, list[dict]], Awaitable[None]] | None = None,
    ):
        self.mode = mode
        self.focus_timeout = focus_timeout
        self.ambient_timeout = ambient_timeout
        self.max_turn_duration = max_turn_duration
        self.max_buffer_size = max_buffer_size
        self.on_turn_complete = on_turn_complete

        # Buffer state
        self.buffer: list[dict] = []  # Each: {"text": str, "utterance_ts": float}
        self.last_utterance_ts: float | None = None
        self.turn_start_ts: float | None = None
        self._timeout_task: asyncio.Task | None = None

    async def add_utterance(self, text: str, utterance_ts: float) -> tuple[bool, str | None]:
        """
        Add utterance to buffer. Returns (should_submit, aggregated_text).

        Decision logic:
        - Focus mode: Submit after 0.5s silence (user expects quick replies)
        - Ambient mode: Submit after 3.0s silence (let user finish thought)
        - Force submit after 30s regardless of silence
        - Force submit if buffer exceeds max_buffer_size
        """
        # Initialize turn start on first utterance
        if self.turn_start_ts is None:
            self.turn_start_ts = utterance_ts

        # Check max buffer size
        if len(self.buffer) >= self.max_buffer_size:
            logger.warning(f"TurnAggregator: buffer full ({self.max_buffer_size}), forcing submit")
            return True, self._aggregate_and_clear()

        # Check max turn duration
        if self.turn_start_ts and (utterance_ts - self.turn_start_ts) > self.max_turn_duration:
            logger.warning(f"TurnAggregator: max duration ({self.max_turn_duration}s) reached, forcing submit")
            return True, self._aggregate_and_clear()

        # Add to buffer (preserve utterance timestamp)
        self.buffer.append({
            "text": text,
            "utterance_ts": utterance_ts
        })
        self.last_utterance_ts = utterance_ts

        # Cancel any existing timeout task
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()

        # Determine timeout based on mode
        timeout = self.focus_timeout if self.mode == Mode.FOCUS else self.ambient_timeout

        # Start new timeout task
        self._timeout_task = asyncio.create_task(self._timeout_handler(timeout))

        return False, None

    async def _timeout_handler(self, timeout_seconds: float) -> None:
        """Wait for silence timeout, then submit turn."""
        await asyncio.sleep(timeout_seconds)

        # Check if we should submit (no new utterances during timeout)
        if self.last_utterance_ts:
            time_since_last = time.time() - self.last_utterance_ts
            if time_since_last >= timeout_seconds:
                logger.info(f"TurnAggregator: {timeout_seconds}s silence detected, submitting turn")
                # Submit turn (call handler)
                await self._submit_turn()

    def _aggregate_and_clear(self) -> str:
        """
        Aggregate buffered utterances and clear buffer.

        Returns:
            Aggregated text with spaces between utterances.
        """
        # Aggregate with spaces
        aggregated = " ".join(item["text"] for item in self.buffer)

        # Clear buffer state
        self.buffer.clear()
        self.last_utterance_ts = None
        self.turn_start_ts = None

        # Cancel timeout task if running
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()

        return aggregated

    async def _submit_turn(self) -> None:
        """
        Submit turn for processing (callback to decider).
        This is called when timeout expires or conditions met.
        """
        if not self.buffer:
            return

        aggregated = self._aggregate_and_clear()

        # Callback to decider (injected in __init__)
        if self.on_turn_complete:
            await self.on_turn_complete(aggregated)

    async def on_mode_change(self, new_mode: Mode) -> None:
        """
        Handle mode switch mid-turn.

        Strategy:
        - If buffer has content: Submit immediately (don't wait)
        - If buffer empty: Just update mode
        """
        if self.buffer:
            logger.info(f"TurnAggregator: mode changed from {self.mode} to {new_mode}, submitting pending turn")
            await self._submit_turn()

        self.mode = new_mode

    async def shutdown(self) -> None:
        """Cleanup on shutdown. Submit any pending turn."""
        if self.buffer:
            logger.info("TurnAggregator: shutdown, submitting pending turn")
            await self._submit_turn()

        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
```

**Complete TurnAggregator Implementation Details**:

**Buffer Management**:
- Buffer is list of dicts: `[{"text": str, "utterance_ts": float}, ...]`
- Cleared after each submit via `_aggregate_and_clear()`
- Max size enforced (50 utterances) to prevent memory issues

**Timestamp Preservation**:
- `utterance_ts`: Original timestamp from SmartTurn V3 (when speech ended)
- `turn_start_ts`: When first utterance of turn arrived (for duration tracking)
- Both preserved separately for debugging and analytics

**Mode Change Handling**:
- If mode changes mid-turn: Submit immediately (don't wait for timeout)
- Example: User switches from ambient to focus → expect quick replies

**Memory Limit Enforcement**:
- `max_buffer_size=50`: Prevents runaway buffer growth
- `max_turn_duration=30.0`: Forces submit even if user keeps talking

**Error Handling** (not shown above, add in implementation):
```python
try:
    await self.add_utterance(text, ts)
except Exception as e:
    logger.error(f"TurnAggregator error: {e}, clearing buffer")
    self.buffer.clear()
    self._timeout_task.cancel()
```

**Decision Tree**:
```
Focus mode (direct interaction):
├─ Gap > 0.5s since last utterance? → Submit turn
├─ Turn duration > 30s? → Submit turn (force)
└─ Buffer size > 50 utterances? → Submit turn (force)

Ambient mode (background companion):
├─ Gap > 3.0s since last utterance? → Submit turn
├─ Turn duration > 30s? → Submit turn (force)
├─ Buffer size > 50 utterances? → Submit turn (force)
└─ Empty buffer + heartbeat? → Don't submit (wait for real speech)

Silent mode:
└─ Never submit (buffer and discard)

Note: Anti-fragmentation (gap < 0.2s) handled by SmartTurn V3, not TurnAggregator
```

**Files to create**:
- `src/turn_aggregator.py` (~200 lines)
- `tests/test_turn_aggregator.py` (~150 lines): Test all timeout scenarios

### Phase 3: Topic Extraction & Conversation Summary

**File**: `src/conversation.py` (NEW, ~150 lines)

Extract topics and maintain conversation state:

```python
class ConversationManager:
    """Maintains conversation state: topics, entities, summary."""

    def __init__(self, store: ConversationStore, claude_cli: ClaudeBackend):
        self.store = store
        self.claude = claude_cli

    async def extract_topics(self, text: str) -> list[str]:
        """
        Extract topic tags from aggregated turn text.

        Uses lightweight Claude call with structured output:
        - Return top 3-5 topics as short phrases (2-4 words each)
        - Examples: "weather forecast", "calendar meeting", "code debugging"
        """
        # ... (implementation)

    async def update_summary(
        self,
        conversation_id: int,
        turn_text: str,
        topics: list[str],
    ) -> None:
        """
        Update conversation summary with new turn.

        Strategy:
        - Keep last 3 turns verbatim
        - Older turns → summarize into key points
        - Track entities: people, places, dates, projects
        - Maintain "active topics" set (mentioned in last 2 turns)
        """
        # ... (implementation)

    async def build_context(
        self,
        conversation_id: int | None,
    ) -> dict[str, Any]:
        """
        Build rich context for decider prompt.

        Returns:
        {
            "conversation_active": bool,
            "conversation_summary": str,  # What we're discussing
            "active_topics": list[str],  # Topics in last 2 turns
            "entities": dict[str, str],  # people, places, things
            "recent_turns": list[dict],  # Last 3 turns with topics
            "recent_transcripts": str,  # Fallback: raw transcripts (current behavior)
        }
        """
        # ... (implementation)
```

**Files to create**:
- `src/conversation.py` (~150 lines)
- `tests/test_conversation.py` (~100 lines): Mock Claude calls

### Phase 4: Decider Integration

**File**: `src/decider.py` (modify, ~50 lines changes)

Integrate turn aggregator and conversation manager:

```python
class DeciderProcessor:
    def __init__(
        self,
        claude_cli: "ClaudeBackend",
        store: "TranscriptStore",
        context_builder: "ContextBuilder",
        turn_aggregator: "TurnAggregator",  # NEW
        conversation_manager: "ConversationManager",  # NEW
    ):
        # ... existing init ...
        self.turn_aggregator = turn_aggregator
        self.conversation = conversation_manager

    async def _handle_listening(
        self,
        transcript: str,
        frame: TranscriptionFrame,
    ) -> None:
        """
        Handle transcript in LISTENING state.

        NEW FLOW:
        1. Add utterance to turn aggregator
        2. If aggregator says "submit turn":
           a. Extract topics from aggregated text
           b. Update conversation state
           c. Build rich context (summary + topics + entities)
           d. Call decider with aggregated text + context
        3. Else: buffer and wait (don't process yet)
        """
        should_submit, aggregated_text = await self.turn_aggregator.add_utterance(
            transcript, time.time()
        )

        if not should_submit:
            # Buffering, don't process yet
            logger.debug("TurnAggregator: buffering utterance")
            return

        # Turn complete! Process aggregated turn.
        logger.info("TurnAggregator: turn complete, processing %s utterances",
                   len(self.turn_aggregator.buffer))

        # Extract topics
        topics = await self.conversation.extract_topics(aggregated_text)

        # Update conversation state
        conv_id = await self.conversation.get_or_create_active()
        await self.conversation.update_summary(conv_id, aggregated_text, topics)

        # Build rich context
        ctx = await self.conversation.build_context(conv_id)
        ctx["transcript_or_heartbeat"] = aggregated_text  # Use aggregated text

        # Existing decision logic...
        decision = await self._decide(ctx, aggregated_text)
        # ...
```

**Mode-Specific Behavior**:
```python
# Focus mode: Quick replies (shorter timeout)
if settings.mode == Mode.FOCUS:
    turn_aggregator.focus_timeout = 0.5

# Ambient mode: Patient (longer timeout)
if settings.mode == Mode.AMBIENT:
    turn_aggregator.ambient_timeout = 3.0
    # Enable topic-based follow-ups!
    ctx["enable_followups"] = True

# Silent mode: Never submit
if settings.mode == Mode.SILENT:
    return  # Don't process anything
```

**Files to modify**:
- `src/decider.py` (~50 lines): Add turn aggregation flow
- `tests/test_decider.py` (~80 lines): Test turn-based decisions

### Phase 5: Prompt Updates

**File**: `prompts/decider.txt` (modify, ~20 lines changes)

Update decider prompt to use conversation context:

```text
CONTEXT:
- Current time: {time} ({timezone})
- Mode: {mode}
- Heartbeat tick: {heartbeat_flag}

CONVERSATION: {conversation_active}
{conversation_summary}

Active topics: {active_topics}
Entities: {entities}

Recent conversation turns:
{recent_turns}

{silence_block}
{speaker_rule_block}
NEW INPUT: {transcript_or_heartbeat}

RULES:
- In ambient mode: Reference conversation topics proactively. "You mentioned X earlier..."
- In focus mode: Direct replies are fine, but use context if relevant
- In silent mode: Always nothing

[... existing rules ...]

OUTPUT — strict JSON, ONE of these exact patterns, nothing else, no markdown fences:
- nothing: {"t":"n"}
- speak:   {"t":"s","r":"<Ukrainian reply, MAX 15 words>"}
- act:     {"t":"a","c":<0.0-1.0>,"i":"<short intent verb phrase, MAX 5 words>","x":{"tool":"Bash","args":"<cmd>"}}
```

**New Context Variables**:
- `{conversation_active}`: "yes" if in active conversation, "no" if isolated utterance
- `{conversation_summary}`: 2-3 sentence overview of current discussion
- `{active_topics}`: Comma-separated topics from last 2 turns
- `{entities}`: Key entities (people, places, things) mentioned
- `{recent_turns}`: Last 3 turns with timestamps and topics

**Files to modify**:
- `prompts/decider.txt` (~20 lines): Add conversation context blocks
- `tests/fixtures/decider_prompt_flag_off.golden.txt` (~10 lines): Update fixture

### Phase 6: Configuration

**File**: `src/config.py` (modify, ~15 lines)

Add turn aggregation settings:

```python
@dataclass
class Settings:
    # ... existing fields ...

    # Turn aggregation (NEW)
    turn_aggregation_enabled: bool = True
    focus_mode_turn_timeout: float = 0.5  # Quick replies
    ambient_mode_turn_timeout: float = 3.0  # Patient listening
    max_turn_duration: float = 30.0  # Force submit
    max_turn_buffer_size: int = 50  # Max utterances per turn

    # Conversation memory (NEW)
    conversation_memory_enabled: bool = True
    max_conversation_age_hours: float = 24.0  # Prune old conversations
    topic_extraction_enabled: bool = True
    max_topics_per_turn: int = 5
```

**Config file**: `~/.heare/config.toml`

```toml
[turn_aggregation]
enabled = true
focus_mode_timeout = 0.5  # seconds
ambient_mode_timeout = 3.0  # seconds
max_turn_duration = 30.0
max_turn_buffer_size = 50

[conversation_memory]
enabled = true
max_age_hours = 24.0
topic_extraction = true
max_topics_per_turn = 5
```

**Files to modify**:
- `src/config.py` (~15 lines): Add new settings fields
- `tests/test_config.py` (~10 lines): Test new settings

### Phase 7: Performance Optimization

**Strategy 1: API Call Reduction**

**Baseline Measurement (Before Implementation)**:
Add metrics collection to current system:
```python
# In src/decider.py (current code)
class DeciderProcessor:
    def __init__(self, ...):
        self._decide_calls_count = 0
        self._stt_calls_count = 0  # If accessible

    async def _decide(self, ctx, transcript):
        self._decide_calls_count += 1
        logger.info(f"Decider call #{self._decide_calls_count}")
        # Log to events table
        await self.store.log_event("decider_call", {
            "count": self._decide_calls_count,
            "timestamp": time.time()
        })
        # ... existing logic
```

**Measurement Period**: Run for 24-48 hours with aggregation disabled to establish baseline.

**Expected Results**:
- Groq STT calls: ~150-300/day (unavoidable, each utterance needs transcription)
- Claude decider calls: ~150-300/day (one per utterance)
- Total cost: Baseline for comparison

**With Turn Aggregation**:
- Groq STT calls: ~150-300/day (unchanged - still need to transcribe each utterance)
- Claude decider calls: ~30-60/day (one per turn, ~3-5 utterances/turn)
- Topic extraction calls: ~30-60/day (one per turn)
- Total Claude calls: ~60-120/day (decider + topic extraction)

**Net Reduction**: 3-5x fewer Claude decider API calls (not STT calls - those are unchanged)

**Important Distinction**:
- **Groq STT calls**: Unavoidable, each utterance must be transcribed
- **Claude decider calls**: Reducible via aggregation (primary savings)
- **Claude topic extraction calls**: New cost, but net positive

**Cost Savings Analysis**:
```
Baseline: 200 decider calls/day × $0.003/call = $0.60/day
With aggregation: 50 decider + 40 topic = 90 calls × $0.003 = $0.27/day
Savings: $0.33/day (55% reduction)
```

**Strategy 2: Batching Topic Extraction**

Instead of calling Claude per turn, batch topic extraction:
- Extract topics every N turns (configurable, default 3)
- Or extract when context buffer is full (>500 tokens)

**Strategy 3: Async Conversation Updates**

Run conversation summary updates in background:
- Don't block decision pipeline on summary updates
- Update summary after decision is made

**Files to create**:
- `src/performance.py` (~100 lines): Metrics and optimization helpers

### Phase 8: Testing & Verification

**Unit Tests**:
- `tests/test_turn_aggregator.py` (~150 lines): All timeout scenarios
- `tests/test_conversation.py` (~100 lines): Topic extraction, summary updates
- `tests/test_decider.py` (~80 lines): Turn-based decision flow
- `tests/test_config.py` (~10 lines): New settings validation

**Integration Tests**:
- `tests/integration/test_conversation_flow.py` (~100 lines): End-to-end turn → topic → decision

**Acceptance Criteria (Automated Tests)**:

**Test 1: Focus Mode Quick Reply**
```python
def test_focus_mode_quick_reply():
    """Verify focus mode processes single utterance quickly."""
    # Given: User says "Heare what's the weather" in focus mode
    aggregator = TurnAggregator(mode=Mode.FOCUS, focus_timeout=0.5)

    # When: Utterance added and 0.5s silence detected
    should_submit, text = aggregator.add_utterance("Heare what's the weather", ts=0.0)
    await asyncio.sleep(0.5)
    should_submit, text = aggregator.add_utterance("", ts=0.5)  # Silence

    # Then: Should submit immediately
    assert should_submit == True
    assert text == "Heare what's the weather"
```

**Test 2: Ambient Mode Aggregation**
```python
def test_ambient_mode_aggregation():
    """Verify ambient mode waits for turn completion."""
    # Given: User speaks multi-sentence thought in ambient mode
    aggregator = TurnAggregator(mode=Mode.AMBIENT, ambient_timeout=3.0)

    # When: Multiple utterances with gaps < 3.0s
    aggregator.add_utterance("I was thinking", ts=0.0)
    aggregator.add_utterance("about the project", ts=1.0)  # 1s gap
    aggregator.add_utterance("and I think we should", ts=2.0)  # 1s gap

    # Then: Should NOT submit yet (waiting for 3.0s silence)
    assert aggregator.should_submit() == False

    # When: 3.0s silence detected
    await asyncio.sleep(3.0)
    should_submit, text = aggregator.add_utterance("", ts=5.0)

    # Then: Should submit aggregated text
    assert should_submit == True
    assert "I was thinking about the project and I think we should" in text
```

**Test 3: Conversation Reference**
```python
def test_conversation_reference():
    """Verify AI references previous conversation topics."""
    # Given: Active conversation with "weather discussion" in summary
    conv_id = await store.create_conversation(mode=Mode.AMBIENT)
    await store.update_conversation_summary(
        conv_id,
        summary="User asked about weather forecast for tomorrow",
        entity_map={"location": "Kyiv"}
    )

    # When: User says "What did you say about it?"
    transcript = "What did you say about it?"
    ctx = await conversation.build_context(conv_id)

    # Then: Decider prompt must include conversation context
    assert ctx["conversation_active"] == True
    assert "weather" in ctx["conversation_summary"]
    assert "Kyiv" in ctx["entities"]

    # And: Decider prompt includes conversation_summary field
    decider_prompt = build_decider_prompt(ctx, transcript)
    assert "conversation_summary" in decider_prompt
    assert "weather" in decider_prompt
```

**Test 4: API Call Reduction (Claude Decider Calls)**
```python
def test_api_call_reduction():
    """Verify 3-5x fewer Claude decider calls with aggregation."""
    # Given: Baseline measurement (aggregation disabled)
    baseline_calls = measure_decider_calls_over_24h(aggregation_enabled=False)
    # Assume: 150 decider calls/day (one per utterance)

    # When: Turn aggregation enabled
    with_aggregation_calls = measure_decider_calls_over_24h(aggregation_enabled=True)
    # Expect: 30-50 decider calls/day (one per turn, ~3-5 utterances/turn)

    # Then: 3-5x reduction in decider calls
    reduction_ratio = baseline_calls / with_aggregation_calls
    assert 3.0 <= reduction_ratio <= 5.0
```

**Implementation Note for Test 4**:
Add metrics counter to `src/decider.py`:
```python
class DeciderProcessor:
    def __init__(self, ...):
        self._decide_calls_count = 0

    async def _decide(self, ctx, transcript):
        self._decide_calls_count += 1
        logger.info(f"Decider call #{self._decide_calls_count}")
        # ... existing logic
```

Log to events table:
```python
await store.log_event("decider_call_count", {
    "count": self._decide_calls_count,
    "timestamp": time.time()
})
```

Compare 24h baseline vs 24h with aggregation via SQL:
```sql
SELECT
    DATE(timestamp, 'unixepoch') as date,
    SUM(CASE WHEN name='decider_call' THEN 1 ELSE 0 END) as decider_calls
FROM events
WHERE timestamp >= ? AND timestamp < ?
GROUP BY date;
```

**API Cost Model Acknowledgment**:
- Topic extraction adds ~1 Claude call per turn
- Net reduction = (decider_calls_saved) - (topic_extraction_calls)
- Example: 150 baseline calls → 50 decider calls + 30 topic calls = 80 total calls (1.9x reduction)
- Still significant savings, but not pure 3-5x when including topic extraction

**Test 5: No Regression**
```python
def test_existing_functionality_preserved():
    """Verify existing tests still pass."""
    # Run all existing tests
    result = pytest.main(["tests/"])
    assert result == 0  # All tests pass
```

**Performance Benchmarks (Measurable)**:
- Focus mode latency: Measured from TranscriptionFrame.timestamp to DecisionFrame.timestamp < 1.5s for 95th percentile
- Ambient mode latency: Measured from turn start (first utterance) to DecisionFrame.timestamp < 4.0s for 95th percentile
- Topic extraction: < 500ms per batch (measured via time.time() around Claude call)
- Memory overhead: < 50MB per active conversation (measured via `tracemalloc` or `psutil`)

## Migration Strategy

### Phase 1: Database Migration (Safe, Additive)

```bash
# Add new tables (non-breaking)
sqlite3 ~/.heare/heare.db < migrations/01_add_conversations.sql
```

**Migration Script**: `migrations/01_add_conversations.sql`

```sql
-- Safe migration: adds new tables, doesn't break existing ones
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts REAL NOT NULL,
    end_ts REAL,
    mode TEXT NOT NULL,
    summary TEXT,
    entity_map TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    aggregated_text TEXT NOT NULL,
    utterance_count INTEGER NOT NULL,
    topic_tags TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

-- Add nullable column (safe, doesn't break existing queries)
ALTER TABLE transcripts ADD COLUMN turn_id INTEGER REFERENCES turns(id);
```

### Phase 2: Feature Flags

Gradual rollout using config flags:

```toml
[turn_aggregation]
enabled = false  # Start disabled

[conversation_memory]
enabled = false  # Start disabled
```

Enable incrementally:
1. Enable turn aggregation first (test API reduction)
2. Enable conversation memory next (test context quality)
3. Enable topic extraction last (test conversation quality)

### Phase 3: Monitoring & Rollback

**Metrics to Track**:
- API call count (before/after)
- Average latency (utterance → decision)
- Conversation quality (user feedback)
- Error rate (timeout failures, aggregation bugs)

**API Reduction Verification Methodology**:

**Step 1: Baseline Measurement (1 week)**
```sql
-- Run with aggregation disabled
-- Query daily decider call counts
SELECT
    DATE(timestamp, 'unixepoch') as date,
    COUNT(*) as decider_calls
FROM events
WHERE name = 'decider_call'
  AND timestamp >= ? AND timestamp < ?
GROUP BY date
ORDER BY date;
```

**Step 2: Enable Aggregation (1 week)**
```toml
[turn_aggregation]
enabled = true
```

**Step 3: Compare Results**
```sql
-- Compare periods
WITH baseline AS (
    SELECT COUNT(*) as calls FROM events
    WHERE name = 'decider_call'
      AND timestamp BETWEEN ? AND ?
),
with_aggregation AS (
    SELECT COUNT(*) as calls FROM events
    WHERE name = 'decider_call'
      AND timestamp BETWEEN ? AND ?
)
SELECT
    baseline.calls as baseline_calls,
    with_aggregation.calls as agg_calls,
    (baseline.calls * 1.0 / with_aggregation.calls) as reduction_ratio
FROM baseline, with_aggregation;
```

**Step 4: Verify Topic Extraction Cost**
```sql
-- Count topic extraction calls
SELECT COUNT(*) as topic_calls
FROM events
WHERE name = 'topic_extraction_call'
  AND timestamp BETWEEN ? AND ?;
```

**Step 5: Calculate Net Savings**
```
Net reduction = (baseline_decider_calls) - (agg_decider_calls + topic_calls)
If net_reduction > 0: Success
If net_reduction <= 0: Re-evaluate (topic extraction too expensive?)
```

**Rollback Plan**:
If issues detected:
```toml
[turn_aggregation]
enabled = false  # Disable, fall back to current behavior
```

Existing flow remains unchanged if features disabled.

## Success Metrics

**Quantitative**:
- **Claude decider API reduction**: 3-5x fewer calls (not STT calls - those remain unchanged)
- **Net API reduction**: 1.9-3x fewer total Claude calls (decider savings minus topic extraction costs)
- **Latency**: Focus mode < 1.5s (95th percentile), Ambient mode < 4.0s (95th percentile)
- **Test coverage**: >80% for new code
- **Memory overhead**: <50MB per active conversation

**Qualitative**:
- User feedback: "Conversation feels more natural"
- Context awareness: AI can reference previous topics ("You mentioned X earlier...")
- No regressions: Existing functionality works as before

**Verification**:
- Automated tests pass (including conversation reference test)
- 24h baseline vs 24h with aggregation shows 3-5x decider call reduction
- User reports improved conversation experience

## Risk Mitigation

**Risk 1: Increased Latency in Ambient Mode**

**Honest Tradeoff Analysis**:
- **Current behavior**: Ambient mode processes each utterance in ~1.0s (VAD 0.5s + STT + Decider)
- **With turn aggregation**: Ambient mode waits 3.0s for user to finish thought
- **Net latency increase**: From ~1.0s to ~3.0s-4.0s (3-4x slower)

**Why This Is Acceptable**:
1. **Ambient mode purpose**: Background companionship, not quick queries
   - User expects: "I'm thinking out loud, Heare listens and responds thoughtfully"
   - Not: "Quick answer to a specific question" (that's what focus mode is for)

2. **Quality improvement**: Better conversation understanding outweighs latency
   - Aggregated turn has full context: "I was thinking about X, and Y, and we should Z"
   - Fragmented utterances lose context: "I was thinking" → "about X" → "and Y"

3. **User control**: Configurable timeout (default 3.0s, adjustable to 1.0s-5.0s)
   - Impatient users: Lower to 1.5s
   - Contemplative users: Increase to 5.0s

4. **Focus mode unchanged**: Still quick replies (0.5s wait)
   - Quick queries: Use focus mode

**Mitigation**:
- Configurable timeouts via `config.toml`: `ambient_mode_turn_timeout = 3.0`
- User education: "Focus mode for quick replies, ambient mode for thoughtful conversation"
- Fallback: Disable aggregation for ambient mode if users complain (set `enabled = false`)

**Not A Mitigation**:
- ~~"Disable aggregation for focus mode only"~~ (already doesn't affect focus mode)

**Risk 2: Over-Aggregation (Too Long Turns)**
- Mitigation: 30s max duration prevents monologues
- Monitoring: Track turn duration distribution

**Risk 3: Topic Extraction Cost**
- Mitigation: Batch extraction, async updates
- Fallback: Disable topic extraction, keep turn aggregation

**Risk 4: Database Bloat**
- Mitigation: Auto-prune conversations older than 24h
- Monitoring: Track DB size growth

**Risk 5: Context Window Overflow**
- Mitigation: Limit recent turns to 3, summarize older ones
- Monitoring: Track token count in prompts

## Future Enhancements (Out of Scope)

- Multi-turn intent tracking (e.g., "find X → filter by Y → sort by Z")
- Sentiment analysis (user frustration detection)
- Cross-session memory (remember topics from yesterday)
- Entity resolution (link "he", "she" to actual names)
- Conversation branching (multiple parallel threads)
- Proactive topic initiation (AI starts new topics)
- Voice-emotion-aware timeout adjustments (user seems excited → longer timeout)

## Implementation Order

1. **Phase 1**: Database schema (`src/storage.py`) → 1 day
2. **Phase 2**: Turn aggregator (`src/turn_aggregator.py`) → 2 days
3. **Phase 3**: Conversation manager (`src/conversation.py`) → 2 days
4. **Phase 4**: Decider integration (`src/decider.py`) → 2 days
5. **Phase 5**: Prompt updates (`prompts/decider.txt`) → 0.5 days
6. **Phase 6**: Configuration (`src/config.py`) → 0.5 days
7. **Phase 7**: Testing (`tests/`) → 2 days
8. **Phase 8**: Migration & monitoring → 1 day

**Total Estimated Time**: 11 days

**Critical Path**: Phase 2 → Phase 3 → Phase 4 (turn aggregation → conversation → integration)

**Parallelizable**: Phase 5 (prompts), Phase 6 (config), Phase 7 (tests early)
