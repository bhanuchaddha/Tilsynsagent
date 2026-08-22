"""Change detection: compares two versions of a sub-area across the five
watched fields, producing a diff in the same shape as ``changed_fields`` in
the golden dataset - so eval cases and live changes are interchangeable with
``rules.engine.apply_rules``.
"""

from __future__ import annotations

from dataclasses import dataclass

from tilsynsagent.rules.engine import WATCHED_FIELDS


def _to_version_dict(record) -> dict:
    """The subset of a SubAreaRecord's fields that engine.apply_rules reads."""
    return {
        "status": record.status,
        "updated": record.datoopdt,
        "version": record.versionsnr,
        "maxbygnhjd": record.maxbygnhjd,
        "maxetager": record.maxetager,
        "bebygpct": record.bebygpct,
        "zonestatus": record.zonestatus,
        "anvendelsegenerel": record.anvendelsegenerel,
    }


def _values_differ(before, after) -> bool:
    """True columns differ, including blank-to-value and value-to-blank.

    None and empty string are both "blank" - the register uses both - so a
    None-to-"" transition (or vice versa) is not treated as a change.
    """

    def norm(v):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    return norm(before) != norm(after)


@dataclass(frozen=True)
class Diff:
    """A normalised change between two versions of one sub-area."""

    sub_area_key: tuple[int, str]
    before: dict
    after: dict
    changed_fields: dict

    @property
    def has_changes(self) -> bool:
        return bool(self.changed_fields)


def diff_versions(previous_record, current_record) -> Diff:
    """Compare two SubAreaRecords of the same sub-area across watched fields.

    ``previous_record`` is the last version seen for this sub-area;
    ``current_record`` is what was just fetched. Produces ``changed_fields``
    shaped exactly like the golden dataset: ``{field: {"before": x, "after": y}}``
    for each of the five watched fields that differs.
    """
    if previous_record.sub_area_key != current_record.sub_area_key:
        raise ValueError(
            "diff_versions compares two versions of the same sub-area; got "
            f"{previous_record.sub_area_key} vs {current_record.sub_area_key}"
        )

    before = _to_version_dict(previous_record)
    after = _to_version_dict(current_record)

    changed_fields = {
        field: {"before": before[field], "after": after[field]}
        for field in WATCHED_FIELDS
        if _values_differ(before[field], after[field])
    }

    return Diff(
        sub_area_key=current_record.sub_area_key,
        before=before,
        after=after,
        changed_fields=changed_fields,
    )
