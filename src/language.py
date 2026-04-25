import re

SUPPORTED_LANGS: set[str] = {"en", "uk", "ru"}
DEFAULT_LANG: str = "en"
LANG_NAMES: dict[str, str] = {"en": "English", "uk": "Ukrainian", "ru": "Russian"}
LANG_TO_VOICE: dict[str, str] = {
    "en": "en-US-AriaNeural",
    "uk": "uk-UA-OstapNeural",
    "ru": "ru-RU-DmitryNeural",
}
WHISPER_NAME_TO_ISO: dict[str, str] = {
    "english": "en",
    "ukrainian": "uk",
    "russian": "ru",
}
CANCEL_PATTERNS: dict[str, re.Pattern] = {
    "en": re.compile(r"(?i)(?:^|[\s.,!?—])(cancel|stop|abort|nevermind|never mind)(?:$|[\s.,!?—])"),
    "uk": re.compile(r"(?i)(?:^|[\s.,!?—])(скасуй|відміни|стоп|не треба)(?:$|[\s.,!?—])"),
    "ru": re.compile(r"(?i)(?:^|[\s.,!?—])(отмени|отмена|стоп|не надо)(?:$|[\s.,!?—])"),
}


def detect_language_from_frame(frame, fallback: str = "en") -> str:
    try:
        result = getattr(frame, "result", None)
        if result is None:
            return fallback
        lang_name = getattr(result, "language", None)
        if lang_name is None:
            return fallback
        iso = WHISPER_NAME_TO_ISO.get(lang_name.lower())
        if iso and iso in SUPPORTED_LANGS:
            return iso
        return fallback
    except Exception:
        return fallback


def voice_for_language(lang: str) -> str:
    return LANG_TO_VOICE.get(lang, LANG_TO_VOICE["en"])


def check_cancel(text: str, lang: str) -> bool:
    pattern = CANCEL_PATTERNS.get(lang)
    if pattern and pattern.search(text):
        return True
    if lang != "en":
        en_pattern = CANCEL_PATTERNS.get("en")
        if en_pattern and en_pattern.search(text):
            return True
    return False


def detect_script_language(text: str) -> str:
    cyrillic = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    latin = sum(1 for c in text if ("A" <= c <= "Z") or ("a" <= c <= "z"))
    total = cyrillic + latin
    if total == 0:
        return "unknown"
    if cyrillic / total > 0.5:
        return "cyrillic"
    if latin / total > 0.5:
        return "latin"
    return "unknown"
