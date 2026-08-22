"""Declared permissions for write tools, checked before execution.

Each write tool declares what it may touch (which tables, which outcome
values it is allowed to write) and its budget for a single run. A call is
validated against its tool's declaration before any database write happens;
a call outside the declared surface, or a run that would exceed its budget,
is refused rather than attempted. This exists from the first write tool
rather than being retrofitted onto tools already in use, per the Phase 1 plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class PermissionDenied(Exception):
    """Raised when a tool call falls outside its declared permission surface,
    or would exceed its run budget. Always raised before any write happens."""


@dataclass(frozen=True)
class ToolPermission:
    """What one write tool is allowed to do."""

    name: str
    # Tables this tool may write to. Anything else is refused even if the
    # underlying function could technically reach it.
    writable_tables: frozenset[str]
    # Outcome values this tool may record on a diff. file.py may only ever
    # write outcome='file'; escalate.py only outcome='escalate'. Neither may
    # write 'ignore' - ignoring is the absence of a write tool call, not a
    # tool call with that argument.
    allowed_outcomes: frozenset[str]
    # Max calls in a single run. A run that would exceed this stops and
    # escalates rather than continuing - see RunBudget below.
    max_calls_per_run: int


FILE_PERMISSION = ToolPermission(
    name="file",
    writable_tables=frozenset({"filings"}),
    allowed_outcomes=frozenset({"file"}),
    max_calls_per_run=200,
)

ESCALATE_PERMISSION = ToolPermission(
    name="escalate",
    writable_tables=frozenset({"escalations"}),
    allowed_outcomes=frozenset({"escalate"}),
    max_calls_per_run=200,
)


@dataclass
class RunBudget:
    """Tracks calls made against each tool's permission during one run.

    A single instance is shared across every write-tool call in a run
    (threaded through the graph state), so the budget is enforced across the
    whole run, not per node invocation.
    """

    calls_made: dict[str, int] = field(default_factory=dict)

    def check_and_record(self, permission: ToolPermission) -> None:
        made = self.calls_made.get(permission.name, 0)
        if made >= permission.max_calls_per_run:
            raise PermissionDenied(
                f"{permission.name} has made {made} calls this run, at its budget of "
                f"{permission.max_calls_per_run}. Refusing this call; the run should stop "
                "and escalate rather than continue past its declared budget."
            )
        self.calls_made[permission.name] = made + 1


def check_outcome(permission: ToolPermission, outcome: str) -> None:
    if outcome not in permission.allowed_outcomes:
        raise PermissionDenied(
            f"{permission.name} may only write outcome(s) {sorted(permission.allowed_outcomes)}; "
            f"refused outcome={outcome!r}."
        )


def check_table(permission: ToolPermission, table: str) -> None:
    if table not in permission.writable_tables:
        raise PermissionDenied(
            f"{permission.name} may only write to {sorted(permission.writable_tables)}; "
            f"refused write to table={table!r}."
        )
