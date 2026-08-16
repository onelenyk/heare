# Dashboard controls vs. the spine engine

Read-only audit, 2026-08-16, `engine = "spine"` (`src/main.py:110-118` →
`src/daemon/spine_engine.py`). Every row was traced three times: the
frontend call site, the API handler, and a grep for the written
key/file/mechanism across **all** of `src/` — not just `src/spine/`, so
an indirect consumer (agent tool, config loader, menubar) could not be
missed.

The shape of the result: the spine bridges four dialects — State, the
`voice_state` file, the inject drop-folder, the DB. Everything the
dashboard drives through those four works. Everything the dashboard
drives through a **pipecat stage** (`src/pipeline/stages/*`) is now
decoration, because no pipecat stage is built any more.

Line numbers as of commit `ba5f039`.

## DEAD — nothing on the spine path reads it

| Control | UI location | API endpoint | What it writes | Consumed by spine? | Verdict |
|---|---|---|---|---|---|
| interrupt on/off | `StatusBar.jsx:46` → `Dashboard.jsx:382` | `POST /interrupt` (`api.py:625`) | `~/.heare/interrupt_enabled.flag` + State `interrupt_off` (`api.py:636-640`) | no — only `src/pipeline/stages/interrupt_toggle_gate.py:50`. Spine barge-in is gated solely on a live AEC (`src/spine/loop.py:117-118, 158`) | **DEAD** |
| provider switch | `BrainCard.jsx:74` | `POST /provider` (`api.py:643`) | State `provider` (`api.py:650`) | no — only `src/agent/llm/switchable.py:296`. `src/spine/llm.py:34-45` resolves DeepSeek from settings, unconditionally | **DEAD** |
| model switch | `BrainCard.jsx:81` | `POST /model` (`api.py:653`) | State `model_<provider>` (`api.py:661`) | no — only `switchable.py:328` | **DEAD** |
| mic gain | `AudioPanel.jsx:104` | `POST /state` (`api.py:494`) | State `input_gain` | no — only `src/pipeline/stages/gain_control.py:44`. `AudioIO` has no gain stage at all (`src/spine/audio_io.py`) | **DEAD** |
| speaker volume | `AudioPanel.jsx:127` | `POST /state` | State `output_volume` | no — only `gain_control.py:86` | **DEAD** |
| sidetone | `AudioPanel.jsx:26` | `POST /state` | State `sidetone` | no — only `src/pipeline/stages/audio_monitor.py:84` | **DEAD** |
| VAD sensitivity | `AudioPanel.jsx:81` | `POST /state` | State `vad_sensitivity` | no — and dead in **both** engines: the only reader anywhere is `src/agent/llm/context_injector.py:337`, which prints the value into a prompt. Spine's threshold is a constant (`src/spine/vad.py:32`) | **DEAD** |
| audio device pickers (in/out) | `AudioDevicePicker.jsx:41-42` → `Dashboard.jsx:231` | `POST /api/audio-devices/select` (`api.py:1177`) | `audio_input_device_file` / `audio_output_device_file` (`api.py:1191-1200`) | no — read by `src/config.py:795-803` and hot-reloaded by `src/main.py:424-451` → `src/pipeline/build.py`, all pipecat. `AudioIO.start()` opens the **default** streams with no `device=` (`src/spine/audio_io.py:84-97`), so even a restart does not apply the choice | **DEAD** |
| agents: start / cancel / list | `AgentsPanel.jsx:24`, `Dashboard.jsx:244`, poll `Dashboard.jsx:128` | `POST /api/agents/start`, `/cancel`, `GET /api/agents` (`api.py:1626-1698`) | nothing — needs the in-process `SubAgentManager` | no — `set_agent_manager()` is called only at `src/pipeline/build.py:1131`. Under spine `get_agent_manager()` is `None` → every call 503s, the panel is permanently empty | **DEAD** |
| memories: list / forget | `MemoriesCard.jsx:25, 42` | `GET /api/memories`, `POST /api/memories/{id}/forget` (`api.py:1810, 1846`) | nothing — needs `api._memory_backend` | no — assigned only at `src/main.py:226`, which is **after** the spine early-return at `src/main.py:118`. Always `None` → `{"memories": []}`. Worse under the menubar: the three memory routes exist in `API.__init__` (`api.py:163-167`) but are missing from `register_routes` (`api.py:182-242`), which is what the menubar mounts (`src/menubar.py:168`) → 404 | **DEAD** |
| prompts editor (save) | `PromptManager` → `Dashboard.jsx:281` | `POST /api/prompts/{key}` (`api.py:1533`) | `prompts/*.txt` on disk | no — the spine composes its system prompt in `src/spine/prompt.py` (hard-coded voice rules + persona from `identity.json` via `load_persona`). `PROMPT_SECTIONS` is read only by `src/agent/llm/prompt_sections.py` / `context_injector.py`, both pipecat. The delegate worker has its own prompt in `src/agent/hands.py:60-80` | **DEAD** |
| TTS voice (setup) | `SetupModal.jsx:95, 113` | `POST /api/setup/config` | `tts_voice` in `config.toml` | no — `spine_engine.py:47` passes `voice=""`, and the voice is then picked from the reply's script (`src/spine/main.py:109`, `src/spine/voicing.py`) | **DEAD** |
| bridge enable / rotate token | `BridgeModal.jsx:125, 134` → `Dashboard.jsx:341, 356` | `POST /api/bridge/toggle`, `/rotate-token` (`api.py:1304, 1280`) | `config.toml` + `~/.heare/browser_bridge.token` | no — `BrowserBridge` is started at `src/main.py:282-292`, after the spine return at `:118`. The bridge process never runs under spine | **DEAD** |
| AGENT status bar | `Dashboard.jsx:438` → `AgentStatusBar.jsx` | (reads `GET /state`) | — | no writer: State `agent_state` is written only by `src/pipeline/stages/agent_state_observer.py:50-70` and by `src/menubar.py:235` on stop. Permanently `○ idle`, even mid-sentence | **DEAD** |
| `starting…` indicator | `StatusBar.jsx:53, 62` | — | — | State key `starting` has **no writer anywhere in `src/`** | **DEAD** |
| MODE row (removed) / `POST /mode` | — (UI removed) | `POST /mode` (`api.py:605`) | State `mode` | route still mounted, no caller | **DEAD (route only)** |
| chrome launch / profiles | — (no caller) | `POST /api/chrome/launch`, `GET /api/chrome/profiles` (`api.py:1058, 1149`) | launches Chrome with CDP | no frontend button anywhere (`grep -r chrome src/frontend/src` → two cosmetic hits) | **DEAD (route only)** |

