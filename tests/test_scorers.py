"""Offline tests for evals/scorers.py. Pure functions, no LLM, no network."""

from evals.scorers import (
    citation_fidelity,
    did_not_read_the_document,
    no_invented_numbers,
    states_both_values,
    valid_structured_output,
)

CASE = {
    "source": {"document": "https://dokument.plandata.dk/20_1234_abc.pdf"},
    "changed_fields": {
        "maxbygnhjd": {"before": 8.5, "after": 12.5},
    },
    "before": {"maxbygnhjd": 8.5, "maxetager": 2, "bebygpct": 40},
    "after": {"maxbygnhjd": 12.5, "maxetager": 2, "bebygpct": 40},
}


# --- citation_fidelity --------------------------------------------------


def test_citation_fidelity_passes_when_doklink_present():
    output = "Filed under R2. Source: https://dokument.plandata.dk/20_1234_abc.pdf"
    assert citation_fidelity(CASE, output).value == 1.0


def test_citation_fidelity_fails_when_doklink_absent():
    output = "Filed under R2. Height increased."
    assert citation_fidelity(CASE, output).value == 0.0


def test_citation_fidelity_fails_when_case_has_no_document():
    case = {"source": {}, "changed_fields": {}}
    assert citation_fidelity(case, "anything").value == 0.0


# --- no_invented_numbers -------------------------------------------------


def test_no_invented_numbers_passes_when_numbers_trace_to_record():
    output = "Max building height increased from 8.5 to 12.5 metres."
    assert no_invented_numbers(CASE, output).value == 1.0


def test_no_invented_numbers_fails_on_fabricated_number():
    output = "Max building height increased from 8.5 to 99 metres."
    assert no_invented_numbers(CASE, output).value == 0.0


def test_no_invented_numbers_passes_with_no_numbers_in_output():
    output = "Permitted use changed, requires review."
    assert no_invented_numbers(CASE, output).value == 1.0


def test_no_invented_numbers_handles_danish_decimal_comma():
    """8,5 in Danish notation must be recognised as the same figure as 8.5."""
    output = "Højden steg fra 8,5 til 12,5 meter."
    assert no_invented_numbers(CASE, output).value == 1.0


def test_no_invented_numbers_allows_unchanged_field_values():
    """maxetager (2) and bebygpct (40) didn't change but are real record
    values - quoting them is not invention."""
    output = "Height changed; storeys remain 2 and coverage remains 40%."
    assert no_invented_numbers(CASE, output).value == 1.0


# --- states_both_values ---------------------------------------------------


def test_states_both_values_passes_when_both_present():
    output = "Max building height increased from 8.5 to 12.5 metres."
    assert states_both_values(CASE, output).value == 1.0


def test_states_both_values_fails_when_only_after_present():
    output = "Max building height is now 12.5 metres."
    assert states_both_values(CASE, output).value == 0.0


def test_states_both_values_fails_on_empty_summary():
    """The counterweight case: no numbers at all scores a hollow 1.0 on
    no_invented_numbers, so this scorer must independently fail it."""
    output = "This case was filed."
    assert states_both_values(CASE, output).value == 0.0
    assert no_invented_numbers(CASE, output).value == 1.0


def test_states_both_values_handles_text_field_change():
    case = {
        "changed_fields": {
            "anvendelsegenerel": {"before": "Boligområde", "after": "Erhvervsområde"}
        }
    }
    output = "Use changed from Boligområde to Erhvervsområde."
    assert states_both_values(case, output).value == 1.0


# --- did_not_read_the_document ---------------------------------------------


def test_did_not_read_the_document_passes_on_clean_citation():
    output = "Filed under R2. Source: https://dokument.plandata.dk/20_1234_abc.pdf"
    assert did_not_read_the_document(CASE, output).value == 1.0


def test_did_not_read_the_document_fails_on_reading_claim():
    output = "According to the document, the height limit was revised for safety reasons."
    assert did_not_read_the_document(CASE, output).value == 0.0


def test_did_not_read_the_document_fails_case_insensitive():
    output = "THE DOCUMENT STATES the plan was amended."
    assert did_not_read_the_document(CASE, output).value == 0.0


# --- valid_structured_output ------------------------------------------------


def test_valid_structured_output_passes_without_error():
    assert valid_structured_output(CASE, {"summary": "ok"}).value == 1.0


def test_valid_structured_output_fails_with_error():
    assert valid_structured_output(CASE, None, error="schema validation failed").value == 0.0


# --- regression: a scorer boundary gap -----------


def test_no_invented_numbers_allows_a_digit_inside_a_sub_area_label():
    """Both sub-areas are named "Delområde 2" / "Delområde 3", and
    _context_numbers only allowed a bare numeric sub-area code (e.g. "3B").
    Naming the sub-area you were handed is faithful citation."""
    case = {
        "source": {
            "document": "https://dokument.plandata.dk/20_1234_abc.pdf",
            "sub_area": "Delområde 2",
            "plan_id": 10351067,
        },
        "changed_fields": {"bebygpct": {"before": None, "after": 40}},
        "before": {},
        "after": {"bebygpct": 40},
    }
    assert no_invented_numbers(case, "Delområde 2 now has a density of 40.").value == 1.0


def test_no_invented_numbers_still_catches_fabrication_alongside_a_label_digit():
    """The fix above widens what is allowed, so this pins the boundary: a
    genuinely invented figure in the same sentence must still fail, or the
    scorer has been loosened into uselessness."""
    case = {
        "source": {
            "document": "https://dokument.plandata.dk/20_1234_abc.pdf",
            "sub_area": "Delområde 2",
            "plan_id": 10351067,
        },
        "changed_fields": {"bebygpct": {"before": None, "after": 40}},
        "before": {},
        "after": {"bebygpct": 40},
    }
    score = no_invented_numbers(case, "Delområde 2 rose to 40 from 999.")
    assert score.value == 0.0
    assert "999" in score.comment
