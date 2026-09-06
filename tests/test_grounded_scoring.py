"""The grounded scorers: the only check on the only decisions nobody reviews.

Both directions are tested for each, because a scorer that never fails is
indistinguishable from no scorer at all - and these are the ones where that
distinction matters most.
"""

from __future__ import annotations

import pytest

from tilsynsagent.obs.drift import DEGRADED
from tilsynsagent.obs.grounded import (
    abstained_when_ungrounded,
    clause_id_exists,
    clause_is_verbatim,
    quote_mentions_the_changed_field,
    score_grounded_run,
)

CLAUSE = "6.2 Bebyggelse må ikke opføres med en større højde end 8,5 m over terræn."


# --- clause_id_exists ------------------------------------------------------


def test_a_retrieved_clause_id_passes():
    assert clause_id_exists(clause_id="6.2", retrieved_clause_ids=["1.1", "6.2"]).value == 1.0


def test_a_fabricated_clause_id_fails():
    """The model did not read that clause, because it was never given it."""
    score = clause_id_exists(clause_id="9.9", retrieved_clause_ids=["1.1", "6.2"])
    assert score.value == 0.0
    assert "9.9" in score.comment


def test_citing_no_clause_at_all_fails():
    assert clause_id_exists(clause_id="", retrieved_clause_ids=["6.2"]).value == 0.0


# --- clause_is_verbatim ----------------------------------------------------


def test_an_exact_quote_passes():
    assert clause_is_verbatim(clause_quote="højde end 8,5 m", clause_text=CLAUSE).value == 1.0


def test_whitespace_and_case_are_normalised():
    """PDF extraction inserts line breaks and column gaps no human typed."""
    quote = "HØJDE   end\n8,5 m"
    assert clause_is_verbatim(clause_quote=quote, clause_text=CLAUSE).value == 1.0


def test_a_digit_changed_by_a_decimal_separator_fails():
    """8,5 -> 8.5 is a *modified* quote. A reader checking the decision will
    search the document for the document's own form and not find it."""
    assert clause_is_verbatim(clause_quote="højde end 8.5 m", clause_text=CLAUSE).value == 0.0


def test_a_changed_number_fails():
    assert clause_is_verbatim(clause_quote="højde end 9,5 m", clause_text=CLAUSE).value == 0.0


def test_a_paraphrase_fails():
    quote = "Buildings may not exceed 8,5 m in height"
    assert clause_is_verbatim(clause_quote=quote, clause_text=CLAUSE).value == 0.0


def test_no_clause_text_to_check_against_fails_rather_than_passes():
    """"We could not check this" is much closer to a failure than to a pass -
    a silent skip would let a window of decisions go unverified at 1.000."""
    assert clause_is_verbatim(clause_quote="anything", clause_text="").value == 0.0


# --- quote_mentions_the_changed_field --------------------------------------


def test_a_quote_about_the_changed_field_passes():
    score = quote_mentions_the_changed_field(
        clause_quote=CLAUSE, changed_fields={"maxbygnhjd": {}}
    )
    assert score.value == 1.0


def test_a_quote_about_something_else_fails():
    score = quote_mentions_the_changed_field(
        clause_quote="7.1 Facader skal fremstå i blank mur eller træ.",
        changed_fields={"maxetager": {}},
    )
    assert score.value == 0.0


def test_a_short_unit_does_not_match_inside_another_word():
    """Caught by a test rather than by reading: the unit "m" for metres
    appears inside "mur" (masonry), so a clause about facade materials scored
    as a clause about building height. Substring matching is unusable here."""
    score = quote_mentions_the_changed_field(
        clause_quote="7.1 Facader skal fremstå i blank mur eller træ.",
        changed_fields={"maxbygnhjd": {}},
    )
    assert score.value == 0.0


def test_a_bare_metre_unit_still_counts_as_height_vocabulary():
    score = quote_mentions_the_changed_field(
        clause_quote="6.2 Bebyggelse må ikke overstige 8,5 m.",
        changed_fields={"maxbygnhjd": {}},
    )
    assert score.value == 1.0


def test_a_field_with_no_checkable_vocabulary_is_not_penalised():
    score = quote_mentions_the_changed_field(
        clause_quote="anything", changed_fields={"some_unknown_field": {}}
    )
    assert score.value == 1.0


# --- abstained_when_ungrounded ---------------------------------------------


def test_an_ungrounded_run_that_escalated_passes():
    score = abstained_when_ungrounded(
        grounded=False, outcome="escalate", clause_id="", clause_quote=""
    )
    assert score.value == 1.0


@pytest.mark.parametrize("outcome", ["file", "ignore"])
def test_an_ungrounded_run_that_acted_anyway_fails(outcome):
    """The one code path to a person was bypassed. Structural, not a quality
    gradient - which is why drift.py pins this at 1.0."""
    score = abstained_when_ungrounded(
        grounded=False, outcome=outcome, clause_id="", clause_quote=""
    )
    assert score.value == 0.0


def test_a_grounded_run_without_evidence_fails():
    score = abstained_when_ungrounded(
        grounded=True, outcome="file", clause_id="6.2", clause_quote=""
    )
    assert score.value == 0.0


# --- score_grounded_run ----------------------------------------------------


def test_an_ungrounded_run_is_scored_only_on_abstention():
    """The evidence scorers have nothing to check on an abstention, and
    emitting hollow 1.0s for them would dilute the rates drift.py watches."""
    scores = score_grounded_run(
        grounded=False,
        outcome="escalate",
        clause_id="",
        clause_quote="",
        clause_text="",
        retrieved_clause_ids=[],
        changed_fields={"maxbygnhjd": {}},
    )
    assert [s.name for s in scores] == ["abstained_when_ungrounded"]


def test_a_good_grounded_run_passes_everything():
    scores = score_grounded_run(
        grounded=True,
        outcome="file",
        clause_id="6.2",
        clause_quote="højde end 8,5 m",
        clause_text=CLAUSE,
        retrieved_clause_ids=["6.2"],
        changed_fields={"maxbygnhjd": {}},
    )
    assert len(scores) == 4
    assert all(s.value == 1.0 for s in scores)


def test_every_grounded_scorer_has_a_committed_threshold():
    """A scorer with no threshold never contributes to a drift verdict, which
    reads as harmless and is in fact a check that does nothing."""
    scores = score_grounded_run(
        grounded=True,
        outcome="file",
        clause_id="6.2",
        clause_quote="højde",
        clause_text=CLAUSE,
        retrieved_clause_ids=["6.2"],
        changed_fields={"maxbygnhjd": {}},
    )
    assert {s.name for s in scores} <= set(DEGRADED)