## PARTIAL

| Control | UI location | API endpoint | What it writes | Consumed by spine? | Verdict |
|---|---|---|---|---|---|
| API key save | `SettingsPanel.jsx:105`, `BrainCard.jsx:54`, `SetupModal` | `POST /settings` (`api.py:851` → `_apply_api_keys:326`) | `~/.heare/.env`, `os.environ`, `self.config`, State `key_<attr>` | partly — the `.env` write is durable, but the "takes effect without a restart" path is State `key_*` → `switchable.py`, pipecat only. `src/spine/llm.py:34-45` resolves the key **once** at `_build_loop`. **Restart required** | **PARTIAL** |
| language (setup) | `SetupModal.jsx:95` | `POST /api/setup/config` | `groq_language` in `config.toml` | yes, but only after restart — `src/spine/main.py:85-91` reads it at build time for the Whisper hint. (The `tts_voice` half of the same form is dead, above) | **PARTIAL** |
| allow installs / reset session | `SettingsPanel.jsx:24, 51` | `POST /api/settings/allow-installs`, `/reset-session` (`api.py:875, 893`) | capability-gate config; backs up `session.json` | not the voice path at all — these govern the delegate worker (`src/agent/hands.py`), and the UI itself says "restart daemon to apply" | **PARTIAL** |
| canvas / display panel | `DisplayCard` ← `GET /display` (`Dashboard.jsx:109`) | `GET /display` (`api.py:706`) | reads the `displays` table | only indirectly: `show_canvas` is written by `_execute_show_display` (`src/agent/tools/direct.py`), reachable from **Hands** but not from the voice model — `src/spine/tools.py` offers exactly three verbs (delegate/remember/recall). Nothing in the spine's own path ever draws | **PARTIAL** |
| activity feed | `HistoryPanel` ← `GET /activity` (`Dashboard.jsx:108`) | `api.py:514` | reads `transcripts` ∪ `actions` | half: `transcripts` are written by `src/spine/persist.py:121, 134`; the `actions` half (`'did'` rows) has no writer in the spine path — only `src/store/conversation.py:245`, pipecat. The feed shows what was said, never what was done | **PARTIAL** |
| VOICE bar | `Dashboard.jsx:434` → `UserVoiceBar.jsx` | (reads `GET /state`) | State `voice_state` + `voice_state` file | thin: `spine_engine.py:56-77` emits only `stt` and `result` per turn, plus `listening` once at boot (`:197`) and on STT error (`:72`). There is no `listening` transition between turns and no speaking phase, so the bar reads `stt → result → (auto-decay) idle` and never returns to `listening` | **PARTIAL** |
| tools modal | `ControlsCard` → `Dashboard.jsx:256` | `GET /api/tools` (`api.py:1101`) | reads the built-in/skill/MCP catalog | display-only, and it describes the wrong agent: the catalog matches Hands (`src/agent/hands.py:197-210`), while the voice model sees three schemas (`src/spine/tools.py:34-101`). Nothing is wrong with the list; the framing implies the voice agent has sixty tools | **PARTIAL** |

