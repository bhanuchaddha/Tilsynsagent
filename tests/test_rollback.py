"""Rollback mechanics, offline.

The claim this protects is "a prompt rollback has been executed and timed",
so the thing worth testing is that the timing is honest and that an
unverified rollback cannot be reported as a successful one.
"""

from __future__ import annotations

from tilsynsagent.llm import rollback as rb
from tilsynsagent.llm.prompts import CACHE_TTL_SECONDS


def _result(**overrides) -> rb.RollbackResult:
    kwargs = {
        "prompt_name": "tilsynsagent-summarise-system",
        "from_version": 2,
        "to_version": 1,
        "label_move_seconds": 0.09,
        "verified_version": 1,
    }
    kwargs.update(overrides)
    return rb.RollbackResult(**kwargs)


def test_a_rollback_that_took_effect_is_verified():
    assert _result().verified


def test_a_rollback_that_did_not_take_effect_is_not_verified():
    """A label move that returns quickly but leaves the old version serving
    is not a fast rollback, it is a failed one. Reporting the elapsed time
    without this check would publish a number that means nothing."""
    result = _result(verified_version=2)
    assert not result.verified
    assert "NOT VERIFIED" in result.summary()


def test_propagation_time_includes_the_prompt_cache():
    """The label move is near-instant; what a caller experiences is bounded
    by the prompt cache TTL, because a running process serves its cached copy
    until that expires. Publishing only the label-move time would be true and
    misleading."""
    result = _result(label_move_seconds=0.09)
    assert result.worst_case_propagation_seconds == 0.09 + CACHE_TTL_SECONDS
    assert result.worst_case_propagation_seconds > result.label_move_seconds


def test_summary_states_both_numbers():
    summary = _result().summary()
    assert "0.09s" in summary
    assert str(CACHE_TTL_SECONDS) in summary
    assert "v2 -> v1" in summary
