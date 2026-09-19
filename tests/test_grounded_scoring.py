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
    field_citation_is_real,
    quote_mentions_the_changed_field,
    score_grounded_run,
)

CLAUSE = "6.2 Bebyggelse må ikke opføres med en større højde end 8,5 m over terræn."
# The whole document these scorers now check against, rather than the one
# retrieved clause they used to be handed - see stage 1.
DOCUMENT = (
    "1.1 Lokalplanens formål er at fastlægge områdets anvendelse.\n"
    f"{CLAUSE}\n"
    "16.3 Denne bestemmelse handler om noget helt andet.\n"
    "Vedtaget den 6.4 2021 af kommunalbestyrelsen.\n"
)

BEFORE = {"status": "Forslag", "versionsnr": 1, "maxbygnhjd": 8.5, "kommunenavn": None}
AFTER = {"status": "Vedtaget", "versionsnr": 2, "maxbygnhjd": 8.5, "kommunenavn": None}


# --- clause_id_exists ------------------------------------------------------


def test_a_clause_id_in_the_document_passes():
    assert clause_id_exists(clause_id="6.2", document_text=DOCUMENT).value == 1.0


def test_a_fabricated_clause_id_fails():
    """A clause number that appears nowhere in the source is a fabrication,
    and a grounded decision is the only kind no person reviews."""
    score = clause_id_exists(clause_id="9.9", document_text=DOCUMENT)
    assert score.value == 0.0
    assert "9.9" in score.comment


def test_a_clause_id_that_is_only_a_substring_fails():
    """"6.3" occurs inside "16.3" and inside the date "6.4 2021". Matching
    bare digits would let a fabricated number score as real."""
    assert clause_id_exists(clause_id="6.3", document_text=DOCUMENT).value == 0.0


def test_citing_no_clause_at_all_fails():
    assert clause_id_exists(clause_id="", document_text=DOCUMENT).value == 0.0


def test_no_document_to_check_against_fails_rather_than_passes():
    assert clause_id_exists(clause_id="6.2", document_text="").value == 0.0


# --- clause_is_verbatim ----------------------------------------------------


def test_an_exact_quote_passes():
    assert clause_is_verbatim(clause_quote="højde end 8,5 m", document_text=DOCUMENT).value == 1.0


def test_whitespace_and_case_are_normalised():
    """PDF extraction inserts line breaks and column gaps no human typed."""
    quote = "HØJDE   end\n8,5 m"
    assert clause_is_verbatim(clause_quote=quote, document_text=DOCUMENT).value == 1.0


def test_a_digit_changed_by_a_decimal_separator_fails():
    """8,5 -> 8.5 is a *modified* quote. A reader checking the decision will
    search the document for the document's own form and not find it."""
    assert clause_is_verbatim(clause_quote="højde end 8.5 m", document_text=DOCUMENT).value == 0.0


def test_a_changed_number_fails():
    assert clause_is_verbatim(clause_quote="højde end 9,5 m", document_text=DOCUMENT).value == 0.0


def test_a_paraphrase_fails():
    quote = "Buildings may not exceed 8,5 m in height"
    assert clause_is_verbatim(clause_quote=quote, document_text=DOCUMENT).value == 0.0


def test_no_document_text_to_check_the_quote_against_fails_rather_than_passes():
    """"We could not check this" is much closer to a failure than to a pass -
    a silent skip would let a window of decisions go unverified at 1.000."""
    assert clause_is_verbatim(clause_quote="anything", document_text="").value == 0.0


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
        grounded=False, outcome="escalate", citation_kind="", clause_id="", clause_quote=""
    )
    assert score.value == 1.0


@pytest.mark.parametrize("outcome", ["file", "ignore"])
def test_an_ungrounded_run_that_acted_anyway_fails(outcome):
    """The one code path to a person was bypassed. Structural, not a quality
    gradient - which is why drift.py pins this at 1.0."""
    score = abstained_when_ungrounded(
        grounded=False, outcome=outcome, citation_kind="", clause_id="", clause_quote=""
    )
    assert score.value == 0.0


def test_a_grounded_run_without_evidence_fails():
    score = abstained_when_ungrounded(
        grounded=True, outcome="file", citation_kind="clause", clause_id="6.2", clause_quote=""
    )
    assert score.value == 0.0


def test_a_grounded_field_citation_with_a_name_and_a_value_passes():
    score = abstained_when_ungrounded(
        grounded=True,
        outcome="file",
        citation_kind="field",
        clause_id="",
        clause_quote="",
        field_name="status",
        field_before="Forslag",
        field_after="Vedtaget",
    )
    assert score.value == 1.0


def test_a_grounded_field_citation_without_values_fails():
    """The field path must not be the loose one - half a citation is not a
    citation, in the same way half a quote is not a quote."""
    score = abstained_when_ungrounded(
        grounded=True,
        outcome="file",
        citation_kind="field",
        clause_id="",
        clause_quote="",
        field_name="status",
    )
    assert score.value == 0.0


