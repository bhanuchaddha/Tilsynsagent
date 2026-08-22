"""The rule engine: R1-R7 from docs/rules.md, as deterministic code.

The rules run in code, not in the model. R1-R7 are field comparisons with a
fixed precedence; an LLM re-deriving them probabilistically would add cost and
latency and produce a disagreement class that is almost always "the model was
wrong". The model's job starts where this engine returns NOT_COVERED.

Every outcome names the rule that produced it, so a filed decision can always
be traced to the rule that justified it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

WATCHED_FIELDS = (
    "maxbygnhjd",
    "maxetager",
    "bebygpct",
    "zonestatus",
    "anvendelsegenerel",
)

DIMENSIONAL_FIELDS = ("maxbygnhjd", "maxetager", "bebygpct")

RULE_SET_ID = "zealand-local-plans-v1"


class Outcome(str, Enum):
    FILE = "file"
    ESCALATE = "escalate"
    IGNORE = "ignore"
    # Not one of the three published outcomes: it is the engine declining to
    # decide, which hands the case to the assessment step.
    NOT_COVERED = "not_covered"


@dataclass(frozen=True)
class Decision:
    """What the engine concluded, and which rule got it there."""

    outcome: Outcome
    rule: str | None
    reason: str

    @property
    def is_covered(self) -> bool:
        return self.outcome is not Outcome.NOT_COVERED


def _is_blank(value: object) -> bool:
    """A field is blank when the register carries no value for it.

    Empty strings are treated as blank because the register uses both.
    Zero is *not* blank - a recorded 0 is a value, and R5/R6 exist to deal
    with what it means.
    """
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _as_number(value: object) -> float | None:
    """Numeric view of a field, or None when it is blank or non-numeric."""
    if _is_blank(value):
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


def _impossible(version: dict) -> tuple[bool, str]:
    """R5's test, applied to a single version of a sub-area.

    Two sub-cases, both meaning the record cannot be relied upon:
    more storeys than metres of height, or a height recorded as 0.
    """
    height = _as_number(version.get("maxbygnhjd"))
    storeys = _as_number(version.get("maxetager"))

    if height is not None and height == 0:
        return True, "a recorded height of 0 m is a missing value written as a number"
    if height is not None and storeys is not None and storeys > height:
        return (
            True,
            f"the record states {storeys:g} storeys within {height:g} m of height, "
            "which is physically impossible",
        )
    return False, ""


def _describe(field: str, before: object, after: object) -> str:
    def show(v: object) -> str:
        if _is_blank(v):
            return "blank"
        if isinstance(v, float) and v.is_integer():
            return f"{v:g}"
        return str(v)

    return f"{field} {show(before)} → {show(after)}"


def _direction(before: object, after: object) -> str:
    b, a = _as_number(before), _as_number(after)
    if b is None or a is None:
        return "changed"
    if a > b:
        return "increased"
    if a < b:
        return "decreased"
    return "changed"


def apply_rules(changed_fields: dict, before: dict, after: dict) -> Decision:
    """Apply R1-R7 in the precedence order given in docs/rules.md.

    ``changed_fields`` maps a watched field to ``{"before": x, "after": y}``,
    the same shape the golden dataset uses, so eval cases and live changes are
    interchangeable.

    Precedence (first match wins):
      1. R5  physically impossible records
      2. R6  built percentage to zero
      3. R1, R2  real value changes            -> file
      4. R3  pure additions                    -> ignore
      5. R4  pure removals                     -> escalate
      6. R7  mixed                             -> escalate

    Data-integrity rules come first: a change derived from an unreliable record
    should not be filed as though it were fact, whatever else it looks like.
    """
    watched_changes = {f: c for f, c in changed_fields.items() if f in WATCHED_FIELDS}

    if not watched_changes:
        return Decision(
            outcome=Outcome.IGNORE,
            rule=None,
            reason="No watched field differs between the two versions.",
        )

    # --- R5: physically impossible records ---------------------------------
    # Checked against both versions: a conclusion drawn from an unreliable
    # record is unreliable even when the unreliable side is the older one.
    for label, version in (("earlier", before), ("later", after)):
        bad, why = _impossible(version)
        if bad:
            return Decision(
                outcome=Outcome.ESCALATE,
                rule="R5",
                reason=(
                    f"The {label} version is physically impossible: {why}. "
                    "Any conclusion drawn from this record is unreliable, so a person "
                    "should confirm it against the plan document."
                ),
            )

    # --- R6: built percentage falling to zero ------------------------------
    if "bebygpct" in watched_changes:
        change = watched_changes["bebygpct"]
        before_pct = _as_number(change.get("before"))
        after_pct = _as_number(change.get("after"))
        if after_pct == 0 and before_pct is not None and before_pct != 0:
            return Decision(
                outcome=Outcome.ESCALATE,
                rule="R6",
                reason=(
                    f"Built percentage fell from {before_pct:g} to 0. Taken literally that "
                    "forbids all building, which is more likely a data entry than a "
                    "decision - but the difference matters enough for a person to confirm it."
                ),
            )

    # Classify each change as an addition (blank -> value), a removal
    # (value -> blank), or a real change of one value to another.
    additions, removals, real_changes = [], [], []
    for field, change in watched_changes.items():
        before_blank = _is_blank(change.get("before"))
        after_blank = _is_blank(change.get("after"))
        if before_blank and not after_blank:
            additions.append(field)
        elif after_blank and not before_blank:
            removals.append(field)
        elif not before_blank and not after_blank:
            real_changes.append(field)
        # blank -> blank is not a change at all and is ignored.

    # --- R1 / R2: real value changes ---------------------------------------
    if real_changes:
        # R1 outranks R2: use decides what may be built at all, which outranks
        # every dimensional limit.
        if "anvendelsegenerel" in real_changes:
            change = watched_changes["anvendelsegenerel"]
            return Decision(
                outcome=Outcome.FILE,
                rule="R1",
                reason=(
                    "Permitted use changed: "
                    f"{_describe('anvendelsegenerel', change['before'], change['after'])}. "
                    "Use decides what may be built at all."
                ),
            )

        dimensional = [f for f in real_changes if f in DIMENSIONAL_FIELDS]
        if dimensional:
            parts = []
            for field in dimensional:
                change = watched_changes[field]
                parts.append(
                    f"{_describe(field, change['before'], change['after'])} "
                    f"({_direction(change['before'], change['after'])})"
                )
            return Decision(
                outcome=Outcome.FILE,
                rule="R2",
                reason="A dimensional limit changed: " + "; ".join(parts) + ".",
            )

        # A real change confined to zonestatus. The rule set files use and
        # dimensional limits, and ignores, escalates or is silent elsewhere -
        # it does not say what a zone reclassification means on its own.
        change = watched_changes["zonestatus"]
        return Decision(
            outcome=Outcome.NOT_COVERED,
            rule=None,
            reason=(
                "Zone status changed on its own: "
                f"{_describe('zonestatus', change['before'], change['after'])}. "
                "The rule set covers changes to permitted use and to dimensional limits, "
                "and does not state what a zone reclassification alone means."
            ),
        )

    # --- R3 / R4 / R7: additions and removals ------------------------------
    if additions and removals:
        return Decision(
            outcome=Outcome.ESCALATE,
            rule="R7",
            reason=(
                f"Some fields gained values ({', '.join(sorted(additions))}) while others "
                f"lost them ({', '.join(sorted(removals))}) in the same revision. That "
                "mixture is more consistent with a record being reworked than with rules "
                "changing, but it cannot be assumed."
            ),
        )

    if additions:
        return Decision(
            outcome=Outcome.IGNORE,
            rule="R3",
            reason=(
                f"{', '.join(sorted(additions))} gained a value for the first time and no "
                "other field changed value. Nothing was loosened or tightened; the register "
                "was completed."
            ),
        )

    if removals:
        parts = [
            _describe(f, watched_changes[f]["before"], watched_changes[f]["after"])
            for f in sorted(removals)
        ]
        return Decision(
            outcome=Outcome.ESCALATE,
            rule="R4",
            reason=(
                "A limit that was recorded is now blank: " + "; ".join(parts) + ". "
                "The figure a reader would quote is no longer in the register, and the "
                "cause is unclear."
            ),
        )

    return Decision(
        outcome=Outcome.IGNORE,
        rule=None,
        reason="No watched field changed value.",
    )
