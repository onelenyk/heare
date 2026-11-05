"""What a model provider needs on the wire beyond its key.

opencode's Go gateway routes a call by its caller's session: 5 November
2026, every call without one came back 400 MissingSessionID and the brain
fell silent. One per process — the brain's turns, or one part's — is what
it asks for. Shared by the brain and by the parts' own model (heare.talk).
"""

from __future__ import annotations

import uuid

SESSION = f"heare-{uuid.uuid4().hex[:16]}"


def routed(base_url: str) -> dict[str, str]:
    """The headers a provider needs beyond the key, by its address."""
    return {"x-opencode-session": SESSION} if "opencode.ai" in base_url else {}
