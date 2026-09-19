"""Declared permissions for write tools, checked before execution.

Each write tool declares what it may touch (which tables, which outcome
values it is allowed to write) and its budget for a single run. A call is
validated against its tool's declaration before any database write happens;
a call outside the declared surface, or a run that would exceed its budget,
is refused rather than attempted.
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
    # write outcome='file'; escalate.py only outcome='escalate'.
    #
    # Neither of those two may write 'ignore', and the reason has changed
    # since this was written. It used to read: "ignoring is the absence of a
    # write tool call, not a tool call with that argument." That was true
    # while every decision came from the rule layer, which never opens the
    # source document and therefore can only ever ignore by declining to act
    # - an absence, with nothing to audit.
    #
    # ground.py broke that premise, deliberately. A grounded ignore is a
    # positive claim backed by a clause the model quoted out of the document
    # ("6.3 says the reader sees nothing new here"), written to the
    # groundings table where it can be checked against the document by code.
    # That is the opposite of an absence: it is the most heavily evidenced
    # decision this system makes.
    #
    # So the ban was not loosened - it was scoped. 'ignore' remains
    # unwritable by file.py and escalate.py, which still have no way to
    # justify it. It is writable only by GROUND_PERMISSION, and only along
    # with a clause id and a verbatim quote (actions/ground.py refuses the
    # write without both).
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

# The grounded-decision tool. The only permission in this file that may write
# 'ignore', and the only one whose decisions no person reviews - see the
# note on allowed_outcomes above for why those two facts belong together.
#
# It writes to groundings *and* to the table its outcome implies (filings for
# a grounded file; nothing further for a grounded ignore, whose whole record
# is the grounding row). 'escalate' is absent from allowed_outcomes on
# purpose: a grounding that cannot decide never calls this tool at all, it
# routes to escalate.py, so there is exactly one code path to a person and it
# is the same one that existed before grounding.
GROUND_PERMISSION = ToolPermission(
    name="ground",
    writable_tables=frozenset({"groundings", "filings"}),
    allowed_outcomes=frozenset({"file", "ignore"}),
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
