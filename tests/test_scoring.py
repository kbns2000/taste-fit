from app.qloo.models import Demographics, Tag
from app.scoring import band_for, compute_fit, vectors_from_tags


def _demo(eid, **age):
    base = {"24_and_younger": 0, "25_to_29": 0, "30_to_34": 0, "35_to_44": 0, "45_to_54": 0, "55_and_older": 0}
    base.update(age)
    return Demographics(entity_id=eid, age=base, gender={"male": 0.1, "female": -0.1})


def test_identical_profiles_score_high_shared_but_low_balance():
    a = {"t1": 0.9, "t2": 0.8, "t3": 0.7}
    names = {"t1": "T1", "t2": "T2", "t3": "T3"}
    fit = compute_fit(a, dict(a), names, _demo("a", **{"25_to_29": 0.5}), _demo("b", **{"25_to_29": 0.5}))
    assert fit.shared_taste == 100.0
    assert fit.fresh_reach == 0.0
    assert fit.balance == 0.0  # clones bring no new reach
    assert fit.audience_alignment == 100.0
    assert len(fit.shared_tags) == 3 and not fit.a_only_tags


def test_disjoint_profiles_score_zero_shared():
    a = {"t1": 0.9}
    b = {"t2": 0.9}
    fit = compute_fit(a, b, {"t1": "T1", "t2": "T2"}, None, None)
    assert fit.shared_taste == 0.0
    assert fit.fresh_reach == 100.0
    assert fit.audience_alignment == 50.0  # unknown demographics -> neutral
    assert fit.band == "weak"


def test_sweet_spot_balance():
    a = {f"a{i}": 0.9 for i in range(6)}
    b = {**{f"a{i}": 0.9 for i in range(6)}, **{f"b{i}": 0.8 for i in range(4)}}  # 40% new
    fit = compute_fit(a, b, {}, None, None)
    assert fit.fresh_reach == 40.0
    assert fit.balance == 100.0


def test_bands():
    assert band_for(70) == "strong"
    assert band_for(69.9) == "workable"
    assert band_for(54.9) == "weak"


def test_vectors_from_tags():
    a, b, _ = vectors_from_tags([Tag(tag_id="x", name="X", affinity=0.5)], [Tag(tag_id="y", name="Y", affinity=0.4)])
    assert a == {"x": 0.5} and b == {"y": 0.4}


def test_demographic_segments_flagged():
    fit = compute_fit({"t": 1.0}, {"t": 1.0}, {"t": "T"},
                      _demo("a", **{"25_to_29": 0.5, "55_and_older": 0.4}),
                      _demo("b", **{"25_to_29": 0.4, "55_and_older": -0.4}))
    assert fit.agreeing_segments == ["25-29"]
    assert fit.diverging_segments == ["55+"]