## WORKS — with the consuming line

| Control | UI location | API endpoint | What it writes | Consumed by spine? | Verdict |
|---|---|---|---|---|---|
| mic mute | `StatusBar.jsx:39` | `POST /mute` (`api.py:615`) | State `mute_mic` | yes — `spine_engine.py:147, 151` → `audio_io.py:136` discards the frame | **WORKS** (Phase E fix) |
| bot mute | `StatusBar.jsx:42` | `POST /mute` | State `mute_bot` | yes — `spine_engine.py:148, 152-154` → `audio_io.py:206` + `stop_playback()` | **WORKS** (Phase E fix) |
| cancel | `StatusBar.jsx:45` | `POST /cancel` (`api.py:700`) | State `cancel` | yes — `spine_engine.py:156-162`: drops playback, sets `loop._interrupted`, `toolbox.cancel_all()` | **WORKS** (Phase E fix) |
| inject text | `InjectPanel` → `Dashboard.jsx:186` | `POST /inject` (`api.py:811`) | a `.txt` file in `inject_dir` | yes — `spine_engine.py:168-181` → `loop.inject()` | **WORKS** |
| start / end role | `RolesCard.jsx:117, 125` | `POST /inject` | same drop-folder (trigger phrase, `«закінчили»`) | yes — same poller; role state read back from `spine_engine.py:104-136` | **WORKS** |
| roles strip (status) | `RolesCard.jsx:54-60` | `GET /state` | — | yes — `spine_engine.py:89-91` (`roles_available`) and `:121-134` (`role_active/turns/last_heard/finishing/channel/since/hint`) | **WORKS** |
| artifacts list / open | `RolesCard.jsx:76, 141` | `GET /api/artifacts[/{name}]` (`api.py:1858, 1877`) | reads `workspace/artifacts/*.md` | yes — written by `src/spine/main.py:195-197` | **WORKS** |
| daemon start / stop / restart | `StatusBar.jsx:54, 57, 60` | `POST /daemon` (`api.py:729`) | in-process callbacks (menubar, `src/menubar.py:160-166`) or SIGTERM/`start_daemon` (CLI) | yes — cancels/recreates the task that runs `run_spine_daemon`; the spine's `finally` (`spine_engine.py:213-226`) tears audio down | **WORKS** |
| usage card | `UsageCard` ← `GET /state` | `api.py:422` → `fetch_usage` | reads `usage_events` | yes — `src/spine/usage.py:91, 138, 185` writes llm/stt/tts rows | **WORKS** |
| running / transcript count / last response | `StatusBar.jsx:18-31`, `Dashboard.jsx:427` | `GET /state` | — | yes — `running` at `spine_engine.py:196`; counts and `last_response` from the transcripts the spine writes | **WORKS** |
| identity (name/emoji/regenerate) | `SetupModal.jsx:62, 76` | `POST /api/setup/identity[/regenerate]` | `~/.heare/identity.json` | yes, after restart — `src/spine/prompt.py:load_persona` and the wake phrases (`src/spine/main.py:43-52`) read that file | **WORKS** |
| panel toggles, canvas close, history tabs | `ControlsCard.jsx:38-68`, `Dashboard.jsx:448` | — | React state only | n/a — pure UI | **WORKS** |
| mic permission check | `App.jsx:20` | `GET /mic/status` (`api.py:963`) | queries sounddevice | engine-independent | **WORKS** |

