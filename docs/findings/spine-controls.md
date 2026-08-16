# Dashboard controls vs. the spine engine

Read-only audit, originally 2026-08-16 (commit `ba5f039`), **re-verified
2026-08-17** against the current tree — the spine engine moves fast
enough that most of the DEAD rows below were fixed within a day. Every
row was traced three times: the frontend call site, the API handler, and
a grep for the written key/file/mechanism across **all** of `src/` — not
just `src/spine/`, so an indirect consumer (agent tool, config loader,
menubar) could not be missed.

`engine = "spine"` (`src/main.py:110-118` → `src/daemon/spine_engine.py`).
The spine bridges four dialects — State, the `voice_state` file, the
inject drop-folder, the DB. Everything the dashboard drives through those
four now works. What's left dead mostly drives a **pipecat stage**
(`src/pipeline/stages/*`), which is no longer built.

## WORKS — with the consuming line

| Control | UI location | API endpoint | What it writes | Consumed by spine? | Verdict |
|---|---|---|---|---|---|
| mic mute | `StatusBar.jsx:39` | `POST /mute` (`api.py:615`) | State `mute_mic` | yes — poller → `audio_io.py:136` discards the frame | **WORKS** |
| bot mute | `StatusBar.jsx:42` | `POST /mute` | State `mute_bot` | yes — poller → `audio_io.py:206` + `stop_playback()` | **WORKS** |
| cancel | `StatusBar.jsx:45` | `POST /cancel` (`api.py:700`) | State `cancel` | yes — control poller: drops playback, sets `loop._interrupted`, `toolbox.cancel_all()` | **WORKS** |
| interrupt on/off | `StatusBar.jsx:46` → `Dashboard.jsx:382` | `POST /interrupt` (`api.py:625`) | State `interrupt_off` | **yes, now** — `src/daemon/spine_engine.py:1113` polls it into `loop.barge_in_enabled`, gated in the barge-in condition itself (`src/spine/loop.py:214`, default `True` at `:122`) | **WORKS** (was DEAD) |
| mic gain | `AudioPanel.jsx:104` | `POST /state` | State `input_gain` | **yes, now** — `src/daemon/spine_engine.py:747` seeds it at boot, `:1128` hot-polls it into `audio.input_gain`; `audio_io.py:290-292` scales every input frame (clamped int16) | **WORKS** (was DEAD) |
| speaker volume | `AudioPanel.jsx:127` | `POST /state` | State `output_volume` | **yes, now** — same poller path into `audio.output_volume`; `audio_io.py:329-350` scales every output frame | **WORKS** (was DEAD) |
| audio device pickers (in/out) | `AudioDevicePicker.jsx:41-42` → `Dashboard.jsx:231` | `POST /api/audio-devices/select` (`api.py:1177`) | `audio_input_device_file` / `audio_output_device_file` | **yes, now, on the next restart** — `src/daemon/spine_engine.py:744-745` resolves the saved device name to a sounddevice index via `_resolve_device()` and passes it into `AudioIO(input_device=…, output_device=…)`; `audio_io.py:237,246` opens the streams on it. Not hot — a stream's device can't change after it's open, so a restart (`POST /daemon`) is still required | **WORKS**, restart to apply |
| memories: list / forget | `MemoriesCard.jsx:25, 42` | `GET /api/memories`, `POST /api/memories/{id}/forget` (`api.py:2144, 2180`) | reads/deletes via `api._memory_backend` | **yes, now** — `src/daemon/spine_engine.py:790` sets `api._memory_backend = loop.memory` (the same `SQLiteBackend` the spine already built), and the three memory routes are present in **both** `API.__init__` (`api.py:276-279`) and `register_routes` (`api.py:364-369`), so the menubar-mounted build has them too | **WORKS** (was DEAD) |
| AGENT status bar | `Dashboard.jsx:438` → `AgentStatusBar.jsx` | reads `GET /state` → `agent_state` | — | **yes, now** — `src/daemon/spine_engine.py:902-995` wraps `loop._speak` to write `agent_state` `talking` on start and `interrupted`/`idle` on finish (`_agent()`, first call at `:914`) | **WORKS** (was DEAD) |
| VOICE bar (speaking + listening) | `Dashboard.jsx:434` → `UserVoiceBar.jsx` | reads `GET /state` → `voice_state` | — | **yes, now, fully** — `_vs()` (`spine_engine.py:893`) writes `stt` → `result` and now also writes `listening` again after every result (`:938`) and after STT errors (`:924`), so the bar returns to "listening" between turns instead of parking on "result". The assistant's own speaking phase is the AGENT bar above, wired the same day | **WORKS** (was PARTIAL) |
| activity feed | `HistoryPanel` ← `GET /activity` (`Dashboard.jsx:108`) | `api.py:514` | reads `transcripts` ∪ `actions` | **yes, now, both halves** — `transcripts` from `src/spine/persist.py`; `actions` (the `'did'` rows) now written too, by `SpineActionLog` (`src/spine/tools.py:163-283`, wired to `Hands` at `:342`), matching the same columns `TranscriptStore` used | **WORKS** (was PARTIAL) |
| chrome badge | `StatusBar.jsx` (`chromeLabel`) | reads `GET /state` → `chrome` / `chrome_enabled` | — | **yes, now honest** — `_bridge_connected()` (`api.py:574-599`) reports connected only when an in-process `BrowserBridge` object exists and `.connected` is true; under spine the bridge is never started (see below), so it correctly shows "not connected" instead of the old always-on "connected" | **WORKS** (was DEAD-but-lying) |
| `starting…` indicator | — (superseded) | `GET /state` → `boot_status` | State `boot_status`, written by `publish_boot_status()` (`spine_engine.py:87`) | **yes, now, but relocated** — the specific `StatusBar.jsx` indicator and the literal State key `starting` from the original audit no longer exist; boot progress moved to a "waiting for keys" banner in `App.jsx:98-160`, fed by `boot_status`, which the spine now publishes at every boot phase (`waiting_for_keys`, `starting`, `stopped`) | **WORKS** (replaced, not fixed in place) |
| inject text | `InjectPanel` → `Dashboard.jsx:186` | `POST /inject` (`api.py:811`) | a `.txt` file in `inject_dir` | yes — poller → `loop.inject()` | **WORKS** |
| start / end role | `RolesCard.jsx:117, 125` | `POST /inject` | same drop-folder | yes — same poller | **WORKS** |
| roles strip (status) | `RolesCard.jsx:54-60` | `GET /state` | — | yes — `roles_available`, `role_active/turns/last_heard/finishing/channel/since/hint` | **WORKS** |
| artifacts list / open | `RolesCard.jsx:76, 141` | `GET /api/artifacts[/{name}]` | reads `workspace/artifacts/*.md` | yes — written by `src/spine/main.py` | **WORKS** |
| daemon start / stop / restart | `StatusBar.jsx:54, 57, 60` | `POST /daemon` (`api.py:729`) | in-process callbacks / SIGTERM | yes — cancels/recreates the task running `run_spine_daemon` | **WORKS** |
| usage card | `UsageCard` ← `GET /state` | `api.py:422` | reads `usage_events` | yes — `src/spine/usage.py` writes llm/stt/tts rows | **WORKS** |
| running / transcript count / last response | `StatusBar.jsx:18-31`, `Dashboard.jsx:427` | `GET /state` | — | yes | **WORKS** |
| identity (name/emoji/regenerate) | `SetupModal.jsx:62, 76` | `POST /api/setup/identity[/regenerate]` | `~/.heare/identity.json` | yes, after restart | **WORKS** |
| panel toggles, canvas close, history tabs | `ControlsCard.jsx:38-68`, `Dashboard.jsx:448` | — | React state only | n/a — pure UI | **WORKS** |
| mic permission check | `App.jsx:20` | `GET /mic/status` | queries sounddevice | engine-independent | **WORKS** |

