"""The ground write tool: records a decision the agent made by reading the
source document, and the clause that justified it.

Declared surface: writes to diffs (setting outcome), groundings, and - for a
grounded *file* - filings. Only ever writes outcome 'file' or 'ignore'.

**This tool refuses to write a decision it cannot evidence.** A grounded
outcome without a clause id and a verbatim quote is exactly the untraceable
autonomous decision CLAUDE.md's one rule forbids, so the check happens here,
before the write, rather than being left to a scorer to notice afterwards. A
scorer tells you a bad decision was made; this stops it being recorded as a
decision at all - the record escalates instead.

**A grounded ignore writes no second row, and that is the point.** Its entire
record is the groundings row: the clause, the quote, the reasoning. Before
grounding existed, an ignore was the absence of a write and there was nothing
to inspect. Now it is the most heavily evidenced outcome in the system.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from tilsynsagent.actions.permissions import (
    GROUND_PERMISSION,
    PermissionDenied,
    RunBudget,
    check_outcome,
    check_table,
)
from tilsynsagent.db import repo


@dataclass(frozen=True)
class GroundResult:
    diff_id: int
    grounding_id: int
    # Set only for a grounded file. A grounded ignore has no filing, by
    # design - see the module docstring.
    filing_id: int | None = None


def ground_decision(
    conn: psycopg.Connection,
    budget: RunBudget,
    *,
    sub_area_id: int,
    before_version_id: int | None,
    after_version_id: int,
    changed_fields: dict,
    rule: str | None,
    rule_set: str,
    outcome: str,
    clause_id: str,
    clause_quote: str,
    reasoning: str,
    document_page_count: int | None,
    retrieved_clause_ids: list[str],
    summary: str | None = None,
    citation: str = "",
) -> GroundResult:
    """Writes a grounded decision and the clause evidence behind it.

    Refuses before any write if the outcome is outside this tool's
    permission, if the run is at budget, or if the evidence is missing.
    """
    check_outcome(GROUND_PERMISSION, outcome)
    check_table(GROUND_PERMISSION, "groundings")
    if not clause_id.strip() or not clause_quote.strip():
        raise PermissionDenied(
            "ground may only write a decision that cites a clause and quotes it: "
            f"got clause_id={clause_id!r}, clause_quote={clause_quote!r}. A grounded "
            "outcome without both is untraceable and must escalate instead."
        )
    budget.check_and_record(GROUND_PERMISSION)

    diff_id = repo.insert_diff(
        conn,
        sub_area_id=sub_area_id,
        before_version_id=before_version_id,
        after_version_id=after_version_id,
        changed_fields=changed_fields,
        outcome=outcome,
        rule=rule,
        rule_set=rule_set,
    )
    grounding_id = repo.insert_grounding(
        conn,
        diff_id=diff_id,
        clause_id=clause_id,
        clause_quote=clause_quote,
        reasoning=reasoning,
        outcome=outcome,
        document_page_count=document_page_count,
        retrieved_clause_ids=retrieved_clause_ids,
    )

    filing_id = None
    if outcome == "file":
        check_table(GROUND_PERMISSION, "filings")
        filing_id = repo.insert_filing(
            conn,
            diff_id=diff_id,
            # The clause is the summary's substance: a filing a person reads
            # should say what the document said, not restate the register
            # fields they can already see.
            summary=summary or f"Clause {clause_id}: {clause_quote}\n\n{reasoning}",
            citation=citation,
        )

    return GroundResult(diff_id=diff_id, grounding_id=grounding_id, filing_id=filing_id)
