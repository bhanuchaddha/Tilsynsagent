import pytest

from tilsynsagent.actions.permissions import (
    ESCALATE_PERMISSION,
    FILE_PERMISSION,
    PermissionDenied,
    RunBudget,
    check_outcome,
    check_table,
)


def test_file_permission_allows_file_outcome():
    check_outcome(FILE_PERMISSION, "file")  # no raise


def test_file_permission_refuses_escalate_outcome():
    with pytest.raises(PermissionDenied):
        check_outcome(FILE_PERMISSION, "escalate")


def test_file_permission_refuses_not_covered_outcome():
    with pytest.raises(PermissionDenied):
        check_outcome(FILE_PERMISSION, "not_covered")


def test_escalate_permission_allows_escalate_outcome():
    check_outcome(ESCALATE_PERMISSION, "escalate")  # no raise


def test_escalate_permission_refuses_file_outcome():
    with pytest.raises(PermissionDenied):
        check_outcome(ESCALATE_PERMISSION, "file")


def test_file_permission_refuses_writing_to_escalations_table():
    with pytest.raises(PermissionDenied):
        check_table(FILE_PERMISSION, "escalations")


def test_file_permission_allows_writing_to_filings_table():
    check_table(FILE_PERMISSION, "filings")  # no raise


def test_escalate_permission_refuses_writing_to_filings_table():
    with pytest.raises(PermissionDenied):
        check_table(ESCALATE_PERMISSION, "filings")


def test_run_budget_refuses_call_past_max():
    permission = FILE_PERMISSION.__class__(
        name="file", writable_tables=frozenset({"filings"}),
        allowed_outcomes=frozenset({"file"}), max_calls_per_run=2,
    )
    budget = RunBudget()
    budget.check_and_record(permission)
    budget.check_and_record(permission)
    with pytest.raises(PermissionDenied):
        budget.check_and_record(permission)


def test_run_budget_tracks_tools_independently():
    budget = RunBudget()
    for _ in range(5):
        budget.check_and_record(FILE_PERMISSION)
    # escalate's budget is untouched by file's calls
    budget.check_and_record(ESCALATE_PERMISSION)
    assert budget.calls_made["file"] == 5
    assert budget.calls_made["escalate"] == 1
