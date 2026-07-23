"""Tests for src.memory.extractor — extraction heuristics + dedup."""

from __future__ import annotations

from src.memory.base import MemoryType
from src.memory.extractor import (
    _content_fingerprint,
    _normalize_content,
    extract_memories,
)


class TestNormalizeContent:
    def test_strips_diacritics(self) -> None:
        assert _normalize_content("café naïve") == "cafe naive"

    def test_lowercases_and_collapses(self) -> None:
        assert _normalize_content("  Hello   WORLD  ") == "hello world"

    def test_removes_punctuation(self) -> None:
        assert _normalize_content(".Hello, world!") == ".hello, world!"


class TestContentFingerprint:
    def test_removes_stopwords(self) -> None:
        fp = _content_fingerprint("I live in Kyiv")
        assert "kyiv" in fp
        assert "in" not in fp.split()

    def test_empty_returns_empty(self) -> None:
        assert _content_fingerprint("") == ""

    def test_short_tokens_excluded(self) -> None:
        fp = _content_fingerprint("a b c")
        assert fp == ""


class TestExtractMemories:
    def test_uk_name_mene_zvaty(self) -> None:
        mems = extract_memories("Мене звати Олексій")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.FACT
        assert mems[0].content == "Олексій"

    def test_uk_casual_name_klychut(self) -> None:
        mems = extract_memories("Мене кличуть Андрій")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.FACT
        assert "Андрій" in mems[0].content

    def test_uk_preference(self) -> None:
        mems = extract_memories("Я люблю каву")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.PREFERENCE
        assert "каву" in mems[0].content

    def test_uk_location(self) -> None:
        mems = extract_memories("Я живу в Києві")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.FACT

    def test_uk_decision(self) -> None:
        mems = extract_memories("Я вирішив почати бігати")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.DECISION
        assert "бігати" in mems[0].content

    def test_en_name_my_name_is(self) -> None:
        mems = extract_memories("My name is Alice")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.FACT
        assert mems[0].content == "Alice"

    def test_en_name_call_me(self) -> None:
        mems = extract_memories("Call me Bob")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.FACT
        assert mems[0].content == "Bob"

    def test_en_casual_name(self) -> None:
        mems = extract_memories("I'm Charlie")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.FACT

    def test_en_preference(self) -> None:
        mems = extract_memories("I like pizza")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.PREFERENCE

    def test_en_location(self) -> None:
        mems = extract_memories("I live in London")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.FACT

    def test_en_decision(self) -> None:
        mems = extract_memories("I decided to learn piano")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.DECISION

    def test_en_work_role(self) -> None:
        mems = extract_memories("I work as a developer")
        assert len(mems) == 1
        assert mems[0].type == MemoryType.FACT

    def test_no_match_returns_empty(self) -> None:
        mems = extract_memories("The weather is nice today")
        assert len(mems) == 0

    def test_dedups_within_batch(self) -> None:
        mems = extract_memories("My name is Alice. My name is Alice.")
        assert len(mems) == 1

    def test_short_value_filtered(self) -> None:
        mems = extract_memories("I am x")
        assert len(mems) == 0

    def test_confidence_is_auto_extracted(self) -> None:
        mems = extract_memories("My name is Alice")
        assert mems[0].confidence == 0.7
        assert mems[0].source == "auto_extracted"

    def test_source_set_to_auto(self) -> None:
        mems = extract_memories("My name is Alice")
        assert mems[0].source == "auto_extracted"
        assert mems[0].id == ""
