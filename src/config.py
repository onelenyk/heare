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
    from .tool_registry import DEFAULT_SDK_ALLOWED_TOOLS
    return DEFAULT_SDK_ALLOWED_TOOLS

logger = logging.getLogger("heare.config")


class Mode(str, Enum):
    SILENT = "silent"
    FOCUS = "focus"
    AMBIENT = "ambient"


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
        _QUIET_HOUR_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
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
    "attention":     (True,  True,  True),
    "error":         (True,  True,  True),
    "long_running":  (True,  True,  False),
    "success":       (True,  True,  False),
    "info":          (False, True,  False),
    "input_waiting": (True,  True,  True),
    "countdown":     (True,  True,  False),
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
            logger.warning(
                "indication.quiet_hours must be a list — keeping default"
            )
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


@dataclass
class Settings:
    mode: Mode = Mode.AMBIENT
    tts_voice: str = "en-US-AriaNeural"
    tts_sample_rate: int = 24000
    confirmation_timeout_seconds: int = 30
    # CCS-03: a CONFIRMATION_DEADLINE indication fires this many seconds
    # BEFORE confirmation_timeout_seconds elapses, giving the user an
    # audible/visible "5s left" cue. 0 disables the cue entirely. If the
    # value is >= confirmation_timeout_seconds it is clamped at startup
    # to max(0, timeout - 1) and a warning is logged (see decider.py).
    confirmation_deadline_warning_seconds: float = 5.0
    transcript_retention_days: int = 30
    min_action_confidence: float = 0.8
    bot_speaking_cooldown_seconds: float = 2.0
    warmup_interval_seconds: float = 240.0
    workspace_dir: Path = field(default_factory=lambda: HEARE_HOME / "workspace")
    session_file: Path = field(default_factory=lambda: HEARE_HOME / "session.json")
    identity_file: Path = field(default_factory=lambda: HEARE_HOME / "identity.json")
    db_path: Path = field(default_factory=lambda: HEARE_HOME / "heare.db")
    log_dir: Path = field(default_factory=lambda: HEARE_HOME / "logs")
    mode_file: Path = field(default_factory=lambda: HEARE_HOME / "mode")
    pid_file: Path = field(default_factory=lambda: HEARE_HOME / "heare.pid")
    claude_cli: str = "claude"
    claude_timeout_seconds: int = 60
    claude_max_retries: int = 3
    claude_max_calls_per_minute: int = 30
    claude_decider_model: str = "haiku"
    groq_api_key: str | None = None
    # Whisper transcription language. NOT a hint — Whisper transcribes audio
    # AS this language and does not auto-detect when it is set. Cross-language
    # speech (e.g. occasional English in a Ukrainian session) is still handled
    # gracefully. Set to your dominant spoken language. ISO-639-1 code.
    # Auto-detect requires a Pipecat patch (PRD A.1 follow-up).
    groq_language: str = "uk"
    # Speaker recognition (off by default — torch/speechbrain live under
    # [project.optional-dependencies].speaker and are lazy-imported)
    speaker_id_enabled: bool = True
    speaker_id_threshold_match: float = 0.50
    speaker_id_threshold_unknown: float = 0.55
    speaker_id_sticky_threshold: float = 0.80
    speaker_id_sticky_seconds: float = 5.0
    speaker_id_min_duration_ms: int = 400
    speaker_id_accum_target_ms: int = 3000
    speaker_id_centroid_k: int = 5
    speaker_id_ema_alpha: float = 0.1
    speaker_id_auto_enroll_after: int = 2
    speaker_id_auto_enroll_enabled: bool = True
    speaker_id_auto_enroll_owner_enabled: bool = True
    speaker_id_auto_enroll_owner_after: int = 5
    speaker_command_keyword_required: bool = True
    # Optional shared-secret phrase. When non-empty, saying
    # `<wake-word> <passphrase>` confirms a pending action. Additive —
    # the existing yes/no + speaker-id flow still works. Never logged.
    confirmation_passphrase: str | None = None
    # Wake word / command keyword. Set via config.toml or onboarding. Default is "гава".
    wake_word: str = "гава"
    # Proactivity level for ambient mode: "low" | "medium" | "high"
    # medium = prompt defaults; low = reserved; high = very engaged.
    proactivity_level: str = "medium"
    command_keyword_pattern: str = r"\b(гава|heare|гей)\b"
    speakers_file: Path = field(default_factory=lambda: HEARE_HOME / "speakers.json")
    # Speaker naming — LLM-driven identity inference for guest_NN slots.
    # Runs as a standalone async task; never blocks audio/STT/TTS.
    speaker_namer_enabled: bool = True
    speaker_namer_model: str = "anthropic/claude-haiku-4.5"
    speaker_namer_min_turns: int = 3
    speaker_namer_debounce_seconds: float = 10.0
    speaker_namer_confidence_threshold: float = 0.8
    speaker_namer_confidence_hysteresis: float = 0.05
    speaker_namer_buffer_size: int = 10
    speaker_namer_queue_max: int = 64
    speaker_namer_timeout_seconds: float = 15.0
    claude_sdk_cli_path: str | None = None
    # Turn aggregation and conversation memory settings
    # Per plan US-010: default to False for gradual rollout
    turn_aggregation_enabled: bool = False
    focus_mode_turn_timeout: float = 0.5
    ambient_mode_turn_timeout: float = 3.0
    max_turn_duration: float = 30.0
    conversation_memory_enabled: bool = False
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
    # CCS-05a: stop-word vocabulary for the decider's pre-generator cancel
    # fast-path. When the user utters a STANDALONE imperative containing
    # one of these words (≤4 words, optional politeness markers), the
    # decider calls IntentQueue.cancel_active() before invoking the LLM.
    # Extensible at runtime via the HEARE_CANCEL_STOP_WORDS env var
    # (comma-separated). See src/language.py:is_standalone_cancel_imperative.
    # AC#1 lists the canonical en+uk+ru roots ("stop", "cancel", "halt",
    # "відміни", "отмени"). AC#5 also requires "будь ласка стоп" to fire,
    # which means "стоп" — the bare Cyrillic transliteration of "stop" —
    # must also be a stop-word. Including it here keeps both ACs satisfied
    # without test-only env overrides.
    cancel_stop_words: list[str] = field(
        default_factory=lambda: [
            "stop", "cancel", "halt", "відміни", "отмени", "стоп",
        ]
    )
    # Phase 1/2.1 — generator pipeline via OpenRouter.
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-3.1-flash-lite-preview-20260303"
    openrouter_timeout_seconds: float = 5.0
    # Topic extraction backend. "openrouter" (default) routes
    # ConversationManager.extract_topics through a non-streaming
    # OpenRouterTopicExtractorCLI client. "claude" routes through the live
    # Claude SDK / CLI session. main.py auto-falls back to Claude with a
    # warning when topic_extraction_backend=openrouter but no OPENROUTER_API_KEY
    # is configured.
    topic_extraction_backend: str = "openrouter"
    topic_extraction_openrouter_model: str = "google/gemini-3.1-flash-lite-preview-20260303"
    topic_extraction_openrouter_timeout_seconds: float = 5.0
    # Phase 2.1 — action worker.
    action_timeout_seconds: float = 120.0
    intent_queue_max_pending: int = 32
    # Phase BP-02: coalesce rapid-fire TranscriptionFrames into one turn.
    # Groq STT occasionally splits one utterance into two frames when the
    # user pauses briefly mid-sentence (~0.5-1s). Buffer text and wait this
    # many seconds for a follow-up fragment before processing. 0 disables
    # the debounce — each frame dispatches immediately (legacy behaviour).
    transcript_debounce_seconds: float = 0.6
    # Phase B-tools: which SDK tools the agent backend may invoke. Names
    # are the CamelCase identifiers the claude-agent-sdk expects (see
    # ClaudeAgentOptions.allowed_tools). The IntentQueue accepts a
    # lowercase alias set — defined in src/tool_registry.py.
    # None = use default from tool_registry.
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
        if key == "indication":
            # Nested table — handled below.
            continue
        if hasattr(settings, key):
            current = getattr(settings, key)
            if isinstance(current, Path):
                value = Path(value).expanduser()
            elif isinstance(current, Mode):
                value = Mode(value)
            setattr(settings, key, value)

    settings.indication = _load_indication_settings(toml_data.get("indication", {}))

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
    settings.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
    settings.serper_api_key = os.environ.get("SERPER_API_KEY")

    # CCS-05a: env override for cancel_stop_words (comma-separated). Empty
    # tokens are dropped; surrounding whitespace stripped; case preserved
    # (the detector lowercases at match time).
    cancel_words_env = os.environ.get("HEARE_CANCEL_STOP_WORDS")
    if cancel_words_env is not None:
        parsed = [w.strip() for w in cancel_words_env.split(",") if w.strip()]
        if parsed:
            settings.cancel_stop_words = parsed

    mode_override = os.environ.get("HEARE_MODE")
    if mode_override:
        settings.mode = Mode(mode_override)
    elif settings.mode_file.exists():
        raw = settings.mode_file.read_text().strip()
        if raw:
            settings.mode = Mode(raw)

    claude_override = os.environ.get("HEARE_CLAUDE_CLI")
    if claude_override:
        settings.claude_cli = claude_override

    if toml_data.get("enable_mcp_servers"):
        logger.warning(
            "enable_mcp_servers is deprecated. All servers in workspace/.mcp.json are now "
            "automatically enabled. Remove enable_mcp_servers from config.toml. Servers listed "
            "in enable_mcp_servers that are not yet in workspace/.mcp.json must be added there "
            "manually — the server will not launch otherwise."
        )

    return settings
