"""Settings and shared enums.

Mutable runtime state (current mode) lives in ~/.heare/mode so it can be
hot-reloaded without restarting the daemon.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


# Import tool registry defaults (lazy import to avoid circular dependency)
def _get_default_sdk_tools():
    from src.agent.tools.registry import DEFAULT_SDK_ALLOWED_TOOLS

    return DEFAULT_SDK_ALLOWED_TOOLS


logger = logging.getLogger("heare.config")


class Mode(str, Enum):
    SILENT = "silent"
    FOCUS = "focus"
    AMBIENT = "ambient"
    ASSISTANT = "assistant"
    MEETING = "meeting"


class DeciderState(str, Enum):
    LISTENING = "listening"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"


HEARE_HOME = Path.home() / ".heare"


_QUIET_HOUR_RE = None  # initialized lazily


def _parse_quiet_hours(raw: list[str]) -> list[tuple[str, str]]:
    """Validate ['HH:MM-HH:MM', ...] entries; drop invalid ones with a warning.

    Returns parsed (start, end) tuples preserving original string form for
    later interpretation by the indication facade.
    """
    global _QUIET_HOUR_RE
    if _QUIET_HOUR_RE is None:
        import re

        _QUIET_HOUR_RE = re.compile(
            r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$"
        )
    out: list[tuple[str, str]] = []
    for entry in raw:
        if not isinstance(entry, str) or _QUIET_HOUR_RE.match(entry) is None:
            logger.warning("indication.quiet_hours: dropping invalid entry %r", entry)
            continue
        start, end = entry.split("-", 1)
        out.append((start, end))
    return out


@dataclass
class IndicationKindToggle:
    sound: bool = True
    visual: bool = True
    notification: bool = True


_LEVEL_DEFAULTS: dict[str, tuple[bool, bool, bool]] = {
    # (sound, visual, notification)
    "attention": (True, True, True),
    "error": (True, True, True),
    "long_running": (True, True, False),
    "success": (True, True, False),
    "info": (False, True, False),
    "input_waiting": (True, True, True),
    "countdown": (True, True, False),
}


def _default_kind_toggles() -> dict[str, IndicationKindToggle]:
    return {
        level: IndicationKindToggle(sound=s, visual=v, notification=n)
        for level, (s, v, n) in _LEVEL_DEFAULTS.items()
    }


@dataclass
class IndicationSettings:
    enabled: bool = True
    sound_enabled: bool = True
    visual_enabled: bool = True
    notification_center_enabled: bool = True
    cooldown_seconds: float = 1.5
    quiet_hours: list[tuple[str, str]] = field(
        default_factory=lambda: [("22:00", "07:00")]
    )
    kinds: dict[str, IndicationKindToggle] = field(
        default_factory=_default_kind_toggles
    )


def _load_indication_settings(raw: dict) -> IndicationSettings:
    s = IndicationSettings()
    if not isinstance(raw, dict):
        return s
    for key in (
        "enabled",
        "sound_enabled",
        "visual_enabled",
        "notification_center_enabled",
    ):
        if key in raw and isinstance(raw[key], bool):
            setattr(s, key, raw[key])
    if "cooldown_seconds" in raw:
        try:
            s.cooldown_seconds = max(0.0, float(raw["cooldown_seconds"]))
        except (TypeError, ValueError):
            logger.warning(
                "indication.cooldown_seconds: invalid value %r — keeping default",
                raw["cooldown_seconds"],
            )
    if "quiet_hours" in raw:
        if isinstance(raw["quiet_hours"], list):
            s.quiet_hours = _parse_quiet_hours(raw["quiet_hours"])
        else:
            logger.warning("indication.quiet_hours must be a list — keeping default")
    kinds_raw = raw.get("kinds")
    if isinstance(kinds_raw, dict):
        for level, defaults in _LEVEL_DEFAULTS.items():
            sub = kinds_raw.get(level)
            if not isinstance(sub, dict):
                continue
            tog = s.kinds[level]
            for attr in ("sound", "visual", "notification"):
                if attr in sub and isinstance(sub[attr], bool):
                    setattr(tog, attr, sub[attr])
    return s


def _load_browser_bridge_settings(settings: "Settings", raw: dict) -> None:
    """Apply [browser_bridge] table values onto Settings, ignoring unknown keys."""
    if not isinstance(raw, dict):
        return
    if "enabled" in raw and isinstance(raw["enabled"], bool):
        settings.browser_bridge_enabled = raw["enabled"]
    if "port" in raw:
        try:
            settings.browser_bridge_port = int(raw["port"])
        except (TypeError, ValueError):
            logger.warning(
                "browser_bridge.port: invalid value %r — keeping default",
                raw["port"],
            )
    if "token" in raw and isinstance(raw["token"], str):
        settings.browser_bridge_token = raw["token"]


@dataclass
class Settings:
    mode: Mode = Mode.AMBIENT
    tts_voice: str = "en-US-AriaNeural"
    tts_sample_rate: int = 24000
    confirmation_timeout_seconds: int = 30
    transcript_retention_days: int = 30
    min_action_confidence: float = 0.8
    bot_speaking_cooldown_seconds: float = 1.0
    # Open-mic barge-in: while the bot is speaking, a genuine human
    # interruption should stop it. Without headphones the bot's own
    # audio echoes back through the mic, so we only treat heard speech
    # as a barge-in when it does NOT closely match what the bot is
    # currently saying (echo suppression) and is long enough to not be
    # a noise fragment. Disable to restore the old "drop everything
    # while bot speaks" behaviour.
    barge_in_enabled: bool = True
    barge_in_min_chars: int = 4
    barge_in_echo_ratio: float = 0.5
    # Acoustic echo gate: cross-correlates mic input against recent bot
    # output audio and drops mic frames whole when correlation exceeds the
    # threshold.
    #
    # OFF by default. Measured on built-in speakers, correlation runs
    # 0.55-0.78 against this 0.15 threshold, so it dropped 100% of frames
    # for as long as the bot spoke — a microphone mute, not a filter, and
    # it made barge-in impossible. With AEC3 actually cancelling (see
    # below) there is nothing left for it to do. Enable only if echo still
    # reaches STT on unusual hardware.
    echo_gate_enabled: bool = False
    echo_gate_threshold: float = 0.15
    echo_gate_buffer_seconds: float = 1.0
    echo_gate_cooldown_seconds: float = 0.3
    echo_gate_peak_decay: float = 0.85
    echo_gate_peak_threshold: float = 0.42

    # WebRTC AEC3 acoustic echo cancellation. Runs on every mic frame, in
    # 10 ms blocks, with a reference tapped after the output transport.
    # See docs/findings/echo-cancellation.md for the four bugs this
    # replaces and the measurements.
    aec_enabled: bool = True
    aec_cooldown_seconds: float = 0.5
    # Speaker-to-microphone delay. Measured ~125 ms on a MacBook Pro with
    # built-in speakers; the previous 30 ms was a guess and capped
    # suppression at a noisy 10-20 dB. Run with aec_measure_delay=true to
    # measure it on other hardware.
    aec_stream_delay_ms: int = 120
    aec_noise_suppression: bool = True
    # Logs measured-vs-configured delay, suppression in dB, and the depth
    # of the reference queue once a second while the bot speaks. These
    # three numbers are what turned "echo behaves oddly" into four
    # located bugs; turn them on before guessing.
    aec_measure_delay: bool = False
    # Peak microphone level before and after the canceller, split by
    # whether the bot was speaking. A stage that outputs -120 dB from a
    # -28 dB input is not filtering, it is deleting — and that reads as
    # perfect echo suppression to the ear.
    audio_probe_enabled: bool = False

    # Whisper is generative: handed silence it returns the likeliest text,
    # which on this setup is "Дякую." Each one becomes a full turn — model
    # call, synthesis, utterance — and the user's real questions queue up
    # behind them, so the assistant seems slow while it answers a room.
    # Observed: eight in ninety seconds. See
    # src/pipeline/stages/speech_energy_gate.py.
    stt_min_rms: float = 180.0
    stt_min_speech_seconds: float = 0.30

    # Voice/hands split. When on, the conversational model sees three
    # verbs — delegate, remember, recall — and everything else runs in
    # src/agent/hands.py: same model, same key, no deadline, off the
    # speaking path. Measured on this machine, a turn that calls a tool
    # inline takes 3822 ms to first audio against 1351 ms without one,
    # and whether anything is said before the work begins is otherwise
    # left to the model's whim. Set false to put all 63 tools back in
    # front of the conversational model.

    # OFF by default. This awaited a DeepSeek call inside process_frame,
    # guarded by "the bot is speaking" — so it fired only while the user
    # was interrupting, holding their words for up to
    # deepseek_timeout_seconds. The one path that must never wait was the
    # only one that did. Echo is already removed by AEC3 before STT.
    echo_classifier_enabled: bool = False
    warmup_interval_seconds: float = 240.0
    workspace_dir: Path = field(default_factory=lambda: HEARE_HOME / "workspace")
    session_file: Path = field(default_factory=lambda: HEARE_HOME / "session.json")
    identity_file: Path = field(default_factory=lambda: HEARE_HOME / "identity.json")
    db_path: Path = field(default_factory=lambda: HEARE_HOME / "heare.db")
    log_dir: Path = field(default_factory=lambda: HEARE_HOME / "logs")
    mode_file: Path = field(default_factory=lambda: HEARE_HOME / "mode")
    pid_file: Path = field(default_factory=lambda: HEARE_HOME / "heare.pid")
    capabilities_file: Path = field(
        default_factory=lambda: HEARE_HOME / "capabilities.json"
    )
    capabilities_max_age_hours: float = 24.0
    mute_file: Path = field(default_factory=lambda: HEARE_HOME / "mute.flag")
    mute_input_file: Path = field(
        default_factory=lambda: HEARE_HOME / "mute_input.flag"
    )
    cancel_flag_file: Path = field(default_factory=lambda: HEARE_HOME / "cancel.flag")
    interrupt_enabled_file: Path = field(
        default_factory=lambda: HEARE_HOME / "interrupt_enabled.off"
    )
    voice_state_file: Path = field(
        default_factory=lambda: HEARE_HOME / "voice_state.json"
    )
    # Chrome profile directory name (e.g. "Default", "Profile 1") — used by
    # the dashboard "launch debug Chrome" hotkey to skip the profile picker.
    # The picker drops --remote-debugging-port when it forks the chosen
    # profile's Chrome process, so picking it explicitly is the only
    # reliable way to keep CDP attached.
    chrome_profile_directory: str = "Default"
    inject_dir: Path = field(default_factory=lambda: HEARE_HOME / "inject")
    skills_paths: list[str] = field(default_factory=lambda: ["~/.heare/skills"])
    groq_api_key: str | None = None
    # Whisper transcription language. ISO-639-1 code (e.g. "en", "uk", "ru").
    # This is a HINT for Groq's Whisper, not a hard force — Groq will detect the
    # language from audio and override this hint if confident. Set to your DOMINANT
    # spoken language so Groq has the right context for detection. Default: "uk" (Ukrainian).
    # Multi-language conversation: Groq detects other languages when spoken, TTS voice
    # automatically swaps to match detected language via TranscriptionGateProcessor.
    groq_language: str = "uk"
    speaker_command_keyword_required: bool = True
    # Optional shared-secret phrase. When non-empty, saying
    # `<wake-word> <passphrase>` confirms a pending action. Additive —
    # the existing yes/no + speaker-id flow still works. Never logged.
    confirmation_passphrase: str | None = None
    # Wake word / command keyword. Set via config.toml. Default is "гава".
    wake_word: str = "гава"
    # Proactivity level for ambient mode: "low" | "medium" | "high"
    # medium = prompt defaults; low = reserved; high = very engaged.
    proactivity_level: str = "medium"
    command_keyword_pattern: str = r"\b(гава|heare|гей)\b"
    # Turn aggregation and conversation memory settings
    # Per plan US-010: default to False for gradual rollout
    turn_aggregation_enabled: bool = False
    focus_mode_turn_timeout: float = 0.5
    ambient_mode_turn_timeout: float = 3.0
    max_turn_duration: float = 30.0
    # On by default: this is what wires ConversationManager, which is the
    # only thing that persists tool calls to the `actions` table. With it off,
    # the agent's actions were silently never logged (History showed its words
    # but never its deeds). The feature is lightweight — a fire-and-forget
    # SQLite action log plus conversation-session tracking, no LLM summaries,
    # no turn aggregation — so the "gradual rollout" default-off had become a
    # bug rather than a safeguard.
    conversation_memory_enabled: bool = True
    memory_backend: str = "sqlite"  # "sqlite" | "engram" | "mem0" | "noop"
    memory_auto_extract: bool = True
    memory_block_max_chars: int = 500
    max_conversation_age_hours: float = 24.0
    topic_extraction_enabled: bool = True
    # CCS-01: hydration freshness window. The action log on startup is
    # filtered to rows newer than now - conversation_idle_seconds so a
    # stale 6-hour-old web_search doesn't pollute the prompt or trigger
    # CCS-04's refinement rule on a dead query. Default: 30 min.
    conversation_idle_seconds: float = 1800.0
    # CCS-04 prep: maximum age of a prior web_search the generator may
    # treat as refinable (vs. issuing a fresh search). MUST be <=
    # conversation_idle_seconds — see __post_init__ assertion.
    refinement_recency_seconds: float = 600.0
    # CCS-05a: stop-word vocabulary for the native cancel fast-path.
    # When the user utters a STANDALONE imperative containing one of these
    # words (≤4 words, optional politeness markers), the TranscriptionGate
    # pushes an InterruptionFrame upstream. Pipecat's native interruption
    # cancels in-flight tool calls and triggers the TTS fade-out observer.
    # Extensible at runtime via the HEARE_CANCEL_STOP_WORDS env var
    # (comma-separated). See src/language.py:is_standalone_cancel_imperative.
    # AC#1 lists the canonical en+uk+ru roots ("stop", "cancel", "halt",
    # "відміни", "отмени"). AC#5 also requires "будь ласка стоп" to fire,
    # which means "стоп" — the bare Cyrillic transliteration of "stop" —
    # must also be a stop-word. Including it here keeps both ACs satisfied
    # without test-only env overrides.
    cancel_stop_words: list[str] = field(
        default_factory=lambda: [
            "stop",
            "cancel",
            "halt",
            "відміни",
            "отмени",
            "стоп",
        ]
    )
    # LLM provider switching (deepseek | zai | opencode)
    llm_provider: str = "deepseek"
    provider_file: Path = field(default_factory=lambda: HEARE_HOME / "provider")
    zai_api_key: str | None = None
    zai_base_url: str = "https://api.z.ai/api/anthropic"
    zai_model: str = "claude-3-5-sonnet"
    # OpenCode Go — OpenAI-compatible API at opencode.ai/zen/go/v1.
    # API key is read from OPENCODE_API_KEY env var.
    opencode_api_key: str | None = None
    opencode_base_url: str = "https://opencode.ai/zen/go/v1"
    opencode_model: str = "minimax-m2.7"
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_timeout_seconds: float = 5.0
    # Phase 2.1 — action worker.
    action_timeout_seconds: float = 120.0
    intent_queue_max_pending: int = 32
    # Phase BP-02: coalesce rapid-fire TranscriptionFrames into one turn.
    # Groq STT occasionally splits one utterance into two frames when the
    # user pauses briefly mid-sentence (~0.5-1s). Buffer text and wait this
    # many seconds for a follow-up fragment before processing. 0 disables
    # the debounce — each frame dispatches immediately (legacy behaviour).
    # 0.3s provides a tighter debounce window for faster response while
    # still catching most STT fragment splits.
    transcript_debounce_seconds: float = 0.3
    # Phase 2: which tools the Pipecat-native pipeline may invoke via
    # register_function. Names are the tool identifiers defined in
    # src/tool_registry.py. None = use all enabled tools from the registry.
    agent_sdk_allowed_tools: list[str] | None = None
    # Web search provider: "auto" (use Serper if key available, else DuckDuckGo),
    # "duckduckgo" (force DuckDuckGo), or "serper" (force Serper with API key)
    web_search_provider: str = "auto"
    # Serper.dev API key for Google search. Get free credits at https://serper.dev
    # Falls back to DuckDuckGo if not set.
    serper_api_key: str | None = None
    # Auto-fetch the top organic search result so the agent has full content
    # for content-style queries (recipe, how-to). Disable to save bandwidth.
    web_search_fetch_top: bool = True
    # Capability discovery (US-004+): remote marketplace + MCP registry endpoints.
    # Empty string disables remote fetch for that source.
    marketplace_url: str = "https://skillsmp.com"
    mcp_registry_url: str = ""
    # US-006: when True, the installer requires entry.checksum (or a signature
    # field) to be set before installing. Default False — checksum is verified
    # opportunistically when present.
    installation_signature_required: bool = False

    def get_sdk_allowed_tools(self) -> list[str]:
        """Get the list of allowed SDK tools, using defaults if not set."""
        if self.agent_sdk_allowed_tools is not None:
            return self.agent_sdk_allowed_tools
        return _get_default_sdk_tools()

    # Context: how many recent transcripts to include in prompts.
    # Default 15 (~100 tokens, 0.05% of 200K context budget).
    # Increase for longer conversation memory, decrease for faster prompts.
    context_recent_transcripts_count: int = 15
    # Multi-intent mode: how many intents Claude can emit per response.
    # Allows chaining multiple actions (e.g., browser automation workflows).
    # Set to 1 for legacy single-intent behavior, 0 for unlimited.
    max_intents_per_response: int = 10
    # DEPRECATED — remove in next release.
    # All servers in workspace/.mcp.json are now automatically enabled.
    # This field is retained only so load_settings() can detect its presence
    # in TOML data and emit a deprecation warning. It is NOT used for any
    # runtime logic (no expansion, no allowlist gating).
    enable_mcp_servers: list[str] = field(default_factory=list)
    # Indication subsystem (sound + visual + macOS notification center).
    # Loaded from the [indication] table in ~/.heare/config.toml — see
    # _load_indication_settings(). Missing section is fully valid; defaults
    # match plan §3 (.omc/plans/indication.md).
    indication: IndicationSettings = field(default_factory=IndicationSettings)

    # File access settings for extended workspace management
    file_access_profile_path: Path = field(
        default_factory=lambda: HEARE_HOME / "profile.json"
    )
    file_access_auto_approve_workspace: bool = True
    file_access_ask_for_new_dirs: bool = True
    file_access_max_archive_size: int = 1024 * 1024 * 1024  # 1GB
    file_access_operation_timeout: int = 300  # 5 minutes

    # Browser-extension bridge — MV3 extension talks to the daemon via a
    # token-authenticated WebSocket on 127.0.0.1. Token is generated by the
    # bridge on first start (when empty) and persisted to [browser_bridge] in
    # config.toml so it survives daemon restarts. Rotate via
    # `heare rotate-browser-token`.
    browser_bridge_enabled: bool = True
    browser_bridge_port: int = 9333
    browser_bridge_token: str = ""

    # OpenCode sub-agent settings
    opencode_binary: str = "opencode"
    opencode_default_model: str | None = None  # None = use opencode's own config
    opencode_default_timeout: float = 120.0
    opencode_max_output_chars: int = 8000
    # Multi-agent background sub-agent settings
    agent_max_concurrent: int = 5
    agent_result_ttl_seconds: float = 600.0
    agent_server_start_timeout: float = 10.0
    agent_port_range_start: int = 14100
    agent_permission_timeout_seconds: float = 120.0
    opencode_binary: str | None = None

    # VAD (Voice Activity Detection) parameters. Tune these to adjust how
    # sensitive heare is to speech vs silence. Lower confidence = more
    # sensitive (triggers on quieter sounds). Lower stop_secs = faster
    # turn-end detection (but may cut off pauses mid-sentence).
    # Raised from 0.3/0.1/0.1. Against a microphone at 2.4x gain those
    # let room noise open a turn in 100 ms, and Whisper — which is
    # generative — dutifully wrote words into the silence. The energy
    # gate after STT catches what still gets through; this stops most of
    # it reaching Whisper at all, which also stops paying for it.
    vad_confidence: float = 0.6
    vad_start_secs: float = 0.2
    vad_stop_secs: float = 0.2
    vad_min_volume: float = 0.25

    # Audio gain / volume controls. Applied as multipliers to raw PCM
    # samples. 1.0 = unity (no change). 0.0 = silence. >1.0 = amplify.
    # input_gain affects the mic signal before STT.
    # output_volume affects the TTS signal before the speaker.
    input_gain: float = 1.0
    output_volume: float = 1.0

    # Audio device selection (optional). Set via config.toml or at
    # runtime via ``heare audio-input <name>`` / ``heare audio-output
    # <name>``. When None (default) the system default device is used.
    # Substring-matched against sounddevice device names.
    audio_input_device: str | None = None
    audio_output_device: str | None = None
    # Hot-reload files (same pattern as mode/provider). Written by the
    # CLI commands; read by the daemon's background device watcher.
    audio_input_device_file: Path = field(
        default_factory=lambda: HEARE_HOME / "audio_input_device"
    )
    audio_output_device_file: Path = field(
        default_factory=lambda: HEARE_HOME / "audio_output_device"
    )

    # Sidetone — copies mic input to speaker so the user can monitor
    # exactly what the agent hears. Gated off during bot speech to
    # prevent feedback loops.
    sidetone_enabled: bool = False
    sidetone_file: Path = field(default_factory=lambda: HEARE_HOME / "sidetone.flag")
    sidetone_volume: float = 0.5

    def __post_init__(self) -> None:
        # CCS-01 invariant: a refinement window longer than the idle
        # hydration window would let the generator refine a query that
        # was filtered out of recent_actions on startup — incoherent.
        assert self.refinement_recency_seconds <= self.conversation_idle_seconds, (
            f"refinement_recency_seconds ({self.refinement_recency_seconds}) "
            f"must be <= conversation_idle_seconds ({self.conversation_idle_seconds}); "
            f"otherwise the refinement rule would fire on entries hydration filters out."
        )

    def ensure_dirs(self) -> None:
        for p in (self.workspace_dir, self.log_dir, HEARE_HOME):
            p.mkdir(parents=True, exist_ok=True)


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_settings() -> Settings:
    settings = Settings()
    toml_data = _read_toml(HEARE_HOME / "config.toml")

    for key, value in toml_data.items():
        if key in ("indication", "browser_bridge"):
            # Nested tables — handled below.
            continue
        if hasattr(settings, key):
            current = getattr(settings, key)
            if isinstance(current, Path):
                value = Path(value).expanduser()
            elif isinstance(current, Mode):
                value = Mode(value)
            setattr(settings, key, value)

    settings.indication = _load_indication_settings(toml_data.get("indication", {}))
    _load_browser_bridge_settings(settings, toml_data.get("browser_bridge", {}))

    # [interrupt] section — persistent default for barge-in behaviour.
    # When enabled=false, the flag file is created on daemon start so the
    # pipeline gate drops mic input while the bot is speaking. The
    # /interrupt API endpoint toggles the flag at runtime independently.
    interrupt_sec = toml_data.get("interrupt", {})
    if isinstance(interrupt_sec, dict):
        interrupt_enabled = interrupt_sec.get("enabled", True)
        if not interrupt_enabled:
            settings.interrupt_enabled_file.parent.mkdir(parents=True, exist_ok=True)
            settings.interrupt_enabled_file.touch(exist_ok=True)

    # [audio] section — VAD sensitivity, mic gain, speaker volume.
    audio_sec = toml_data.get("audio", {})
    if isinstance(audio_sec, dict):
        for attr, key in (
            ("vad_confidence", "vad_confidence"),
            ("vad_start_secs", "vad_start_secs"),
            ("vad_stop_secs", "vad_stop_secs"),
            ("vad_min_volume", "vad_min_volume"),
            ("input_gain", "input_gain"),
            ("output_volume", "output_volume"),
            ("sidetone_volume", "sidetone_volume"),
        ):
            if key in audio_sec:
                try:
                    val = float(audio_sec[key])
                    setattr(settings, attr, val)
                except (TypeError, ValueError):
                    logger.warning(
                        "[audio].%s is not a float — keeping default (%s)",
                        key,
                        getattr(settings, attr),
                    )

    # Env var overrides for audio settings
    for env_key, attr, min_val, max_val in (
        ("HEARE_VAD_CONFIDENCE", "vad_confidence", 0.0, 1.0),
        ("HEARE_VAD_START_SECS", "vad_start_secs", 0.0, 2.0),
        ("HEARE_VAD_STOP_SECS", "vad_stop_secs", 0.0, 2.0),
        ("HEARE_VAD_MIN_VOLUME", "vad_min_volume", 0.0, 2.0),
        ("HEARE_INPUT_GAIN", "input_gain", 0.0, 5.0),
        ("HEARE_OUTPUT_VOLUME", "output_volume", 0.0, 5.0),
    ):
        raw = os.environ.get(env_key)
        if raw is not None:
            try:
                val = float(raw)
                if min_val <= val <= max_val:
                    setattr(settings, attr, val)
                else:
                    logger.warning(
                        "%s=%s out of range [%s, %s] — keeping default (%s)",
                        env_key,
                        raw,
                        min_val,
                        max_val,
                        getattr(settings, attr),
                    )
            except ValueError:
                logger.warning(
                    "%s=%s is not a float — keeping default (%s)",
                    env_key,
                    raw,
                    getattr(settings, attr),
                )

    # Build command_keyword_pattern from wake_word if the user customized it
    if settings.wake_word != "гава":
        import re

        escaped = re.escape(settings.wake_word)
        settings.command_keyword_pattern = rf"\b({escaped})\b"

    if settings.confirmation_passphrase is not None:
        if not isinstance(settings.confirmation_passphrase, str):
            logger.warning(
                "confirmation_passphrase must be a string; ignoring (got %s)",
                type(settings.confirmation_passphrase).__name__,
            )
            settings.confirmation_passphrase = None
        else:
            phrase = settings.confirmation_passphrase.strip()
            if phrase and len(phrase) < 5:
                logger.warning(
                    "confirmation_passphrase is very short (len=%d); "
                    "recommend 5+ chars / 2+ words to avoid STT false-positives",
                    len(phrase),
                )

    settings.groq_api_key = os.environ.get("GROQ_API_KEY")
    settings.serper_api_key = os.environ.get("SERPER_API_KEY")

    from src.agent.llm.providers import PROVIDERS

    for key, cfg in PROVIDERS.items():
        setattr(settings, cfg.api_key_attr, os.environ.get(cfg.api_key_env))

    # CCS-05a: env override for cancel_stop_words (comma-separated). Empty
    # tokens are dropped; surrounding whitespace stripped; case preserved
    # (the detector lowercases at match time).
    cancel_words_env = os.environ.get("HEARE_CANCEL_STOP_WORDS")
    if cancel_words_env is not None:
        parsed = [w.strip() for w in cancel_words_env.split(",") if w.strip()]
        if parsed:
            settings.cancel_stop_words = parsed

    barge_in_env = os.environ.get("HEARE_BARGE_IN")
    if barge_in_env is not None:
        settings.barge_in_enabled = barge_in_env.strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    barge_in_ratio_env = os.environ.get("HEARE_BARGE_IN_ECHO_RATIO")
    if barge_in_ratio_env is not None:
        try:
            settings.barge_in_echo_ratio = float(barge_in_ratio_env)
        except ValueError:
            logger.warning(
                "HEARE_BARGE_IN_ECHO_RATIO is not a float; ignoring (got %r)",
                barge_in_ratio_env,
            )

    mode_override = os.environ.get("HEARE_MODE")
    if mode_override:
        settings.mode = Mode(mode_override)
    elif settings.mode_file.exists():
        raw = settings.mode_file.read_text().strip()
        if raw:
            settings.mode = Mode(raw)

    if settings.provider_file.exists():
        raw = settings.provider_file.read_text().strip().lower()
        if raw in PROVIDERS:
            settings.llm_provider = raw

    if settings.audio_input_device_file.exists():
        raw = settings.audio_input_device_file.read_text().strip()
        if raw:
            settings.audio_input_device = raw

    if settings.audio_output_device_file.exists():
        raw = settings.audio_output_device_file.read_text().strip()
        if raw:
            settings.audio_output_device = raw

    if toml_data.get("enable_mcp_servers"):
        logger.warning(
            "enable_mcp_servers is deprecated. All servers in workspace/.mcp.json are now "
            "automatically enabled. Remove enable_mcp_servers from config.toml. Servers listed "
            "in enable_mcp_servers that are not yet in workspace/.mcp.json must be added there "
            "manually — the server will not launch otherwise."
        )

    return settings


def write_browser_bridge_token(settings: Settings, token: str) -> None:
    """Persist `token` to the `[browser_bridge]` table in `~/.heare/config.toml`.

    Atomic (tmpfile + os.replace). Preserves all other config sections by
    rewriting the `[browser_bridge]` block in place when present, or appending
    it as a new section when absent. Updates `settings.browser_bridge_token`
    in place so callers don't need to reload.
    """
    import re
    import tempfile

    config_path = HEARE_HOME / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    quoted = '"' + token.replace("\\", "\\\\").replace('"', '\\"') + '"'
    new_section = f"[browser_bridge]\ntoken = {quoted}\n"

    existing = config_path.read_text() if config_path.exists() else ""
    section_re = re.compile(
        r"(?ms)^\[browser_bridge\][^\[]*?(?=^\[|\Z)",
    )
    match = section_re.search(existing)

    if match:
        block = match.group(0)
        token_re = re.compile(r"(?m)^\s*token\s*=.*$")
        if token_re.search(block):
            new_block = token_re.sub(f"token = {quoted}", block, count=1)
        else:
            # Insert token line right after the [browser_bridge] header.
            new_block = re.sub(
                r"^\[browser_bridge\][^\n]*\n",
                lambda m: m.group(0) + f"token = {quoted}\n",
                block,
                count=1,
            )
        content = existing[: match.start()] + new_block + existing[match.end() :]
    else:
        sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
        content = existing + sep + ("\n" if existing else "") + new_section

    fd, tmp_path = tempfile.mkstemp(
        prefix=".config.toml.",
        suffix=".tmp",
        dir=str(config_path.parent),
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, config_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    settings.browser_bridge_token = token


def set_confirmation_passphrase(word: str) -> None:
    """Persist `word` as the top-level `confirmation_passphrase` key in
    `~/.heare/config.toml`. Restart required for the daemon to pick it up.

    Appending the line at end-of-file — the old approach — parks it inside
    whatever ``[table]`` happens to be last, and ``load_settings`` only reads
    top-level keys, so the passphrase silently did nothing. Any such misplaced
    copy is removed before the key is rewritten in the preamble.
    """
    import re

    config_path = HEARE_HOME / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        content = config_path.read_text()
        pruned = re.sub(
            r"(?m)^[ \t]*confirmation_passphrase[ \t]*=.*$\n?", "", content
        )
        if pruned != content:
            _atomic_write(config_path, pruned)

    write_config_toml_values({"confirmation_passphrase": word})


def backup_session_file(settings: "Settings") -> Path | None:
    """Rename `settings.session_file` to a numbered backup, if it exists.

    Returns the backup path, or None if there was no session file to reset.
    """
    if not settings.session_file.exists():
        return None
    idx = 0
    while True:
        backup = settings.session_file.with_name(f"session_{idx}.backup.json")
        if not backup.exists():
            break
        idx += 1
    settings.session_file.rename(backup)
    return backup


def write_env_var(key: str, value: str) -> None:
    """Write or update an env var in .env file."""
    env_path = HEARE_HOME.parent / ".env"
    lines = []
    found = False
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


# ── API key plumbing ──────────────────────────────────────────────────
#
# Every UI that can set an API key (dashboard settings card, brain card,
# setup modal, the plain HTML form) goes through these helpers, so there is
# exactly one definition of "which keys exist", one place that writes the
# .env file, and one place that edits config.toml.


def api_key_fields() -> dict[str, str]:
    """Map ``Settings`` attribute name → environment variable name for every
    API key the UI is allowed to set.

    Derived from the provider registry so adding a provider needs no second
    edit here. Groq is listed explicitly because it is the STT provider, not
    an LLM one, and so has no ``ProviderConfig``.
    """
    from src.agent.llm.providers import PROVIDERS

    fields = {"groq_api_key": "GROQ_API_KEY"}
    for cfg in PROVIDERS.values():
        fields[cfg.api_key_attr] = cfg.api_key_env
    return fields


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    """Write `content` to `path` via tmpfile + os.replace, chmod `mode`."""
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_env_updates(updates: dict[str, str], env_path: Path | None = None) -> None:
    """Merge `updates` (ENV_NAME → value) into ``~/.heare/.env``.

    Updates existing assignments in place and appends the rest, so comments,
    blank lines, key order and unrelated variables all survive. Written
    atomically with 0600 — the file holds credentials.
    """
    if not updates:
        return
    path = env_path or (HEARE_HOME / ".env")
    remaining = dict(updates)
    lines: list[str] = []

    if path.exists():
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                name = stripped.split("=", 1)[0].strip()
                if name in remaining:
                    lines.append(f"{name}={remaining.pop(name)}")
                    continue
            lines.append(line)

    lines.extend(f"{name}={value}" for name, value in remaining.items())
    _atomic_write(path, "\n".join(lines) + "\n")


def write_config_toml_values(values: dict[str, str]) -> None:
    """Set top-level string keys in ``~/.heare/config.toml`` in place.

    Only the preamble (everything before the first ``[table]`` header) is
    touched; section tables, their contents and all comments are copied
    through byte-for-byte.

    This exists because the obvious shortcut — read every ``k = v`` line into
    a flat dict and rewrite the file from it — silently drops the
    ``[section]`` headers. Doing that to ``[browser_bridge]`` orphans the
    bridge token, so the daemon cannot load it, mints a fresh one, and every
    Chrome extension still holding the old token is rejected with close 4001.
    """
    import re

    if not values:
        return

    config_path = HEARE_HOME / "config.toml"
    content = config_path.read_text() if config_path.exists() else ""

    first_table = re.search(r"(?m)^[ \t]*\[", content)
    head = content[: first_table.start()] if first_table else content
    tail = content[first_table.start() :] if first_table else ""

    for key, value in values.items():
        quoted = '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
        key_re = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=.*$")
        if key_re.search(head):
            head = key_re.sub(f"{key} = {quoted}", head, count=1)
        else:
            if head and not head.endswith("\n"):
                head += "\n"
            head += f"{key} = {quoted}\n"

    if tail and head and not head.endswith("\n"):
        head += "\n"

    _atomic_write(config_path, head + tail)
