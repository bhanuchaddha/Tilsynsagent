"""The ground write tool: records a decision the agent made by reading the
source document, and the source that justified it.

Declared surface: writes to diffs (setting outcome), groundings, and - for a
grounded *file* - filings. Only ever writes outcome 'file' or 'ignore'.

**This tool refuses to write a decision it cannot evidence.** A grounded
outcome without a complete citation is exactly the untraceable autonomous
decision CLAUDE.md's one rule forbids, so the check happens here, before the
write, rather than being left to a scorer to notice afterwards. A scorer
tells you a bad decision was made; this stops it being recorded as a decision
at all - the record escalates instead.

**Two kinds of citation, one bar.** A decision rests on a quoted clause or on
a named register field, never on neither and never on half of one. A field
citation is not the weaker option: "status changed from F to V" is checkable
against the record by code, exactly as a quote is checkable against the
document. What is refused is an *incomplete* citation of either kind.

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


def _source_line(
    citation_kind: str,
    clause_id: str,
    clause_quote: str,
    field_name: str,
    field_before: str,
    field_after: str,
) -> str:
    """The opening line of a filing summary: what this decision rested on.

    A person reading a filing should see the source before the reasoning,
    and should be able to check it without opening anything else. For a
    clause that means the number and the quote; for a field, the name and
    both values.
    """
    if citation_kind == "field":
        return (
            f"Register field {field_name}: "
            f"{field_before or '(none)'} -> {field_after or '(none)'}"
        )
    return f"Clause {clause_id}: {clause_quote}"


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
    citation_kind: str,
    clause_id: str,
    clause_quote: str,
    field_name: str = "",
    field_before: str = "",
    field_after: str = "",
    reasoning: str,
    document_page_count: int | None,
    document_chars: int | None = None,
    summary: str | None = None,
    citation: str = "",
) -> GroundResult:
    """Writes a grounded decision and the evidence behind it.

    Refuses before any write if the outcome is outside this tool's
    permission, if the run is at budget, or if the citation is incomplete.
    """
    check_outcome(GROUND_PERMISSION, outcome)
    check_table(GROUND_PERMISSION, "groundings")
    if citation_kind == "clause":
        if not clause_id.strip() or not clause_quote.strip():
            raise PermissionDenied(
                "ground may only write a clause-cited decision that names the clause "
                f"and quotes it: got clause_id={clause_id!r}, "
                f"clause_quote={clause_quote!r}. A grounded outcome without both is "
                "untraceable and must escalate instead."
            )
    elif citation_kind == "field":
        if not field_name.strip() or not (field_before.strip() or field_after.strip()):
            raise PermissionDenied(
                "ground may only write a field-cited decision that names the field and "
                f"at least one side of its change: got field_name={field_name!r}, "
                f"field_before={field_before!r}, field_after={field_after!r}. A grounded "
                "outcome without them is untraceable and must escalate instead."
            )
    else:
        raise PermissionDenied(
            f"ground requires citation_kind 'clause' or 'field', got {citation_kind!r}. "
            "A decision that does not say what kind of source it rests on cannot be "
            "checked, and must escalate instead."
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
        citation_kind=citation_kind,
        clause_id=clause_id,
        clause_quote=clause_quote,
        field_name=field_name,
        field_before=field_before,
        field_after=field_after,
        reasoning=reasoning,
        outcome=outcome,
        document_page_count=document_page_count,
        document_chars=document_chars,
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
            summary=summary or f"{_source_line(citation_kind, clause_id, clause_quote, field_name, field_before, field_after)}\n\n{reasoning}",
            citation=citation,
        )

    return GroundResult(diff_id=diff_id, grounding_id=grounding_id, filing_id=filing_id)
