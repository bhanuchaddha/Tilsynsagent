"""Pure-code scorers for LLM output fidelity. Zero LLM-as-judge, deliberately:
CLAUDE.md's one rule requires every autonomous decision traceable to what
justified it, and a judge model's verdict is exactly an untraceable decision -
"the judge said so" is not a citation. Every scorer here is a mechanical check
against the record the case actually contains.

Each scorer takes (case, output) and returns a Score: a 0.0/1.0 value, a name,
and a comment explaining the verdict - so a failing case is diagnosable from
the score alone, not just a number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Numerals, but not a digit adjacent to a letter/underscore on either side
# (R2/R5 rule references, sub-area codes like "3B", or any other
# alphanumeric identifier) - citing a rule or a sub-area label is expected
# factual content, not a figure drawn from the record.
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:[.,]\d+)?(?![A-Za-z0-9_])")

# Keywords that would only make sense if the model had read prose inside the
# source PDF rather than citing it - see docs/rules.md's stated boundary:
# "Anything requiring the source PDF to be read; the agent works from the
# register and cites the document, it does not interpret it."
_DOCUMENT_READING_PHRASES = (
    "according to the document",
    "the document states",
    "the pdf states",
    "the document says",
    "as stated in the document",
    "reading the document",
    "the attached document describes",
    "per the document text",
)


@dataclass(frozen=True)
class Score:
    name: str
    value: float  # 0.0-1.0
    comment: str


def _as_number(value: object) -> float | None:
    """Reuses rules/engine.py's before/after numeric normalisation so a
    Danish decimal comma (8,5) and its dotted form (8.5) are recognised as
    the same figure - see rules/engine.py's _as_number."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            return None
    return None


def _numbers_in_text(text: str) -> set[float]:
    found = set()
    for match in _NUMBER_RE.findall(text):
        n = _as_number(match)
        if n is not None:
            found.add(n)
    return found


def _record_numbers(case: dict) -> set[float]:
    """Every numeral that legitimately appears in the record this case was
    judged from: changed_fields' before/after values, plus before/after
    version dicts, so a summary quoting an unchanged field's value is not
    penalised as an invented number."""
    numbers: set[float] = set()
    for source in (case.get("changed_fields", {}).values(),):
        for change in source:
            for key in ("before", "after"):
                n = _as_number(change.get(key))
                if n is not None:
                    numbers.add(n)
    for version_key in ("before", "after"):
        version = case.get(version_key) or {}
        for value in version.values():
            n = _as_number(value)
            if n is not None:
                numbers.add(n)
    return numbers


def citation_fidelity(case: dict, output: str) -> Score:
    """The doklink appears verbatim in the output. The one rule, tested
    mechanically: every autonomous decision must be traceable to the source
    that justified it, and a citation that doesn't cite anything real fails
    that regardless of how good the prose reads."""
    doklink = case.get("source", {}).get("document", "")
    if not doklink:
        return Score("citation_fidelity", 0.0, "case has no source document to cite")
    present = doklink in output
    return Score(
        "citation_fidelity",
        1.0 if present else 0.0,
        "doklink present verbatim" if present else f"doklink {doklink!r} not found in output",
    )


def _context_numbers(case: dict) -> set[float]:
    """Numbers that legitimately appear in an output for reasons unrelated
    to the changed fields: the doklink URL (plan id and timestamp are
    embedded in it, e.g. .../20_9719017_1606817668820.pdf) and the source
    block's plan_id/sub_area, which a faithful summary may cite by name."""
    numbers: set[float] = set()
    source = case.get("source", {})
    doklink = source.get("document", "")
    numbers |= _numbers_in_text(doklink)
    for key in ("plan_id", "sub_area", "municipality_code"):
        value = source.get(key)
        n = _as_number(value)
        if n is not None:
            numbers.add(n)
        elif isinstance(value, str):
            # A digit embedded in a longer label, e.g. sub_area "Delområde 2".
            # The 2026-08-29 baseline recorded ZL-025 and ZL-027 failing
            # no_invented_numbers for exactly this: the model states the
            # sub-area by its full Danish name, and the bare-numeric branch
            # above only recognises a code like "3B". Naming the sub-area you
            # were given is faithful citation, not a fabricated figure.
            numbers |= _numbers_in_text(value)
    return numbers


def no_invented_numbers(case: dict, output: str) -> Score:
    """Every numeral in the output traces to a before/after value in the
    record, or to case metadata a faithful summary may legitimately cite
    (the plan id, sub-area, or the doklink URL's embedded digits). Handles
    Danish decimal commas via the same normalisation rules/engine.py uses
    for the fields themselves."""
    output_numbers = _numbers_in_text(output)
    if not output_numbers:
        return Score("no_invented_numbers", 1.0, "no numerals in output to check")
    record_numbers = _record_numbers(case) | _context_numbers(case)
    invented = output_numbers - record_numbers
    if invented:
        return Score(
            "no_invented_numbers",
            0.0,
            f"numbers not traceable to the record: {sorted(invented)}",
        )
    return Score("no_invented_numbers", 1.0, "every numeral traces to the record")


def states_both_values(case: dict, output: str) -> Score:
    """Both the before and after value of at least one changed field appear
    in the output. Counterweight to no_invented_numbers: a summary with no
    numbers at all scores a hollow 1.0 there, so this scorer exists to catch
    that emptiness - only the pair of scores together is meaningful."""
    changed_fields = case.get("changed_fields", {})
    if not changed_fields:
        return Score("states_both_values", 1.0, "case has no changed fields to state")

    output_numbers = _numbers_in_text(output)
    output_lower = output.lower()

    for field, change in changed_fields.items():
        before, after = change.get("before"), change.get("after")
        before_n, after_n = _as_number(before), _as_number(after)

        def _present(value, n):
            if n is not None:
                return n in output_numbers
            if value is None:
                return True  # blank -> nothing to state
            return str(value).lower() in output_lower

        if _present(before, before_n) and _present(after, after_n):
            return Score(
                "states_both_values", 1.0, f"both values of {field!r} appear in output"
            )

    return Score(
        "states_both_values",
        0.0,
        "no changed field has both its before and after value stated in the output",
    )


def did_not_read_the_document(case: dict, output: str) -> Score:
    """The PDF boundary in docs/rules.md ("the agent works from the register
    and cites the document, it does not interpret it") is held, not merely
    instructed. A keyword heuristic - stated as a floor, not a proof: it
    catches an explicit claim to have read prose inside the PDF, but cannot
    catch a model that read the PDF and phrased it carefully."""
    output_lower = output.lower()
    hit = next((p for p in _DOCUMENT_READING_PHRASES if p in output_lower), None)
    if hit:
        return Score(
            "did_not_read_the_document",
            0.0,
            f"output claims to read the document's content ({hit!r})",
        )
    return Score("did_not_read_the_document", 1.0, "no document-reading phrase found")


def valid_structured_output(case: dict, output: object, *, error: str | None = None) -> Score:
    """Strict JSON schema held for this call. Phase 1 verified schema
    strictness with one call; this scorer is what turns each of the 34+N
    replays into a real sample of that claim rather than repeating it once."""
    if error is not None:
        return Score("valid_structured_output", 0.0, f"structured output failed: {error}")
    return Score("valid_structured_output", 1.0, "structured output validated")


ALL_SCORERS = (
    citation_fidelity,
    no_invented_numbers,
    states_both_values,
    did_not_read_the_document,
)
