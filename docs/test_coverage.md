# heare Test Coverage by Feature

678 tests across 42 test files organized by feature area.

## 1. Voice Input Pipeline (STT + VAD)

| Feature | Test File | Test Count |
|---------|-----------|------------|
| Groq STT integration | `test_decider.py` | ~20 tests |
| Audio transcoding (MP3 → PCM) | `test_audio.py` | 9 tests |
| Echo cancellation | `test_audio.py` | 2 tests |
| Sample rate handling | `test_audio.py` | 2 tests |
| PCM format validation | `test_audio.py` | 1 test |

**Key tests:**
- `test_transcode_silence_produces_valid_pcm`
- `test_transcode_respects_sample_rate`
- `test_echo_cancellation_full_cycle`
- `test_pcm_samples_are_valid_s16le`

---

## 2. Decision Engine (Core State Machine)

| Feature | Test File | Test Count |
|---------|-----------|------------|
| Yes/No parsing (Ukrainian + English) | `test_decider.py` | ~40 tests |
| Confirmation flow state transitions | `test_decider.py` | ~30 tests |
| Intent extraction from decisions | `test_decider.py` | ~25 tests |
| Uncertain response handling | `test_decider.py` | ~15 tests |
| Silent timeout on confirmation | `test_silent_timeout.py` | 8 tests |
| Mode-aware behavior (silent/focus/ambient) | `test_decider.py` | ~20 tests |

**Key tests:**
- `test_yes_variants[так]`, `test_yes_variants[ага]`, `test_yes_variants[yes]`
- `test_no_variants[ні]`, `test_no_variants[nevermind]`, `test_no_variants[cancel]`
- `test_unclear[можливо]`, `test_unclear[maybe later actually]`
- `test_yes_vocative[гава так]` — vocative case handling
- `test_state_machine_transitions_*` (multiple state paths)

---

## 3. Voice Output (TTS)

| Feature | Test File | Test Count |
|---------|-----------|------------|
| Edge TTS streaming | `test_tts_streaming.py` | 14 tests |
| Edge TTS client | `test_edge_tts.py` | 9 tests |
| Edge TTS error handling | `test_edge_tts_errors.py` | 8 tests |
| TTS cache | `test_tts_cache.py` | 8 tests |
| Audio frame chunking (100ms) | `test_audio.py` | 3 tests |
| Speaker selection | `test_speaker_gallery.py` | 30 tests |
| Speaker processor | `test_speaker_processor.py` | 80 tests |
| Speaker ID (voice recognition) | `test_speaker_id.py` | 9 tests |

**Key tests:**
- `test_tts_streaming_*` — streaming chunks, reconnects
- `test_edge_tts_general_errors_*` — transient error handling
- `test_speaker_gallery_*` — voice selection by name/gender
- `test_speaker_id_recognizes_stranger_vs_known`

---

## 4. Action System (Intents + Execution)

| Feature | Test File | Test Count |
|---------|-----------|------------|
| Intent queue (FIFO, cancel, limits) | `test_actions.py` | 18 tests |
| Intent parsing from stream | `test_intent_parser.py` | 15 tests |
| Direct tool execution (fast path) | `test_direct_tools.py` | 20 tests |
| Action routing (simple vs complex) | `test_actions_routing.py` | 9 tests |
| Tool allowlist enforcement | `test_actions.py` | 6 tests |
| MCP tool routing | `test_actions_routing.py` | 2 tests |
| **Workflows** (NEW) | `test_workflow.py` | 10 tests |
| End-to-end intent flow | `integration/test_intent_flow.py` | 8 tests |

**Supported tools (with tests):**
- `bash` — shell commands
- `read` — file reading
- `write` — file creation
- `edit` — file editing (via Claude)
- `web_fetch` — URL fetching
- `web_search` — web search (Brave API)
- `workflow` — saved action sequences
- `mcp__*` — MCP server tools

**Key tests:**
- `test_queue_fifo_order`
- `test_cancel_latest_removes_newest`
- `test_intent_parser_*` — `<intent>` tag extraction
- `test_simple_tool_routes_to_direct`
- `test_workflow_execute`

---

## 5. Claude Backend Integration

| Feature | Test File | Test Count |
|---------|-----------|------------|
| Claude CLI subprocess calls | `test_claude_cli.py` | 30 tests |
| Agent SDK backend | `test_agent_sdk_cli.py` | 28 tests |
| OpenRouter integration | `test_openrouter_cli.py` | 9 tests |
| Session persistence | `test_agent_sdk_cli.py` | 4 tests |
| Rate limiting | `test_rate_limit.py` | 6 tests |
| Stale session reconnection | `test_agent_sdk_cli.py` | 2 tests |
| Tool allowlist propagation | `test_agent_sdk_cli.py` | 5 tests |

**Key tests:**
- `test_call_decider_strips_markdown_fence`
- `test_sdk_call_action_streams_each_text_block`
- `test_sdk_allowed_tools_uses_settings_list`
- `test_sdk_stale_session_reconnects_and_retries`

---

## 6. MCP (Model Context Protocol) Servers

| Feature | Test File | Test Count |
|---------|-----------|------------|
| MCP server loading | `test_mcp.py` | 12 tests |
| MCP tool expansion (wildcards) | `test_agent_sdk_cli.py` | 3 tests |
| MCP tool routing | `test_actions_routing.py` | 2 tests |

**Supported MCP servers (config):**
- chrome-devtools — browser automation
- filesystem — file operations
- github — GitHub issues/PRs
- (any stdio MCP server)

