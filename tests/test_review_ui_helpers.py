"""The UI's real logic, tested where Streamlit cannot reach.

Every tab body executes on every rerun and nothing `st.*`-shaped can be
rendered in this repo, so anything that decides something lives in
review/repo.py and is tested here.
"""

from __future__ import annotations

import json

from tilsynsagent.review.repo import (
    alert_summary,
    document_cache_rate,
    flagged_grounded_runs,
    grounding_rate_sentence,
    langfuse_queue_url,
    langfuse_trace_url,
    latest_comparison,
    load_nightly_runs,
)


def _grounding(**over):
    base = {
        "grounding_id": 1,
        "clause_id": "6.2",
        "clause_quote": "højde end 8,5 m",
        "retrieved_clause_ids": ["6.2"],
        "changed_fields": {"maxbygnhjd": {"before": 8.5, "after": 12.0}},
        "document_page_count": 41,
    }
    base.update(over)
    return base


def test_a_missing_base_url_yields_no_link_rather_than_a_broken_one():
    """A link that 404s in front of an audience is worse than a trace id
    someone can paste into a search box."""
    assert langfuse_trace_url(None, "tr-1") is None
    assert langfuse_trace_url("https://cloud.langfuse.com", "") is None


def test_a_trace_url_is_built_from_the_base():
    assert langfuse_trace_url("https://cloud.langfuse.com/", "tr-1") == (
        "https://cloud.langfuse.com/trace/tr-1"
    )


def test_an_explicit_queue_url_wins_over_the_derived_one():
    assert langfuse_queue_url("https://base", "https://explicit") == "https://explicit"
    assert langfuse_queue_url("https://base") == "https://base/annotation-queues"


def test_the_grounding_sentence_names_both_halves():
    """A percentage alone invites the wrong reading in both directions: low
    looks like failure when abstention is safe, high looks like success when
    it might mean the agent stopped escalating."""
    sentence = grounding_rate_sentence(
        {"uncovered_total": 10, "grounded_total": 6, "grounding_rate": 0.6}
    )
    assert "6 (60%)" in sentence
    assert "handed 4 to a person" in sentence
    assert "correct behaviour" in sentence


def test_no_uncovered_cases_is_explained_rather_than_shown_as_zero_percent():
    sentence = grounding_rate_sentence({"uncovered_total": 0})
    assert "0%" not in sentence
    assert "do not cover" in sentence


def test_a_fabricated_clause_is_flagged():
    flagged = flagged_grounded_runs([_grounding(clause_id="9.9")])
    assert len(flagged) == 1
    assert flagged[0]["failing"][0][0] == "clause_id_exists"


def test_a_quote_about_the_wrong_field_is_flagged():
    flagged = flagged_grounded_runs(
        [_grounding(clause_quote="7.1 Facader skal fremstå i blank mur.")]
    )
    assert "quote_mentions_the_changed_field" in [f[0] for f in flagged[0]["failing"]]


def test_a_good_grounding_is_not_flagged():
    assert flagged_grounded_runs([_grounding()]) == []


def test_retrieved_ids_stored_as_json_text_are_handled():
    """psycopg can hand back a JSONB column either way depending on the
    query; the tab must not crash on the string form."""
    assert flagged_grounded_runs([_grounding(retrieved_clause_ids=json.dumps(["6.2"]))]) == []


def test_flagging_never_fetches_a_document():
    """Every tab body runs on every rerun. A per-card document fetch would
    make the screen unusable - which is why clause_is_verbatim is not among
    the checks run here."""
    names = {n for f in flagged_grounded_runs([_grounding(clause_id="9.9")]) for n, _ in f["failing"]}
    assert "clause_is_verbatim" not in names


def test_alert_summary_counts_demo_and_recurrence_separately():
    summary = alert_summary(
        [
            {"is_demo": True, "recurrences": 2},
            {"is_demo": False, "recurrences": 0},
        ]
    )
    assert summary == {"total": 2, "real": 1, "demo": 1, "recurring": 1}


def test_the_cost_sentence_explains_why_caching_matters():
    sentence = document_cache_rate([_grounding()])
    assert "41 pages" in sentence
    assert "no invalidation" in sentence


def test_no_documents_read_yet_is_stated_plainly():
    assert document_cache_rate([]) == "No documents read yet."


def test_nightly_runs_load_newest_first(tmp_path):
    for name in ("2026-09-01", "2026-09-04", "2026-09-05"):
        (tmp_path / f"{name}.json").write_text(json.dumps({"passes": [{"records": [], "aggregate": {}}]}))
    runs = load_nightly_runs(directory=tmp_path)
    assert [r["name"] for r in runs] == ["2026-09-05", "2026-09-04", "2026-09-01"]


def test_a_corrupt_run_file_is_skipped_not_fatal(tmp_path):
    (tmp_path / "2026-09-05.json").write_text("{not json")
    assert load_nightly_runs(directory=tmp_path) == []


def test_fewer_than_two_runs_means_no_comparison(tmp_path):
    assert latest_comparison(directory=tmp_path) is None


def test_the_comparison_distinguishes_new_from_newly_failing(tmp_path):
    def payload(scores, records):
        return {
            "resolved_prompts": {},
            "passes": [
                {
                    "records": records,
                    "aggregate": {"scorers": {n: {"mean": v, "n": 1} for n, v in scores.items()}},
                }
            ],
        }

    (tmp_path / "2026-09-04.json").write_text(
        json.dumps(payload({"s": 1.0}, [{"case_id": "ZL-1", "scores": {"s": 1.0}}]))
    )
    (tmp_path / "2026-09-05.json").write_text(
        json.dumps(
            payload(
                {"s": 0.5},
                [
                    {"case_id": "ZL-1", "scores": {"s": 0.0}},
                    {"case_id": "AQ-1", "scores": {"s": 0.0}},
                ],
            )
        )
    )
    comparison = latest_comparison(directory=tmp_path)
    assert comparison.new_case_ids == ["AQ-1"]
    assert comparison.newly_failing_case_ids == ["ZL-1"]
