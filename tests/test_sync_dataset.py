"""Offline test: --dry-run must never touch Langfuse. Real sync is verified
manually per the plan's Verification section (run twice, confirm item count
stays 34+N), not exercised by pytest."""

import subprocess
import sys


def test_dry_run_lists_all_cases_without_requiring_langfuse_keys(no_langfuse_env):
    result = subprocess.run(
        [sys.executable, "-m", "evals.sync_dataset", "--dry-run"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ},
        check=False,
    )
    assert result.returncode == 0
    assert "cases to sync" in result.stderr
    assert "ZL-001" in result.stderr
    assert "NC-001" in result.stderr
