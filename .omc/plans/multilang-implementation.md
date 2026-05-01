# Multi-Language Support Implementation (Approach 3)

**Status**: In Progress  
**Date**: 2026-05-01  
**Approach**: Claude-based language detection with automatic voice selection

## Problem

Currently, all audio transcription is forced to Ukrainian (`groq_language = "uk"`), causing English speech to be transcribed as broken Ukrainian. The user wants:
- English speech → English transcription → English response → English TTS voice
- Ukrainian speech → Ukrainian transcription → Ukrainian response → Ukrainian TTS voice  
- Agent auto-detects language and responds consistently in the same language

## Solution: Approach 3 (Best)

Implement Claude-based language detection with automatic voice selection for robust multi-language support.

### Changes Made

#### 1. **Language Detection Module** (`src/language_detector.py`)
- `detect_language_via_claude()`: Uses Claude to confirm language from text when needed
- `_detect_language_heuristic()`: Fallback heuristic detection based on Cyrillic/Latin character distribution
- `get_voice_for_language()`: Maps language codes (en/uk/ru) to TTS voices
- `LANG_TO_VOICE`: Mapping dictionary for voice names
- Supports: English (en-US-AriaNeural), Ukrainian (uk-UA-OstapNeural), Russian (ru-RU-DmitryNeural)

#### 2. **Language Refiner Processor** (`src/language_refiner.py`)
- Sits after STT in the pipeline
- Refines language detection via Claude when Groq's confidence is ambiguous
- Validates Groq's auto-detect results
- Stores refined language in frame for downstream processors

#### 3. **Configuration Changes** (`src/config.py`)
- Changed `groq_language` default from `"uk"` (hardcoded) to `"auto"` (auto-detect)
- Updated docstring to clarify auto-detection behavior
- Supports per-session language override via config

#### 4. **Pipeline Modifications** (`src/pipeline.py`)
- STT initialization now conditionally sets language parameter:
  - When `groq_language == "auto"`: Groq's Whisper auto-detects language from audio
  - When `groq_language` is ISO code (e.g., "uk"): Forces transcription in that language
- LanguageState initialization defaults to "en" when groq_language is "auto"

### How It Works

**Turn Flow**:
1. **User speaks** → Audio arrives at STT
2. **STT processes** → Groq's Whisper auto-detects language from audio, returns text + language
3. **TranscriptionGateProcessor** → Reads Groq's language detection, applies 2-turn hysteresis
4. **Language confirmation** → (Optional) Uses heuristic/Claude to confirm language
5. **TTS voice selection** → Dynamically calls `tts_service.set_voice()` to match detected language
6. **System prompt injection** → LanguageState triggers system prompt rewrite with language info
7. **LLM generates** → Claude responds in detected language
8. **Response TTS** → Uses matching voice for response language

**Existing Infrastructure Used**:
- `LanguageState` (already exists): Tracks active user language across turns
- `TranscriptionGateProcessor` (already exists): 2-turn hysteresis, TTS voice swapping
- `_wire_language_state()` (already exists): System prompt injection on language change
- `voice_for_language()` (already exists in language.py): Language → Voice mapping

### Architecture Benefits

1. **No Hardcoding**: Language detected per-turn, not forced at startup
2. **Fallback Chain**: Groq → Heuristic → Claude → English default
3. **Hysteresis**: 2-turn confirmation prevents flaky switches between similar languages
4. **Dynamic Voice**: TTS voice changes mid-conversation without restart
5. **System Prompt**: Injected language info helps Claude maintain consistency
6. **Zero Coupling**: New modules don't modify existing Pipecat architecture

### Testing

New test file: `tests/test_language_detector.py`
- Voice mapping tests (en/uk/ru/unknown)
- Heuristic detection (English, Ukrainian, Russian, mixed, empty)
- Language name mapping

Existing tests updated:
- `test_default_settings()`: Changed expected groq_language from "uk" to "auto"

### Configuration Examples

**Auto-detection (default)**:
```toml
# .env or config.toml — not specified, uses default
groq_language = "auto"
```

**Force Ukrainian**:
```toml
groq_language = "uk"  # Disables auto-detect, forces all transcription as Ukrainian
```

**Force English**:
```toml
groq_language = "en"  # Disables auto-detect, forces all transcription as English
```

### Test Instructions

1. Build and start in watch mode:
   ```bash
   make build && make watch
   ```

2. Test English speech:
   - Speak: "Hello, how are you?"
   - Expected: Transcribed as English, response in English, voice is AriaNeural

3. Test Ukrainian speech:
   - Speak: "Привіт, як справи?"
   - Expected: Transcribed as Ukrainian, response in Ukrainian, voice is OstapNeural

4. Test language switching:
   - Speak in English, then Ukrainian in next turn
   - Expected: TTS voice changes per turn, response language matches

5. Test force-language mode:
   - Edit config: `groq_language = "uk"`
   - Restart, speak English
   - Expected: English transcribed as broken Ukrainian (old behavior)

### Known Limitations

- Groq's Whisper is called per-turn with audio (no caching) — inherent to STT
- Claude detection adds latency if confidence is ambiguous (optional, fallback only)
- Edge languages (code-switching, mixed) may be ambiguous — heuristic handles most cases
- TTS voice selection happens per-turn, not per-sentence (sufficient for conversation)

### Future Enhancements

- Sentence-level language detection for mixed-language responses
- Confidence scoring from Groq integrated into hysteresis threshold
- Per-user language preference overrides
- Language detection metrics in watch dashboard

## Files Changed

- `src/language_detector.py` (NEW)
- `src/language_refiner.py` (NEW)
- `src/config.py` (modified: groq_language default)
- `src/pipeline.py` (modified: STT language handling)
- `tests/test_config.py` (updated: groq_language assertion)
- `tests/test_language_detector.py` (NEW)

## Status: Implementation Complete ✓

- [x] Language detector module implemented
- [x] Language refiner processor implemented
- [x] Config updated for auto-detection
- [x] Pipeline wired for conditional language parameter
- [x] Tests created and passing
- [x] Existing architecture leveraged (no breaking changes)
