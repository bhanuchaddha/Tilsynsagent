"""Shared fixtures. Loads .env for the whole suite the same way runner.py's
main() does, then provides fixtures that deliberately blind individual tests
to real credentials rather than assuming they're absent - a developer's local
.env is loaded by test_actions_integration.py's module-level load_dotenv()
regardless of what this file does, so offline tests must monkeypatch keys
*out*, not just skip setting them.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture
def no_langfuse_env(monkeypatch):
    """Removes Langfuse keys for the duration of the test, regardless of
    whether a real .env set them."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)


@pytest.fixture
def fake_groq_client():
    """A Groq-shaped client double: .chat.completions.create(...) returns a
    response whose .choices[0].message.content and .usage are pre-set. Used
    to exercise assess()/summarise() without a network call, exploiting the
    injectable `client` parameter both functions already accept."""

    def _make(content: str, prompt_tokens=100, completion_tokens=50, total_tokens=150):
        client = MagicMock()
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
        )
        client.chat.completions.create.return_value = response
        return client

    return _make
