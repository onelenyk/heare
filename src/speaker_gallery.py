"""Owner-centric speaker gallery with atomic persistence + label sanitization.

Phase 1 is owner-only: the gallery holds a single reference vector under
the 'owner' id, and identify() returns ('owner', cos) or (None, cos).
Phase 2 will add candidate tracking, auto-enrollment, centroid drift
guards, and an asyncio.Lock around mutations.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


logger = logging.getLogger("heare.speaker_gallery")


LABEL_MAX_LEN = 32
# `\x00-\x1f` already covers tab, LF, CR.
LABEL_FORBIDDEN = re.compile(r"[{}<>\x00-\x1f]")

# Auto-append FIFO cap for the PERSISTENT gallery (speakers.json). Keeps the
# enrollment reference at index 0 and rotates the rest. Only high-confidence
# matches (threshold_match + margin) feed this list — it's long-term memory.
AUTO_ENROLL_CAP = 20
# Anti-drift guard for the persistent gallery: reject appends whose cosine
# against the current centroid is below this floor.
AUTO_ENROLL_MIN_COS = 0.55

# Session-local FIFO cap. This is short-term memory — in-memory only, never
# persisted, forgotten on daemon restart. Fed by EVERY successful owner
# match so the gallery adapts rapidly to the current room/mic/ambient.
SESSION_REFS_CAP = 10


class LabelValidationError(ValueError):
    pass


def sanitize_label(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned:
        raise LabelValidationError("label cannot be empty")
    if len(cleaned) > LABEL_MAX_LEN:
        raise LabelValidationError(
            f"label exceeds {LABEL_MAX_LEN} characters"
        )
    if LABEL_FORBIDDEN.search(cleaned):
        raise LabelValidationError(
            "label contains forbidden characters (newline, control, braces, angle brackets)"
        )
    return cleaned


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SpeakerGallery:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._speakers: dict[str, dict[str, Any]] = {}
        # In-memory session refs — rapid adaptation to the current acoustic
        # environment. Never persisted; forgotten on restart.
        self._session_refs: dict[str, list[np.ndarray]] = {}

    @classmethod
    def load(cls, path: Path) -> SpeakerGallery:
        g = cls(path)
        if not path.exists():
            return g
        try:
            data = json.loads(path.read_text())
            g._speakers = data.get("speakers", {}) or {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("gallery load failed at %s: %s — starting empty", path, e)
            g._speakers = {}
        return g

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".speakers.", suffix=".json.tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"version": 1, "speakers": self._speakers}, f, indent=2)
            os.replace(tmp_name, self.path)
        except Exception:
            # Leave original file alone; clean up the temp.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def list_speakers(self) -> list[str]:
        return sorted(self._speakers.keys())

    def identify(
        self, v: np.ndarray, threshold_match: float = 0.75
    ) -> tuple[str | None, float]:
        """Max-over-refs: match if v is close to ANY enrolled example.

        Considers both the persistent gallery (speakers.json) and any
        in-memory session refs registered via `register_session_ref`.
        Adding more refs can only raise the max score, so session refs
        are a pure tailwind — they catch drifted marginal turns without
        polluting the persistent store.
        """
        if not self._speakers and not self._session_refs:
            return None, 0.0
        v_norm = float(np.linalg.norm(v)) + 1e-12
        best_id: str | None = None
        best_score = -1.0
        all_sids = set(self._speakers.keys()) | set(self._session_refs.keys())
        for sid in all_sids:
            refs_list: list[np.ndarray] = []
            entry = self._speakers.get(sid)
            if entry:
                persistent = entry.get("embeddings") or []
                if persistent:
                    refs_list.append(np.asarray(persistent, dtype=np.float32))
            session = self._session_refs.get(sid) or []
            if session:
                refs_list.append(np.asarray(session, dtype=np.float32))
            if not refs_list:
                continue
            refs = np.vstack(refs_list)
            ref_norms = np.linalg.norm(refs, axis=1) + 1e-12
            scores = (refs @ v) / (ref_norms * v_norm)
            score = float(scores.max())
            if score > best_score:
                best_score = score
                best_id = sid
        if best_score >= threshold_match:
            return best_id, best_score
        return None, max(best_score, 0.0)

    def enroll_owner(self, v: np.ndarray, label: str = "owner") -> None:
        clean = sanitize_label(label)
        now = _iso_now()
        self._speakers["owner"] = {
            "label": clean,
            "embeddings": [v.astype(np.float32).tolist()],
            "created_at": now,
            "updated_at": now,
            "turn_count": 1,
        }
        self.save()

    def append_reference(
        self,
        speaker_id: str,
        v: np.ndarray,
        cap: int = AUTO_ENROLL_CAP,
        min_cos: float = AUTO_ENROLL_MIN_COS,
    ) -> bool:
        """Append v as an additional reference for speaker_id.

        FIFO-bounded by `cap` with the enrollment reference (index 0)
        always preserved. Rejects the append if the new vector's cosine
        against the current centroid falls below `min_cos` (anti-drift).
        Persists on success. Returns True if appended, False if rejected
        or speaker unknown.
        """
        entry = self._speakers.get(speaker_id)
        if not entry:
            return False
        centroid = self.get_centroid(speaker_id)
        if centroid is not None:
            v_norm = float(np.linalg.norm(v)) + 1e-12
            cos = float(np.dot(centroid, v) / v_norm)
            if cos < min_cos:
                return False
        embeddings: list[list[float]] = list(entry.get("embeddings") or [])
        embeddings.append(v.astype(np.float32).tolist())
        if len(embeddings) > cap:
            # Keep the original enrollment reference + the most recent
            # (cap - 1) samples. Drops the oldest rolled-in refs.
            embeddings = [embeddings[0]] + embeddings[-(cap - 1) :]
        entry["embeddings"] = embeddings
        entry["updated_at"] = _iso_now()
        entry["turn_count"] = int(entry.get("turn_count", 0)) + 1
        self.save()
        return True

    def register_session_ref(
        self,
        speaker_id: str,
        v: np.ndarray,
        cap: int = SESSION_REFS_CAP,
    ) -> None:
        """Add v to the in-memory session ref list for speaker_id.

        No anti-drift guard here — the caller is expected to only register
        vectors that already identified as speaker_id (i.e. already cleared
        threshold_match against some existing ref). FIFO-bounded by `cap`.
        """
        refs = self._session_refs.setdefault(speaker_id, [])
        refs.append(v.astype(np.float32))
        if len(refs) > cap:
            # Drop oldest session refs, keep the most recent `cap`.
            self._session_refs[speaker_id] = refs[-cap:]

    def session_ref_count(self, speaker_id: str) -> int:
        return len(self._session_refs.get(speaker_id, []))

    def clear_session_refs(self) -> None:
        self._session_refs.clear()

    def get_centroid(self, speaker_id: str) -> np.ndarray | None:
        entry = self._speakers.get(speaker_id)
        if not entry:
            return None
        embeddings = entry.get("embeddings") or []
        if not embeddings:
            return None
        arr = np.asarray(embeddings, dtype=np.float32)
        mean = arr.mean(axis=0)
        norm = np.linalg.norm(mean) + 1e-12
        return mean / norm

    def get_label(self, speaker_id: str) -> str | None:
        entry = self._speakers.get(speaker_id)
        if not entry:
            return None
        return entry.get("label")
