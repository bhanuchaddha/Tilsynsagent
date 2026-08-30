"""Token accounting, kept separate from pricing (llm/pricing.py).

Usage is a fact about a call - how many tokens it consumed. Cost is a
judgement applied to that fact using a price that changes over time and that
Groq publishes no machine-readable feed for. Keeping them apart means a price
correction never requires re-deriving what a call actually used.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

# Set by assess()/summarise() after each Groq call, read by graph.py's nodes
# immediately afterward. A contextvar rather than a return-value change keeps
# assess()/summarise()'s signatures exactly as they were before Phase 2 -
# callers that only want the Assessment/Summary need no changes at all.
_last_usage: contextvars.ContextVar[Usage | None] = contextvars.ContextVar(
    "_last_usage", default=None
)


def take_last_usage() -> Usage | None:
    """Reads and clears the most recently recorded Usage, or None if no LLM
    call has happened in this context since the last read."""
    usage = _last_usage.get()
    _last_usage.set(None)
    return usage


@dataclass(frozen=True)
class Usage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float | None = None

    @classmethod
    def from_groq(cls, resp: Any, *, model: str, latency_ms: float | None = None) -> Usage:
        """Builds a Usage from a Groq chat-completion response.

        ``resp.usage`` is the only place token counts exist - assess() and
        summarise() otherwise discard the response after reading
        ``.choices[0].message.content``.
        """
        usage = resp.usage
        return cls(
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            latency_ms=latency_ms,
        )

    def record(self) -> None:
        """Publishes this Usage for the next take_last_usage() call."""
        _last_usage.set(self)

    def to_dict(self) -> dict:
        """Plain-dict form for GraphState, which the Postgres checkpointer
        serializes via msgpack - see graph.py's note on GraphState.record."""
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
        }
