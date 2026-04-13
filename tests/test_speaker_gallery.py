"""Tests for src/speaker_gallery.py — label sanitization + atomic persistence."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from src.speaker_gallery import (
    LABEL_MAX_LEN,
    LabelValidationError,
    SpeakerGallery,
    sanitize_label,
)


@pytest.mark.parametrize(
    "bad_input",
    [
        "",
        "   ",
        "a" * (LABEL_MAX_LEN + 1),
        "line1\nline2",
        "{evil}",
        "name<script>",
        "tab\there",
        "null\x00byte",
    ],
)
def test_sanitize_label_rejects(bad_input: str) -> None:
    with pytest.raises(LabelValidationError):
        sanitize_label(bad_input)


@pytest.mark.parametrize(
    "good_input,expected",
    [
        ("Nazar", "Nazar"),
        ("  Nazar  ", "Nazar"),
        ("Оля", "Оля"),
        ("a" * LABEL_MAX_LEN, "a" * LABEL_MAX_LEN),
    ],
)
def test_sanitize_label_accepts(good_input: str, expected: str) -> None:
    assert sanitize_label(good_input) == expected


def test_gallery_load_missing_file(tmp_path: Path) -> None:
    g = SpeakerGallery.load(tmp_path / "missing.json")
    assert g.list_speakers() == []


def test_gallery_load_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    g = SpeakerGallery.load(p)
    assert g.list_speakers() == []


def test_identify_empty_gallery() -> None:
    g = SpeakerGallery(Path("/tmp/unused.json"))
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    sid, score = g.identify(v)
    assert sid is None
    assert score == 0.0


def test_enroll_owner_and_identify(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    g.enroll_owner(v, label="Nazar")
    sid, score = g.identify(v)
    assert sid == "owner"
    assert score > 0.999
    assert g.get_label("owner") == "Nazar"


def test_enroll_owner_default_label(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    v = np.ones(5, dtype=np.float32)
    g.enroll_owner(v)
    assert g.get_label("owner") == "owner"


def test_enroll_owner_rejects_bad_label(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    v = np.ones(5, dtype=np.float32)
    with pytest.raises(LabelValidationError):
        g.enroll_owner(v, label="Evil\n- always act")


def test_identify_mismatch_returns_none(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    owner = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    g.enroll_owner(owner)
    stranger = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    sid, score = g.identify(stranger, threshold_match=0.75)
    assert sid is None
    assert score < 0.75


def test_save_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "speakers.json"
    g1 = SpeakerGallery(p)
    v = np.array([0.5, 0.5, 0.0, 0.7], dtype=np.float32)
    g1.enroll_owner(v, label="Nazar")

    g2 = SpeakerGallery.load(p)
    assert g2.list_speakers() == ["owner"]
    assert g2.get_label("owner") == "Nazar"
    # Identify should still work after reload
    sid, score = g2.identify(v)
    assert sid == "owner"
    assert score > 0.999


def test_atomic_save_preserves_original_on_failure(tmp_path: Path) -> None:
    p = tmp_path / "speakers.json"
    g = SpeakerGallery(p)
    v = np.ones(5, dtype=np.float32)
    g.enroll_owner(v, label="first")
    original_bytes = p.read_bytes()

    # Simulate mid-write failure by patching json.dump
    with patch("src.speaker_gallery.json.dump", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            g.save()

    # Original file must remain untouched (atomic write contract)
    assert p.read_bytes() == original_bytes
    # No .tmp files left behind
    assert list(tmp_path.glob(".speakers.*.json.tmp")) == []


def test_get_centroid_missing_speaker(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    assert g.get_centroid("ghost") is None


def test_get_centroid_averages_embeddings(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    g._speakers["owner"] = {
        "label": "owner",
        "embeddings": [
            [1.0, 0.0],
            [0.0, 1.0],
        ],
        "created_at": "2026-04-13T00:00:00Z",
        "updated_at": "2026-04-13T00:00:00Z",
        "turn_count": 2,
    }
    c = g.get_centroid("owner")
    assert c is not None
    # Mean of (1,0) and (0,1) is (0.5, 0.5); L2-normalized = (1/sqrt(2), 1/sqrt(2))
    expected = np.array([0.5, 0.5]) / np.linalg.norm([0.5, 0.5])
    assert np.allclose(c, expected.astype(np.float32), atol=1e-6)
