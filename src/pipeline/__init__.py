"""Pipecat pipeline composition.

Re-exports the public surface of :mod:`src.pipeline.build` so callers can
write ``from src.pipeline import build_pipeline`` without reaching into
the submodule.
"""
from .build import (
    _assemble_native_stages,
    _build_system_prompt,
    _wire_language_state,
    build_pipeline,
)

__all__ = [
    "build_pipeline",
    "_assemble_native_stages",
    "_build_system_prompt",
    "_wire_language_state",
]