## PARTIAL

| Control | UI location | API endpoint | What it writes | Consumed by spine? | Verdict |
|---|---|---|---|---|---|
| API key save | `SettingsPanel.jsx:105`, `BrainCard.jsx:54`, `SetupModal` | `POST /settings` | `~/.heare/.env`, State `key_*` | the `.env` write is durable, but `src/spine/llm.py:34-45` still resolves `deepseek_api_key` **once** at `_build_loop` — rotating a key while the daemon is already running still needs a restart. (Separately, a **missing** key at boot is now picked up live within a second, no restart — `spine_engine.py:160-198`, `REQUIRED_KEYS`/`KEY_HINT`. Different case, same key.) | **PARTIAL** |
| language (setup) | `SetupModal.jsx:95` | `POST /api/setup/config` | `groq_language` in `config.toml` | yes, but only after restart — `src/spine/main.py:95` reads it at build time | **PARTIAL** |
| allow installs / reset session | `SettingsPanel.jsx:24, 51` | `POST /api/settings/allow-installs`, `/reset-session` | capability-gate config; backs up `session.json` | reachable from the live engine — `capability_install_enabled` gates install tools inside `Hands` (`src/agent/hands.py:214`), which the spine's `delegate` verb runs — but not the voice model's own path, and the UI still says "restart daemon to apply" | **PARTIAL** |
| canvas / display panel | `DisplayCard` ← `GET /display` | `GET /display` (`api.py:706`) | reads the `displays` table | only indirectly: `show_canvas`/`display` are tools on `Hands` (`src/agent/tools/system.py`), reachable via **delegate**, but `src/spine/tools.py`'s `VoiceToolbox` offers the voice model exactly three verbs (delegate/remember/recall) — nothing in the spine's own path ever draws | **PARTIAL** |
| tools modal | `ControlsCard` → `Dashboard.jsx:256` | `GET /api/tools` | reads the built-in/skill/MCP catalog | display-only, and it still describes the wrong agent: the catalog matches `Hands` (~50 tools), while the voice model sees three schemas (`src/spine/tools.py` `VoiceToolbox.schemas`) | **PARTIAL** |

