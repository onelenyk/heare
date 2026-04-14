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


def test_identify_uses_max_over_refs_not_centroid(tmp_path: Path) -> None:
    """A vector that matches ONE reference should identify as owner even
    when it's far from the centroid — the key property of max-over-refs.
    """
    g = SpeakerGallery(tmp_path / "speakers.json")
    # Two orthogonal references; centroid is their L2-normalized mean
    # (45 degrees off each ref). A vector aligned with ref #1 has cosine
    # ~1.0 to that ref but only ~0.707 to the centroid — max-over-refs
    # correctly returns 1.0 where centroid-only would return 0.707.
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
    v = np.array([1.0, 0.0], dtype=np.float32)
    sid, score = g.identify(v, threshold_match=0.9)
    assert sid == "owner"
    assert score == pytest.approx(1.0, abs=1e-6)


def test_append_reference_rejects_unknown_speaker(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    v = np.array([1.0, 0.0], dtype=np.float32)
    assert g.append_reference("ghost", v) is False


def test_append_reference_grows_gallery_and_persists(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    v0 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    g.enroll_owner(v0, label="owner")
    assert len(g._speakers["owner"]["embeddings"]) == 1

    v1 = np.array([0.95, 0.31, 0.0], dtype=np.float32)  # ~0.95 to v0
    assert g.append_reference("owner", v1) is True
    assert len(g._speakers["owner"]["embeddings"]) == 2

    # Persists on disk — reloading picks up both refs
    g2 = SpeakerGallery.load(tmp_path / "speakers.json")
    assert len(g2._speakers["owner"]["embeddings"]) == 2


def test_append_reference_anti_drift_rejects_low_cosine(tmp_path: Path) -> None:
    """A vector roughly orthogonal to the existing centroid (cos ~0) must
    be rejected by the anti-drift guard even though the API was called.
    """
    g = SpeakerGallery(tmp_path / "speakers.json")
    g.enroll_owner(np.array([1.0, 0.0, 0.0], dtype=np.float32), label="owner")
    stranger = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert g.append_reference("owner", stranger) is False
    # Gallery untouched
    assert len(g._speakers["owner"]["embeddings"]) == 1


def test_register_session_ref_is_considered_by_identify(tmp_path: Path) -> None:
    """A session ref should count as an anchor for identify() — a query
    that only matches the session ref (not the persistent ones) should
    still return the speaker id.
    """
    g = SpeakerGallery(tmp_path / "speakers.json")
    # Enroll with one persistent ref along axis 0
    g.enroll_owner(np.array([1.0, 0.0, 0.0], dtype=np.float32), label="owner")
    # Register a session ref along axis 1 (orthogonal to the persistent one)
    g.register_session_ref("owner", np.array([0.0, 1.0, 0.0], dtype=np.float32))
    # A query aligned with the session ref should match owner
    v = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    sid, score = g.identify(v, threshold_match=0.90)
    assert sid == "owner"
    assert score == pytest.approx(1.0, abs=1e-6)


def test_register_session_ref_fifo_cap(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    g.enroll_owner(np.array([1.0, 0.0], dtype=np.float32), label="owner")
    for i in range(20):
        g.register_session_ref(
            "owner", np.array([1.0, 0.01 * i], dtype=np.float32), cap=5
        )
    assert g.session_ref_count("owner") == 5


def test_session_refs_not_persisted(tmp_path: Path) -> None:
    """Session refs live only in memory — a fresh load from disk does
    NOT carry them over.
    """
    g = SpeakerGallery(tmp_path / "speakers.json")
    g.enroll_owner(np.array([1.0, 0.0], dtype=np.float32), label="owner")
    g.register_session_ref("owner", np.array([0.0, 1.0], dtype=np.float32))
    assert g.session_ref_count("owner") == 1

    # Reload from disk — session refs must be gone
    g2 = SpeakerGallery.load(tmp_path / "speakers.json")
    assert g2.session_ref_count("owner") == 0
    assert len(g2._speakers["owner"]["embeddings"]) == 1


def test_clear_session_refs(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    g.enroll_owner(np.array([1.0, 0.0], dtype=np.float32), label="owner")
    g.register_session_ref("owner", np.array([0.0, 1.0], dtype=np.float32))
    assert g.session_ref_count("owner") == 1
    g.clear_session_refs()
    assert g.session_ref_count("owner") == 0


def test_append_reference_fifo_cap_preserves_enrollment_ref(
    tmp_path: Path,
) -> None:
    """When more than `cap` refs are appended, the oldest rotated refs are
    dropped but the enrollment reference at index 0 is always preserved.
    """
    g = SpeakerGallery(tmp_path / "speakers.json")
    enrollment = np.array([1.0, 0.0], dtype=np.float32)
    g.enroll_owner(enrollment, label="owner")

    # Append 10 near-identical refs; cap the gallery at 5 for the test
    for i in range(10):
        v = np.array([1.0, 0.01 * (i + 1)], dtype=np.float32)
        v = v / np.linalg.norm(v)
        assert g.append_reference("owner", v.astype(np.float32), cap=5) is True

    embeddings = g._speakers["owner"]["embeddings"]
    assert len(embeddings) == 5
    # Enrollment ref must still be at index 0
    assert np.allclose(
        np.array(embeddings[0], dtype=np.float32), enrollment, atol=1e-6
    )


# ---------------------------------------------------------------------------
# SPK-A2: remove_speaker + rename_speaker
# ---------------------------------------------------------------------------


def test_remove_speaker_success(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    g.enroll_owner(np.ones(5, dtype=np.float32))
    assert g.remove_speaker("owner") is True
    assert g.list_speakers() == []
    g2 = SpeakerGallery.load(tmp_path / "speakers.json")
    assert g2.list_speakers() == []


def test_remove_speaker_missing(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    assert g.remove_speaker("ghost") is False


def test_rename_speaker_success(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    g.enroll_owner(np.ones(5, dtype=np.float32), label="owner")
    assert g.rename_speaker("owner", "Nazar") is True
    assert g.get_label("owner") == "Nazar"
    g2 = SpeakerGallery.load(tmp_path / "speakers.json")
    assert g2.get_label("owner") == "Nazar"


def test_rename_speaker_invalid_label(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    g.enroll_owner(np.ones(5, dtype=np.float32))
    with pytest.raises(LabelValidationError):
        g.rename_speaker("owner", "bad\nlabel")


def test_rename_speaker_missing(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    assert g.rename_speaker("ghost", "whatever") is False


# ---------------------------------------------------------------------------
# SPK-A3: audit() drift report
# ---------------------------------------------------------------------------


def test_audit_single_ref(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    g.enroll_owner(v)
    report = g.audit("owner")
    assert report is not None
    assert report["ref_count"] == 1
    assert report["min_cos_vs_centroid"] == pytest.approx(1.0, abs=1e-3)
    assert report["mean_cos_vs_centroid"] == pytest.approx(1.0, abs=1e-3)
    assert report["max_cos_vs_centroid"] == pytest.approx(1.0, abs=1e-3)
    assert report["mean_cos_vs_enrollment"] == pytest.approx(1.0, abs=1e-3)
    assert report["enrollment_cos_floor_hit"] is False


def test_audit_multi_ref_healthy(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    base = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    g.enroll_owner(base)
    for i in range(3):
        delta = np.array([0.0, 0.01 * (i + 1), 0.0], dtype=np.float32)
        v = base + delta
        v = v / np.linalg.norm(v)
        g.append_reference("owner", v.astype(np.float32))
    report = g.audit("owner")
    assert report is not None
    assert report["ref_count"] == 4
    assert report["mean_cos_vs_centroid"] > 0.95
    assert report["enrollment_cos_floor_hit"] is False


def test_audit_detects_drift(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    base = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    g.enroll_owner(base)
    # Directly poke the internal state to simulate a drifted gallery.
    # Real-world append_reference would reject these via anti-drift guard.
    drifted = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    g._speakers["owner"]["embeddings"].append(drifted.tolist())
    g._speakers["owner"]["embeddings"].append(drifted.tolist())
    report = g.audit("owner")
    assert report is not None
    assert report["enrollment_cos_floor_hit"] is True
    assert report["mean_cos_vs_enrollment"] < 0.55


def test_audit_missing_returns_none(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    assert g.audit("ghost") is None


# ---------------------------------------------------------------------------
# SPK-A4: enroll_guest auto-enrollment API
# ---------------------------------------------------------------------------


def test_enroll_guest_assigns_next_id(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    g.enroll_owner(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    v1 = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    v2 = np.array([0.0, 0.9, 0.1], dtype=np.float32)
    v3 = np.array([0.0, 0.8, 0.2], dtype=np.float32)
    assert g.enroll_guest(v1) == "guest_01"
    assert g.enroll_guest(v2) == "guest_02"
    assert g.enroll_guest(v3) == "guest_03"
    assert "owner" in g.list_speakers()
    assert sorted(g.list_speakers()) == ["guest_01", "guest_02", "guest_03", "owner"]


def test_enroll_guest_persists(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    v = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    guest_id = g.enroll_guest(v)
    assert guest_id == "guest_01"
    g2 = SpeakerGallery.load(tmp_path / "speakers.json")
    assert "guest_01" in g2.list_speakers()
    assert g2.get_label("guest_01") == "guest_01"


def test_enroll_guest_max_cap(tmp_path: Path) -> None:
    g = SpeakerGallery(tmp_path / "speakers.json")
    v = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert g.enroll_guest(v, max_guests=2) == "guest_01"
    assert g.enroll_guest(v, max_guests=2) == "guest_02"
    # Third call hits the cap — empty string + gallery unchanged
    assert g.enroll_guest(v, max_guests=2) == ""
    assert sorted(g.list_speakers()) == ["guest_01", "guest_02"]
