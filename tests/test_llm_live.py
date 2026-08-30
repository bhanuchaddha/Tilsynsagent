"""One real Groq call each for assess() and summarise() - not 34, that
belongs in the eval runner (evals/run_baseline.py), not pytest. This proves
the structured-output schema still validates and that usage.record()
actually captures non-zero tokens from a live response, per CLAUDE.md's
verification rule: documented behaviour is not evidence, a live call is.

Skipped automatically if GROQ_API_KEY is not set.
"""

import os

import pytest

from tilsynsagent.llm.assess import assess
from tilsynsagent.llm.summarise import summarise
from tilsynsagent.llm.usage import take_last_usage

pytestmark = pytest.mark.skipif(
    "GROQ_API_KEY" not in os.environ, reason="requires a real GROQ_API_KEY"
)


def test_assess_live_call_validates_and_records_usage():
    take_last_usage()  # clear any stale value from a prior test in this run
    result = assess(
        sub_area_description="Testkommune, plan 1, sub-area A",
        changed_fields={"zonestatus": {"before": "Byzone", "after": "Sommerhusområde"}},
        doklink="https://dokument.plandata.dk/20_1051943_APPROVED_1188304802472.pdf",
    )
    assert result.what_is_unclear
    assert result.what_a_person_must_decide
    assert result.citation

    usage = take_last_usage()
    assert usage is not None
    assert usage.total_tokens > 0
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens > 0


def test_summarise_live_call_validates_and_records_usage():
    take_last_usage()
    summary = summarise(
        sub_area_description="Testkommune, plan 1, sub-area A",
        changed_fields={"maxbygnhjd": {"before": 27, "after": 22.5}},
        rule="R2",
        rule_reason="A dimensional limit changed.",
        doklink="https://dokument.plandata.dk/20_11287563_1719326479343.pdf",
    )
    assert isinstance(summary, str)
    assert summary

    usage = take_last_usage()
    assert usage is not None
    assert usage.total_tokens > 0
