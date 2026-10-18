"""One-time codes: taken once, short-lived, never kept in the clear."""

from __future__ import annotations

from heare.api import pair


def test_a_code_is_eight_characters(tmp_path):
    assert len(pair.make(tmp_path / "codes.json")) == pair.LENGTH


def test_a_code_is_taken_once(tmp_path):
    path = tmp_path / "codes.json"
    code = pair.make(path)
    assert pair.redeem(path, code)
    assert not pair.redeem(path, code)


def test_a_code_dies_after_five_minutes(tmp_path):
    path = tmp_path / "codes.json"
    code = pair.make(path, now=1_000.0)
    assert not pair.redeem(path, code, now=1_000.0 + pair.TTL_S + 1.0)


def test_a_wrong_code_is_refused(tmp_path):
    path = tmp_path / "codes.json"
    pair.make(path)
    assert not pair.redeem(path, "ZZZZZZZZ")


def test_only_the_hash_is_written_down(tmp_path):
    path = tmp_path / "codes.json"
    code = pair.make(path)
    assert code not in path.read_text(encoding="utf-8")
