"""Heare overlay UI — always-on-top window over the running daemon.

See ``.omc/plan-overlay-ui.md`` for the architecture rationale. The
overlay is a localhost-only client of the existing file-based observer
contract (voice_state.json, audio_event.json, mute flags) — no new
transport, no auth, no remote access.
"""
