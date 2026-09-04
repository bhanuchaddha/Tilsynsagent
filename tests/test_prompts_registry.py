"""The prompt registry's contract: a run never fails because Langfuse is
unavailable, and the version that produced a decision is always recoverable.

These are the properties the rollback story depends on. If the fallback path
breaks, the offline test suite and CI stop working; if the recorded version
is wrong, "which prompt produced this filing?" gets an answer that is worse
than no answer.
"""

from __future__ import annotations

import pytest

from tilsynsagent.llm import prompts


@pytest.fixture(autouse=True)
def _clear_recorded_prompt():
    prompts.take_last_prompt()
    yield
    prompts.take_last_prompt()


def test_falls_back_when_observability_disabled(monkeypatch):
    monkeypatch.setattr(prompts.obs, "observability_enabled", lambda: False)
    resolved = prompts.get_prompt(prompts.ASSESS_PROMPT_NAME)
    assert resolved.source == "fallback"
    assert resolved.version is None
    assert resolved.text == prompts.FALLBACKS[prompts.ASSESS_PROMPT_NAME]


def test_falls_back_when_langfuse_raises(monkeypatch):
    """A Langfuse outage must not stop the agent deciding. An observability
    vendor being down is not a reason to stop making traceable decisions -
    it degrades to the pinned prompt and says so."""
    monkeypatch.setattr(prompts.obs, "observability_enabled", lambda: True)

    class _Boom:
        def get_prompt(self, *a, **k):
            raise RuntimeError("langfuse unreachable")

    monkeypatch.setattr("langfuse.get_client", lambda: _Boom())
    resolved = prompts.get_prompt(prompts.SUMMARISE_PROMPT_NAME)
    assert resolved.source == "fallback"
    assert resolved.version is None
    assert resolved.text == prompts.FALLBACKS[prompts.SUMMARISE_PROMPT_NAME]


def test_uses_langfuse_version_when_available(monkeypatch):
    monkeypatch.setattr(prompts.obs, "observability_enabled", lambda: True)

    class _Prompt:
        prompt = "fetched text"
        version = 7
        is_fallback = False

    class _Client:
        def get_prompt(self, name, **k):
            return _Prompt()

    monkeypatch.setattr("langfuse.get_client", lambda: _Client())
    resolved = prompts.get_prompt(prompts.ASSESS_PROMPT_NAME)
    assert resolved.source == "langfuse"
    assert resolved.version == 7
    assert resolved.text == "fetched text"
    assert resolved.label == f"{prompts.ASSESS_PROMPT_NAME}@v7"


def test_sdk_served_fallback_is_reported_as_fallback(monkeypatch):
    """The SDK can serve our own fallback text (prompt missing upstream) and
    still return an object. Reporting that as a version number would be a
    lie on the trace, so is_fallback wins over whatever version says."""
    monkeypatch.setattr(prompts.obs, "observability_enabled", lambda: True)

    class _Prompt:
        prompt = prompts.FALLBACKS[prompts.ASSESS_PROMPT_NAME]
        version = 0
        is_fallback = True

    class _Client:
        def get_prompt(self, name, **k):
            return _Prompt()

    monkeypatch.setattr("langfuse.get_client", lambda: _Client())
    resolved = prompts.get_prompt(prompts.ASSESS_PROMPT_NAME)
    assert resolved.source == "fallback"
    assert resolved.version is None


def test_unknown_prompt_name_is_a_programming_error():
    """Unlike a Langfuse failure, an unknown name is a bug in this repo and
    must surface rather than silently resolving to something."""
    with pytest.raises(KeyError):
        prompts.get_prompt("no-such-prompt")


def test_record_and_take_round_trip():
    resolved = prompts.ResolvedPrompt("n", "t", 3, "langfuse")
    prompts.record_last_prompt(resolved)
    assert prompts.take_last_prompt() is resolved
    assert prompts.take_last_prompt() is None, "take must clear, or a later case reuses it"


def test_fallback_label_names_no_version():
    assert prompts.ResolvedPrompt("n", "t", None, "fallback").label == "n@fallback"


# --- regression: the citation-fidelity failure the 2026-08-29 baseline found


def test_summarise_fallback_demands_the_literal_url():
    """The 2026-08-29 baseline recorded citation_fidelity at 0.704 because
    the model paraphrased its citation ("as documented in the plan PDF")
    rather than quoting the doklink. The repair was prompt-side: an explicit,
    non-negotiable instruction to copy the URL character for character.

    This pins the *pinned fallback* specifically, which is the text used when
    Langfuse is down - the path least likely to be exercised by hand and so
    the one most likely to silently regress. A registry edit that drops the
    requirement upstream is caught by the eval gate instead.
    """
    text = prompts.FALLBACKS[prompts.SUMMARISE_PROMPT_NAME]
    assert "character for character" in text
    assert "Never replace" in text


def test_summarise_fallback_demands_before_and_after_values():
    """The other half of the same repair, and the counterweight to it: a
    summary that cites perfectly but states no values scores a hollow pass on
    citation_fidelity while failing states_both_values."""
    assert "before value and the after value" in prompts.FALLBACKS[prompts.SUMMARISE_PROMPT_NAME]
