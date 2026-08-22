"""The file write tool: records a diff as a filed decision.

Declared surface: writes only to diffs (setting outcome) and filings. Only
ever writes outcome='file'. Called after rules.engine.apply_rules returns
FILE and llm.summarise has produced the record text.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from tilsynsagent.actions.permissions import (
    FILE_PERMISSION,
    RunBudget,
    check_outcome,
    check_table,
)
from tilsynsagent.db import repo


@dataclass(frozen=True)
class FileResult:
    diff_id: int
    filing_id: int


def file_decision(
    conn: psycopg.Connection,
    budget: RunBudget,
    *,
    sub_area_id: int,
    before_version_id: int | None,
    after_version_id: int,
    changed_fields: dict,
    rule: str,
    rule_set: str,
    summary: str,
    citation: str,
) -> FileResult:
    """Writes a diff with outcome='file' and its filing. Refuses before any
    write happens if the call falls outside this tool's declared permission
    or its run budget."""
    check_outcome(FILE_PERMISSION, "file")
    check_table(FILE_PERMISSION, "filings")
    budget.check_and_record(FILE_PERMISSION)

    diff_id = repo.insert_diff(
        conn,
        sub_area_id=sub_area_id,
        before_version_id=before_version_id,
        after_version_id=after_version_id,
        changed_fields=changed_fields,
        outcome="file",
        rule=rule,
        rule_set=rule_set,
    )
    filing_id = repo.insert_filing(conn, diff_id=diff_id, summary=summary, citation=citation)
    return FileResult(diff_id=diff_id, filing_id=filing_id)
