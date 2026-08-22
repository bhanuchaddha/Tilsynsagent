from tilsynsagent.detect import diff_versions
from tilsynsagent.sources.plandata import SubAreaRecord


def make_record(**overrides) -> SubAreaRecord:
    base = dict(
        feature_id="f.1",
        lokplan_id=100,
        delnr="A",
        komnr=253,
        kommunenavn="Greve",
        versionsnr=1,
        status="V",
        datoopdt="2026-01-01T00:00:00.000Z",
        maxbygnhjd=8.5,
        maxetager=2,
        bebygpct=40,
        zonestatus=None,
        anvendelsegenerel="Boligområde",
        doklink="https://dokument.plandata.dk/x.pdf",
    )
    base.update(overrides)
    return SubAreaRecord(**base)


def test_no_change_produces_empty_diff():
    a = make_record()
    b = make_record(feature_id="f.2", versionsnr=2, datoopdt="2026-02-01T00:00:00.000Z")
    d = diff_versions(a, b)
    assert d.changed_fields == {}
    assert not d.has_changes


def test_dimensional_change_detected():
    a = make_record()
    b = make_record(feature_id="f.2", versionsnr=2, maxbygnhjd=12.5)
    d = diff_versions(a, b)
    assert d.changed_fields == {"maxbygnhjd": {"before": 8.5, "after": 12.5}}


def test_none_to_empty_string_not_a_change():
    a = make_record(zonestatus=None)
    b = make_record(feature_id="f.2", versionsnr=2, zonestatus="")
    d = diff_versions(a, b)
    assert d.changed_fields == {}


def test_blank_to_value_detected():
    a = make_record(zonestatus=None)
    b = make_record(feature_id="f.2", versionsnr=2, zonestatus="Byzone")
    d = diff_versions(a, b)
    assert d.changed_fields == {"zonestatus": {"before": None, "after": "Byzone"}}


def test_mismatched_sub_area_raises():
    a = make_record(delnr="A")
    b = make_record(delnr="B")
    try:
        diff_versions(a, b)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_unwatched_field_change_ignored():
    a = make_record(status="F")
    b = make_record(feature_id="f.2", versionsnr=2, status="V")
    d = diff_versions(a, b)
    assert d.changed_fields == {}