---

## 7. Conversation Memory & Context

| Feature | Test File | Test Count |
|---------|-----------|------------|
| Turn aggregation | `test_turn_aggregator.py` | 18 tests |
| Conversation manager | `test_conversation.py` | 22 tests |
| Conversation memory (Phase 2) | `test_conversation_memory_phase2.py` | 5 tests |
| Context building | `test_context.py` | 26 tests |
| Active topics tracking | `test_conversation.py` | 8 tests |
| Entity extraction | `test_conversation.py` | 5 tests |

**Key tests:**
- `test_turn_aggregator_*`
- `test_conversation_manager_*`
- `test_context_includes_recent_transcripts`
- `test_context_projects_active_topics`

---

## 8. Generator (LLM Streaming Response)

| Feature | Test File | Test Count |
|---------|-----------|------------|
| OpenRouter streaming | `test_generator.py` | 35 tests |
| Prompt template rendering | `test_generator_prompt.py` | 14 tests |
| Intent emission in stream | `test_generator.py` | 8 tests |
| Persona injection | `test_generator_prompt.py` | 4 tests |
| MCP server listing in prompt | `test_generator_prompt.py` | 3 tests |
| Pipeline integration | `integration/test_s2s_pipeline.py` | 1 test |

**Key tests:**
- `test_streaming_response_yields_frames`
- `test_intent_emitted_in_stream`
- `test_prompt_includes_persona`
- `test_prompt_lists_mcp_servers`

---

## 9. Storage & Persistence

| Feature | Test File | Test Count |
|---------|-----------|------------|
| SQLite operations | `test_storage.py` | 35 tests |
| Schema migrations | `test_migration.py` | 14 tests |
| Session persistence | `test_storage.py` | 8 tests |
| Identity (persona) storage | `test_identity.py` | 7 tests |
| Transcript storage | `test_storage.py` | 6 tests |
| Decision logging | `test_storage.py` | 5 tests |
| Action logging | `test_storage.py` | 4 tests |

**Key tests:**
- `test_store_transcript_*`
- `test_store_decision_*`
- `test_store_action_outcome_*`
- `test_migration_*`

---

## 10. Configuration & CLI

| Feature | Test File | Test Count |
|---------|-----------|------------|
| Settings loading | `test_config.py` | 18 tests |
| Environment variable overrides | `test_config.py` | 8 tests |
| CLI commands (start/stop/status) | `test_main_cli.py` | 16 tests |
| Main entry point | `test_main.py` | 10 tests |
| Feature flags | `test_feature_flags.py` | 15 tests |
| Mode hot-reload | `test_mode_hot_reload.py` | 7 tests |

**Supported modes:**
- `silent` — never speak/act
- `focus` — speak only when addressed
- `ambient` — proactive speaking

**Key tests:**
- `test_settings_from_toml_file`
- `test_env_var_overrides_toml`
- `test_mode_hot_reload_*`

---

## 11. Pipeline & Watch

| Feature | Test File | Test Count |
|---------|-----------|------------|
| Pipecat pipeline building | `test_pipeline.py` | 12 tests |
| File watching | `test_watch.py` | 22 tests |
| Daemon lifecycle | `test_main.py` | 5 tests |
| Graceful shutdown | `test_shutdown.py` | 8 tests |
| Startup warmup | `test_warmup.py` | 8 tests |

**Key tests:**
- `test_build_pipeline_returns_generator_processor`
- `test_watch_detects_file_changes`
- `test_shutdown_sigterm_*`

---

## 12. Logging & Reliability

| Feature | Test File | Test Count |
|---------|-----------|------------|
| Log rotation | `test_log_rotation.py` | 6 tests |
| Stranger detection | `test_stranger_integration.py` | 13 tests |
| Error recovery | `test_edge_tts_errors.py` | 8 tests |
| Timeout handling | `test_silent_timeout.py` | 8 tests |
| Rate limiting | `test_rate_limit.py` | 6 tests |

---

## 13. Integration Tests (End-to-End)

| Test File | Coverage |
|-----------|----------|
| `integration/test_intent_flow.py` | 8 tests — full intent lifecycle |
| `integration/test_conversation_flow.py` | 7 tests — turn aggregation + conversation |
| `integration/test_s2s_pipeline.py` | 1 test — streaming pipeline |

---

## Test Count by Feature Area

| Feature Area | Approx. Tests |
|--------------|---------------|
| Decision Engine (yes/no, state machine) | ~120 |
| Speaker/Voice (TTS, voices, recognition) | ~130 |
| Action System (intents, tools, workflows) | ~90 |
| Claude Backend (CLI, SDK, OpenRouter) | ~70 |
| Conversation Memory & Context | ~60 |
| Generator (LLM streaming, prompts) | ~55 |
| Audio (STT, transcoding, echo) | ~35 |
| Storage & Persistence | ~50 |
| Configuration & CLI | ~45 |
| Pipeline & Watch | ~30 |
| Integration (E2E) | ~16 |
| **Total** | **678** |

---

## Coverage Quality

- ✅ All major features have unit tests
- ✅ Critical paths have integration tests
- ✅ Error cases covered (timeouts, failures, edge cases)
- ✅ Ukrainian language parsing thoroughly tested
- ✅ Multi-language support (Ukrainian + English) verified
- ⚠️ Some hardware-dependent tests auto-skipped without pipecat

---

## Unused/Old Tests?

None identified — all tests map to current modules. `test_migration.py` validates schema but could be one-use; still valuable for verification.