## What to wire next, by user impact

1. **AGENT status bar and the speaking phase of the VOICE bar.** The
   dashboard cannot show that the assistant is talking — the single most
   visible "is this thing alive" signal, and the cheapest to restore.
   Hook: `src/daemon/spine_engine.py:77` (after `loop.transcribe` is
   wrapped) — wrap `loop.synthesise`/playback the same way `_vs` wraps
   STT, writing `agent_state` `talking`/`idle` and `voice_state`
   `listening` when a turn ends.
2. **Interrupt on/off.** A switch labelled "interrupt: on" that cannot
   turn interruption off is worse than no switch. Hook:
   `src/spine/loop.py:158` — add the flag to the `self.audio.playing and
   self._duplex` condition, polled into the loop at
   `src/daemon/spine_engine.py:147` beside `mute_mic`/`mute_bot`.
3. **Audio device pickers.** Selecting a headset does nothing, silently.
   Hook: `src/spine/audio_io.py:84-97` — pass `device=` to both
   `RawInputStream`/`RawOutputStream` from
   `settings.audio_input_device` / `audio_output_device` (already
   resolved by `src/config.py:795-803`); restart-only is acceptable as a
   first step.
4. **Memories panel.** Two independent breaks — an unset backend and, in
   the menubar build, missing routes. Hooks: `src/api.py:238` (add the
   three `/api/memories*` routes to `register_routes` so they match
   `__init__`) and `src/daemon/spine_engine.py:40` (set
   `api._memory_backend` to the `SQLiteBackend` the spine already builds
   in `src/spine/main.py`, alongside `api.state = state`).
5. **Mic gain / speaker volume.** Two sliders, no effect. Hook:
   `src/spine/audio_io.py:136` (input) and `:206` (output) — scale the
   int16 frame by a factor the control poller refreshes at
   `spine_engine.py:147`. Sidetone and VAD sensitivity are a size larger
   and, given VAD sensitivity is dead in both engines, the honest option
   for that one is to delete the slider.
6. **Provider / model switch.** Real work (the spine has one provider by
   design), but the card currently claims otherwise. Hook: either
   `src/spine/llm.py:34` (`resolve_llm` reading State) or, cheaper for
   now, hide the selector while `engine = "spine"`.
7. **Agents panel and the prompts editor.** Both need a decision, not a
   wire: the sub-agent manager belongs to a pipeline that no longer runs
   (`src/pipeline/build.py:1131`), and the prompt templates describe a
   prompt the spine does not assemble (`src/spine/prompt.py`). Until
   then they are three panels of empty or ignored UI.

One more, not a control: `GET /state` reports `chrome: connected`
whenever the bridge is enabled in config and a token file exists
(`src/api.py:388`) — under the spine the bridge process is never
started, so that badge is always wrong when enabled.
