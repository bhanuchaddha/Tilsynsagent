"""The escalate write tool: records a diff as an escalation and identifies
the LangGraph thread paused on it.

Declared surface: writes only to diffs (setting outcome) and escalations.
Only ever writes outcome='escalate'. Called both for rule-decided escalations
(R4/R5/R6/R7) and for cases the LLM assessment step evaluated after
rules.engine.apply_rules returned NOT_COVERED.

This tool does not call interrupt() itself - that happens in the graph node
that calls it, since interrupt() must run inside the graph's execution to
pause correctly. This function only performs the write; see graph.py for the
node that pauses the run after this returns.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from tilsynsagent.actions.permissions import (
    ESCALATE_PERMISSION,
    RunBudget,
    check_outcome,
    check_table,
)
from tilsynsagent.db import repo


@dataclass(frozen=True)
class EscalateResult:
    diff_id: int
    escalation_id: int


def escalate_decision(
    conn: psycopg.Connection,
    budget: RunBudget,
    *,
    sub_area_id: int,
    before_version_id: int | None,
    after_version_id: int,
    changed_fields: dict,
    rule: str | None,
    rule_set: str,
    thread_id: str,
    what_is_unclear: str,
    what_a_person_must_decide: str | None,
    citation: str,
) -> EscalateResult:
    """Writes a diff with outcome='escalate' and its escalation record.
    Refuses before any write happens if the call falls outside this tool's
    declared permission or its run budget."""
    check_outcome(ESCALATE_PERMISSION, "escalate")
    check_table(ESCALATE_PERMISSION, "escalations")
    budget.check_and_record(ESCALATE_PERMISSION)

    diff_id = repo.insert_diff(
        conn,
        sub_area_id=sub_area_id,
        before_version_id=before_version_id,
        after_version_id=after_version_id,
        changed_fields=changed_fields,
        outcome="escalate",
        rule=rule,
        rule_set=rule_set,
    )
    escalation_id = repo.insert_escalation(
        conn,
        diff_id=diff_id,
        thread_id=thread_id,
        what_is_unclear=what_is_unclear,
        what_a_person_must_decide=what_a_person_must_decide,
        citation=citation,
    )
    return EscalateResult(diff_id=diff_id, escalation_id=escalation_id)