def test_a_grounded_run_with_an_unrecognised_citation_kind_fails():
    score = abstained_when_ungrounded(
        grounded=True,
        outcome="file",
        citation_kind="vibes",
        clause_id="6.2",
        clause_quote="q",
    )
    assert score.value == 0.0


# --- field_citation_is_real ------------------------------------------------


def test_a_real_field_transition_passes():
    score = field_citation_is_real(
        field_name="status",
        field_before="Forslag",
        field_after="Vedtaget",
        before=BEFORE,
        after=AFTER,
    )
    assert score.value == 1.0


def test_a_field_that_is_not_in_the_record_fails():
    """The field path's counterpart to a fabricated clause number, and the
    easier fabrication to miss because it looks like arithmetic on data the
    reader assumes was verified."""
    score = field_citation_is_real(
        field_name="hoejdegraense",
        field_before="8,5",
        field_after="12",
        before=BEFORE,
        after=AFTER,
    )
    assert score.value == 0.0
    assert "hoejdegraense" in score.comment


def test_a_real_field_with_invented_values_fails():
    score = field_citation_is_real(
        field_name="status",
        field_before="Forslag",
        field_after="Aflyst",
        before=BEFORE,
        after=AFTER,
    )
    assert score.value == 0.0
    assert "Aflyst" in score.comment


def test_a_field_citation_with_no_values_at_all_fails():
    score = field_citation_is_real(
        field_name="status", field_before="", field_after="", before=BEFORE, after=AFTER
    )
    assert score.value == 0.0


def test_one_empty_side_is_accepted_when_the_other_matches():
    """A field that gained or lost a value is a real transition to cite."""
    score = field_citation_is_real(
        field_name="kommunenavn",
        field_before="",
        field_after="",
        before=BEFORE,
        after={**AFTER, "kommunenavn": "Egedal"},
    )
    assert score.value == 0.0  # both sides empty is not a citation

    score = field_citation_is_real(
        field_name="kommunenavn",
        field_before="",
        field_after="Egedal",
        before=BEFORE,
        after={**AFTER, "kommunenavn": "Egedal"},
    )
    assert score.value == 1.0


def test_values_are_compared_as_the_model_sees_them():
    """versionsnr is an int in the record and reaches the model as "2"."""
    score = field_citation_is_real(
        field_name="versionsnr",
        field_before="1",
        field_after="2",
        before=BEFORE,
        after=AFTER,
    )
    assert score.value == 1.0


# --- score_grounded_run ----------------------------------------------------


def test_an_ungrounded_run_is_scored_only_on_abstention():
    """The evidence scorers have nothing to check on an abstention, and
    emitting hollow 1.0s for them would dilute the rates drift.py watches."""
    scores = score_grounded_run(
        grounded=False,
        outcome="escalate",
        citation_kind="",
        clause_id="",
        clause_quote="",
        document_text="",
        changed_fields={"maxbygnhjd": {}},
    )
    assert [s.name for s in scores] == ["abstained_when_ungrounded"]


def test_a_good_grounded_run_passes_everything():
    scores = score_grounded_run(
        grounded=True,
        outcome="file",
        citation_kind="clause",
        clause_id="6.2",
        clause_quote="højde end 8,5 m",
        document_text=DOCUMENT,
        changed_fields={"maxbygnhjd": {}},
    )
    assert len(scores) == 4
    assert all(s.value == 1.0 for s in scores)


def test_a_field_cited_run_is_checked_against_the_record_not_the_document():
    """Running the clause scorers on a field citation would produce three
    guaranteed zeros for a perfectly traceable decision, poisoning exactly the
    rates drift.py watches."""
    scores = score_grounded_run(
        grounded=True,
        outcome="file",
        citation_kind="field",
        clause_id="",
        clause_quote="",
        field_name="status",
        field_before="Forslag",
        field_after="Vedtaget",
        document_text="",
        before=BEFORE,
        after=AFTER,
        changed_fields={},
    )
    assert [s.name for s in scores] == [
        "abstained_when_ungrounded",
        "field_citation_is_real",
    ]
    assert all(s.value == 1.0 for s in scores)


def test_a_fabricated_field_citation_is_caught():
    scores = score_grounded_run(
        grounded=True,
        outcome="file",
        citation_kind="field",
        clause_id="",
        clause_quote="",
        field_name="status",
        field_before="Forslag",
        field_after="Aflyst",
        document_text="",
        before=BEFORE,
        after=AFTER,
        changed_fields={},
    )
    failing = [s for s in scores if s.value < 1.0]
    assert [s.name for s in failing] == ["field_citation_is_real"]


def test_every_grounded_scorer_has_a_committed_threshold():
    """A scorer with no threshold never contributes to a drift verdict, which
    reads as harmless and is in fact a check that does nothing."""
    scores = score_grounded_run(
        grounded=True,
        outcome="file",
        citation_kind="clause",
        clause_id="6.2",
        clause_quote="højde",
        document_text=DOCUMENT,
        changed_fields={"maxbygnhjd": {}},
    )
    assert {s.name for s in scores} <= set(DEGRADED)
