"""The rule engine: R1-R4 from docs/rules.md, as deterministic code.

The rules run in code, not in the model. R1-R4 are field comparisons with a
fixed precedence; an LLM re-deriving them probabilistically would add cost and
latency and produce a disagreement class that is almost always "the model was
wrong". The model's job starts where this engine returns NOT_COVERED.

Every outcome names the rule that produced it, so a filed decision can always
be traced to the rule that justified it. There are only two outcomes a rule can
reach: FILE or ESCALATE. `ignore` is not reachable deterministically - it is
always a claim about a document this engine never opens (see docs/rules.md,
"The two outcomes"); NOT_COVERED hands that claim to assess() instead of
asserting it from the register alone.
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

RULE_SET_ID = "zealand-local-plans-v2"


class Outcome(str, Enum):
    FILE = "file"
    ESCALATE = "escalate"
    # Not one of the two published outcomes: it is the engine declining to
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
    Zero is *not* blank - a recorded 0 is a value, and R1 exists to deal
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
    """R1's physical-impossibility test, applied to a single version of a
    sub-area.

    Two sub-cases, both meaning the record cannot be relied upon: more
    storeys than metres of height, or a height recorded as 0.
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
    """Apply R1-R4 in the precedence order given in docs/rules.md.

    ``changed_fields`` maps a watched field to ``{"before": x, "after": y}``,
    the same shape the golden dataset uses, so eval cases and live changes are
    interchangeable.

    Precedence (first match wins):
      1. R1  physically impossible records                    -> escalate
      2. R2  a watched field value -> different value          -> file
      3. R3  field(s) gained a value, none lost                -> file
      4. R4  any field lost its value (alone, or mixed with
              gains)                                           -> escalate
      -  seen before, new version, no watched field differs    -> not_covered

    Data-integrity comes first: a change derived from an unreliable record
    should not be filed as though it were fact, whatever else it looks like.
    A revision where no watched field differs is not asserted "unchanged" -
    it is handed to assess() as the honest "we cannot see what changed."
    """
    watched_changes = {f: c for f, c in changed_fields.items() if f in WATCHED_FIELDS}

    if not watched_changes:
        return Decision(
            outcome=Outcome.NOT_COVERED,
            rule=None,
            reason=(
                "This sub-area has been seen before and a new version exists, but no "
                "watched field differs from the last version stored. Something moved "
                "outside the five watched fields; the register alone cannot say what."
            ),
        )

    # --- R1: physically impossible records ----------------------------------
    # Checked against both versions: a conclusion drawn from an unreliable
    # record is unreliable even when the unreliable side is the older one.
    for label, version in (("earlier", before), ("later", after)):
        bad, why = _impossible(version)
        if bad:
            return Decision(
                outcome=Outcome.ESCALATE,
                rule="R1",
                reason=(
                    f"The {label} version is physically impossible: {why}. "
                    "Any conclusion drawn from this record is unreliable, so a person "
                    "should confirm it against the plan document."
                ),
            )

    # bebygpct falling from a real value to 0 is the third R1 sub-case - a
    # transition, not a single-version check, since a first-time value of 0
    # is a new value (R3), not an impossible record.
    if "bebygpct" in watched_changes:
        change = watched_changes["bebygpct"]
        before_pct = _as_number(change.get("before"))
        after_pct = _as_number(change.get("after"))
        if after_pct == 0 and before_pct is not None and before_pct != 0:
            return Decision(
                outcome=Outcome.ESCALATE,
                rule="R1",
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

    # --- R2: any watched field value -> different value ---------------------
    if real_changes:
        # Use outranks dimensions outranks zone, for the reason text - the
        # outcome is the same (file) regardless of which field changed.
        if "anvendelsegenerel" in real_changes:
            change = watched_changes["anvendelsegenerel"]
            return Decision(
                outcome=Outcome.FILE,
                rule="R2",
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

        # A real change confined to zonestatus. v2 files this like any other
        # watched-field value change: a reader watching this land now sees a
        # zone status where before it read differently.
        change = watched_changes["zonestatus"]
        return Decision(
            outcome=Outcome.FILE,
            rule="R2",
            reason=(
                "Zone status changed: "
                f"{_describe('zonestatus', change['before'], change['after'])}."
            ),
        )

    # --- R4: any field lost its value (alone, or mixed with gains) ----------
    # Checked before R3 so a mixed gain/loss revision escalates on this
    # branch rather than being caught by the pure-addition check below.
    if removals:
        parts = [
            _describe(f, watched_changes[f]["before"], watched_changes[f]["after"])
            for f in sorted(removals)
        ]
        if additions:
            parts += [
                _describe(f, watched_changes[f]["before"], watched_changes[f]["after"])
                for f in sorted(additions)
            ]
            reason = (
                f"Some fields gained values ({', '.join(sorted(additions))}) while others "
                f"lost them ({', '.join(sorted(removals))}) in the same revision. A limit "
                "that was recorded is now blank, and the register alone cannot say why: "
                + "; ".join(parts) + "."
            )
        else:
            reason = (
                "A limit that was recorded is now blank: " + "; ".join(parts) + ". "
                "The figure a reader would quote is no longer in the register, and the "
                "cause is unclear."
            )
        return Decision(outcome=Outcome.ESCALATE, rule="R4", reason=reason)

    # --- R3: field(s) gained a value, none lost ------------------------------
    if additions:
        return Decision(
            outcome=Outcome.FILE,
            rule="R3",
            reason=(
                f"{', '.join(sorted(additions))} now carries a value where none was stated "
                "before. A reader watching this land now sees a limit that was not visible "
                "to them before, so it is filed."
            ),
        )

    return Decision(
        outcome=Outcome.NOT_COVERED,
        rule=None,
        reason=(
            "This sub-area has been seen before and a new version exists, but no watched "
            "field differs from the last version stored. Something moved outside the five "
            "watched fields; the register alone cannot say what."
        ),
    )
