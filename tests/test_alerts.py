"""Alerts: written where they outlive the vendor, and idempotent.

Idempotency is the property that makes the alert directory usable. A
nightly firing on a persistent regression fires again tomorrow; if each
firing created a file, a week of a known problem would bury the directory
and destroy exactly the value it has.
"""

from __future__ import annotations

from datetime import date

from tilsynsagent.obs.alerts import (
    DEMO_PREFIX,
    business_alert,
    clear_demo_alerts,
    developer_alert,
    open_alerts,
    write_alert,
)


def test_a_first_alert_creates_a_file(tmp_path):
    path = write_alert(
        kind="quality-drop", subject="clause_is_verbatim", title="T", body="B",
        directory=tmp_path,
    )
    assert path.exists()
    assert path.read_text().startswith("# T")


def test_a_repeat_appends_rather_than_creating_a_second_file(tmp_path):
    for _ in range(3):
        write_alert(
            kind="quality-drop", subject="clause_is_verbatim", title="T", body="B",
            directory=tmp_path,
        )
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    assert files[0].read_text().count("## Recurrence") == 2


def test_the_filename_carries_the_date_kind_and_subject(tmp_path):
    path = write_alert(
        kind="eval-regression", subject="0.94-to-0.86", title="T", body="B",
        on=date(2026, 9, 5), directory=tmp_path,
    )
    assert path.name == "2026-09-05-eval-regression-0-94-to-0-86.md"


def test_a_demo_alert_is_distinguishable_by_filename_alone(tmp_path):
    """demo/reset.py deletes by prefix rather than by reading the file: the
    naming scheme agreed at write time is what makes a reset unable to touch
    a real alert."""
    path = write_alert(
        kind="quality-drop", subject="s", title="T", body="B", demo=True, directory=tmp_path
    )
    assert path.name.startswith(DEMO_PREFIX)


def test_a_business_alert_names_no_code(tmp_path):
    """Written for someone who will never open the code. A scorer name is
    unavoidable - it is what fired - but nothing else may leak."""
    path = business_alert(
        scorer="clause_is_verbatim", rate=0.82, minimum=0.95, window_size=40,
        directory=tmp_path,
    )
    text = path.read_text()
    assert "82%" in text
    assert "quote" in text.lower()
    for jargon in ("prompt version", "case id", "pull request", "commit", ".py"):
        assert jargon not in text.lower()


def test_a_business_alert_says_what_the_scorer_means_in_plain_language(tmp_path):
    path = business_alert(
        scorer="abstained_when_ungrounded", rate=0.9, minimum=1.0, window_size=30,
        directory=tmp_path,
    )
    assert "hand it to a person" in path.read_text()


def test_a_developer_alert_separates_new_from_newly_failing(tmp_path):
    """The distinction is the whole developer workflow: a new failing case is
    the dataset working; a case that used to pass is a regression."""
    path = developer_alert(
        current_score=0.86,
        previous_score=0.94,
        regressed_scorers=["clause_is_verbatim"],
        new_case_ids=["AQ-001"],
        newly_failing_case_ids=["ZL-018"],
        directory=tmp_path,
    )
    text = path.read_text()
    new_section = text.split("## New cases")[1].split("## Cases that used to pass")[0]
    failing_section = text.split("## Cases that used to pass")[1]
    assert "AQ-001" in new_section and "ZL-018" not in new_section
    assert "ZL-018" in failing_section and "AQ-001" not in failing_section


def test_a_developer_alert_flags_a_changed_prompt_version(tmp_path):
    path = developer_alert(
        current_score=0.86, previous_score=0.94, regressed_scorers=[],
        new_case_ids=[], newly_failing_case_ids=["ZL-018"],
        prompt_versions={"ground": "tilsynsagent-ground-system@v2"},
        previous_prompt_versions={"ground": "tilsynsagent-ground-system@v1"},
        directory=tmp_path,
    )
    text = path.read_text()
    assert "v1` -> `" in text and "v2" in text
    assert "does not appear in any code diff" in text


def test_open_alerts_skips_the_readme_and_counts_recurrences(tmp_path):
    (tmp_path / "README.md").write_text("# Alerts\n")
    write_alert(kind="k", subject="s", title="Real one", body="B", directory=tmp_path)
    write_alert(kind="k", subject="s", title="Real one", body="B", directory=tmp_path)
    alerts = open_alerts(directory=tmp_path)
    assert len(alerts) == 1
    assert alerts[0]["title"] == "Real one"
    assert alerts[0]["recurrences"] == 1


def test_clearing_demo_alerts_never_touches_a_real_one(tmp_path):
    write_alert(kind="k", subject="real", title="R", body="B", directory=tmp_path)
    demo = write_alert(
        kind="k", subject="demo", title="D", body="B", demo=True, directory=tmp_path
    )
    assert clear_demo_alerts(directory=tmp_path) == 1
    assert not demo.exists()
    assert len(list(tmp_path.glob("*.md"))) == 1
