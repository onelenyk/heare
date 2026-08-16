"""Neutral, dependency-free text utilities shared across layers.

Nothing under ``src.text`` imports from any other ``src`` package, and
nothing outside it needs to be imported for it to work — that is the
point of the package. Modules that would otherwise create a cycle by
reaching from a lower layer into a higher one (e.g. the memory
extractor needing spine's speech-hallucination filter) belong here
instead, at the bottom of the dependency graph where both sides can
import down to them.
"""
from __future__ import annotations
