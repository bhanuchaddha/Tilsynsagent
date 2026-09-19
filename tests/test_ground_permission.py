"""The third write tool's permission surface.

The ban on writing 'ignore' was scoped, not loosened - file.py and escalate.py
still cannot write it, because they still have no way to justify it. Only
ground.py can, and only with a clause and a quote attached.
"""

from __future__ import annotations

import pytest

from tilsynsagent.actions.ground import ground_decision
from tilsynsagent.actions.permissions import (
    ESCALATE_PERMISSION,
    FILE_PERMISSION,
    GROUND_PERMISSION,
    PermissionDenied,
    RunBudget,
    check_outcome,
    check_table,
)


def test_only_the_ground_tool_may_write_ignore():
    assert "ignore" in GROUND_PERMISSION.allowed_outcomes
    for permission in (FILE_PERMISSION, ESCALATE_PERMISSION):
        with pytest.raises(PermissionDenied):
            check_outcome(permission, "ignore")


def test_the_ground_tool_may_not_escalate():
    """A grounding that cannot decide never calls this tool - it routes to
    escalate.py. One code path to a person, and it is the one that existed
    before grounding."""
    with pytest.raises(PermissionDenied):
        check_outcome(GROUND_PERMISSION, "escalate")


def test_the_ground_tool_may_write_groundings_and_filings():
    check_table(GROUND_PERMISSION, "groundings")
    check_table(GROUND_PERMISSION, "filings")
    with pytest.raises(PermissionDenied):
        check_table(GROUND_PERMISSION, "escalations")


def test_a_decision_without_a_clause_is_refused_before_any_write():
    """The check is here, before the write, rather than left to a scorer: a
    scorer tells you a bad decision was made; this stops it being recorded as
    a decision at all."""
    with pytest.raises(PermissionDenied, match="names the clause"):
        ground_decision(
            None,
            RunBudget(),
            sub_area_id=1,
            before_version_id=1,
            after_version_id=2,
            changed_fields={},
            rule=None,
            rule_set="v2",
            outcome="file",
            clause_id="",
            clause_quote="something",
            reasoning="r",
            citation_kind="clause",
            document_page_count=10,
        )


def test_a_decision_without_a_quote_is_refused_before_any_write():
    with pytest.raises(PermissionDenied, match="quotes it"):
        ground_decision(
            None,
            RunBudget(),
            sub_area_id=1,
            before_version_id=1,
            after_version_id=2,
            changed_fields={},
            rule=None,
            rule_set="v2",
            outcome="ignore",
            clause_id="6.2",
            clause_quote="   ",
            reasoning="r",
            citation_kind="clause",
            document_page_count=10,
        )


def test_a_field_citation_without_a_field_name_is_refused_before_any_write():
    """The citation kind stage 1 added must be gated as tightly as the one it
    joined - a decision resting on an unnamed field is untraceable."""
    with pytest.raises(PermissionDenied, match="names the field"):
        ground_decision(
            None,
            RunBudget(),
            sub_area_id=1,
            before_version_id=1,
            after_version_id=2,
            changed_fields={},
            rule=None,
            rule_set="v2",
            outcome="file",
            citation_kind="field",
            clause_id="",
            clause_quote="",
            field_name="",
            field_before="Forslag",
            field_after="Vedtaget",
            reasoning="r",
            document_page_count=10,
        )


def test_a_field_citation_without_any_value_is_refused_before_any_write():
    with pytest.raises(PermissionDenied, match="names the field"):
        ground_decision(
            None,
            RunBudget(),
            sub_area_id=1,
            before_version_id=1,
            after_version_id=2,
            changed_fields={},
            rule=None,
            rule_set="v2",
            outcome="file",
            citation_kind="field",
            clause_id="",
            clause_quote="",
            field_name="status",
            reasoning="r",
            document_page_count=10,
        )


def test_an_unknown_citation_kind_is_refused_before_any_write():
    """A decision that does not say what kind of source it rests on cannot be
    checked by either scorer, so it must not become a recorded decision."""
    with pytest.raises(PermissionDenied, match="citation_kind"):
        ground_decision(
            None,
            RunBudget(),
            sub_area_id=1,
            before_version_id=1,
            after_version_id=2,
            changed_fields={},
            rule=None,
            rule_set="v2",
            outcome="file",
            citation_kind="",
            clause_id="6.2",
            clause_quote="q",
            reasoning="r",
            document_page_count=10,
        )


def test_an_outcome_outside_the_permission_is_refused_first():
    with pytest.raises(PermissionDenied):
        ground_decision(
            None,
            RunBudget(),
            sub_area_id=1,
            before_version_id=1,
            after_version_id=2,
            changed_fields={},
            rule=None,
            rule_set="v2",
            outcome="escalate",
            clause_id="6.2",
            clause_quote="q",
            reasoning="r",
            citation_kind="clause",
            document_page_count=10,
        )


def test_the_permission_comment_states_why_ignore_became_writable():
    """The comment was edited with a stated reason rather than silently
    loosened. If someone deletes that reasoning, this fails."""
    import inspect

    from tilsynsagent.actions import permissions

    source = inspect.getsource(permissions)
    assert "positive claim" in source
    assert "not loosened" in source or "scoped" in source