## DEAD — nothing on the spine path reads it

| Control | UI location | API endpoint | What it writes | Consumed by spine? | Verdict |
|---|---|---|---|---|---|
| provider switch | `BrainCard.jsx:74` | `POST /provider` | State `provider` | no — only `src/agent/llm/switchable.py`. `src/spine/llm.py` resolves DeepSeek from settings, unconditionally, no State read | **DEAD** |
| model switch | `BrainCard.jsx:81` | `POST /model` | State `model_<provider>` | no — same reason | **DEAD** |
| sidetone | `AudioPanel.jsx:26` | `POST /state` | State `sidetone` | no — only `src/pipeline/stages/audio_monitor.py`; nothing in `src/spine/` references it | **DEAD** |
| VAD sensitivity | `AudioPanel.jsx:81` | `POST /state` | State `vad_sensitivity` | no — dead in **both** engines: the only reader anywhere is the pipecat `context_injector.py`, which prints the value into a prompt. Spine's threshold is a constant (`src/spine/vad.py`) | **DEAD** |
| agents: start / cancel / list | `AgentsPanel.jsx:24`, `Dashboard.jsx:244` | `POST /api/agents/start`, `/cancel`, `GET /api/agents` | nothing — needs the in-process `SubAgentManager` | no — `set_agent_manager()` is called only at `src/pipeline/build.py:1131`. Under spine `get_agent_manager()` is `None` → every call 503s | **DEAD** |
| prompts editor (save) | `PromptManager` → `Dashboard.jsx:281` | `POST /api/prompts/{key}` | `prompts/*.txt` on disk | no — the spine composes its system prompt in `src/spine/prompt.py` (hard-coded voice rules + persona from `identity.json`); nothing there reads `prompts/` | **DEAD** |
| TTS voice (setup) | `SetupModal.jsx:95, 113` | `POST /api/setup/config` | `tts_voice` in `config.toml` | no — `spine_engine.py` passes `voice=""`, and the voice is picked from the reply's script instead (`src/spine/voicing.py`) | **DEAD** |
| bridge enable / rotate token | `BridgeModal.jsx:125, 134` | `POST /api/bridge/toggle`, `/rotate-token` | `config.toml` + `~/.heare/browser_bridge.token` | no — `BrowserBridge` is started in `_build_and_run_daemon`, after the spine's early return in `src/main.py`. The bridge process never runs under spine (this is also why the chrome badge above now correctly shows "not connected") | **DEAD** |
| MODE row (removed) / `POST /mode` | — (UI removed) | `POST /mode` | State `mode` | route still mounted, no caller | **DEAD (route only)** |
| chrome launch / profiles | — (no caller) | `POST /api/chrome/launch`, `GET /api/chrome/profiles` | launches Chrome with CDP | no frontend button anywhere | **DEAD (route only)** |

## What to wire next, by user impact

Fixed since the original audit (2026-08-16 → 2026-08-17): the AGENT
status bar and the VOICE bar's speaking/listening phases, interrupt
on/off, audio device pickers (restart-only), memories, mic gain/speaker
volume, the activity feed's action half, and the chrome badge's honesty.
What's left:

1. **Provider / model switch.** Real work (the spine has one provider by
   design), but the card currently claims otherwise. Hook: either
   `src/spine/llm.py:34` (`resolve_llm` reading State) or, cheaper for
   now, hide the selector while `engine = "spine"`.
2. **Agents panel and the prompts editor.** Both need a decision, not a
   wire: the sub-agent manager belongs to a pipeline that no longer runs
   (`src/pipeline/build.py:1131`), and the prompt templates describe a
   prompt the spine does not assemble (`src/spine/prompt.py`). Until
   then they are two panels of empty or ignored UI.
3. **Sidetone and VAD sensitivity.** VAD sensitivity is dead in both
   engines — the honest option is to delete the slider rather than wire
   it. Sidetone would need a real hook in `audio_io.py`, a size larger
   than the gain/volume work already done.
4. **API key rotation while running.** `spine/llm.py` resolves
   `deepseek_api_key` once at boot; a key saved via the dashboard still
   needs a restart to take effect (separate from the boot-time
   missing-key case, which is already live).
