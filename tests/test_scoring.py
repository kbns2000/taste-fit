from app.qloo.models import Demographics, Tag
from app.scoring import band_for, compute_fit, shared_taste_from_affinity, vectors_from_tags


def _demo(eid, **age):
    base = {"24_and_younger": 0, "25_to_29": 0, "30_to_34": 0, "35_to_44": 0, "45_to_54": 0, "55_and_older": 0}
    base.update(age)
    return Demographics(entity_id=eid, age=base, gender={"male": 0.1, "female": -0.1})


def test_identical_tag_profiles_fall_back_to_tag_cosine():
    a = {"t1": 0.9, "t2": 0.8, "t3": 0.7}
    names = {"t1": "T1", "t2": "T2", "t3": "T3"}
    fit = compute_fit(a, dict(a), names, _demo("a", **{"25_to_29": 0.5}), _demo("b", **{"25_to_29": 0.5}))
    assert fit.shared_basis == "tags"  # no cross-affinity given
    assert fit.shared_taste == 100.0
    assert fit.fresh_reach == 0.0
    assert fit.audience_alignment == 100.0
    assert fit.fit == 100.0 and fit.band == "strong"
    assert len(fit.shared_tags) == 3 and not fit.a_only_tags


def test_disjoint_profiles_score_zero_shared():
    a = {"t1": 0.9}
    b = {"t2": 0.9}
    fit = compute_fit(a, b, {"t1": "T1", "t2": "T2"}, None, None)
    assert fit.shared_taste == 0.0
    assert fit.fresh_reach == 100.0
    assert fit.audience_alignment == 50.0  # unknown demographics -> neutral
    assert fit.band == "weak"


def test_cross_affinity_maps_qloo_scale_to_0_100():
    # Qloo: 0.5 is "medium", 1.0 the maximum -> 0 and 100
    assert shared_taste_from_affinity(0.5, 0.5) == 0.0
    assert shared_taste_from_affinity(1.0, 1.0) == 100.0
    assert shared_taste_from_affinity(0.3, None) == 0.0  # below medium clips at 0
    assert shared_taste_from_affinity(None, 0.9) == 80.0  # one direction is enough
    assert shared_taste_from_affinity(None, None) is None
    # live Patagonia x Bon Iver (2026-10-24): 0.924 and 0.986 -> 91
    assert round(shared_taste_from_affinity(0.924, 0.986), 1) == 91.0


def test_cross_affinity_overrides_saturated_tags():
    a = {f"a{i}": 1.0 for i in range(6)}
    b = {**{f"a{i}": 1.0 for i in range(6)}, **{f"b{i}": 1.0 for i in range(4)}}  # 40% new
    fit = compute_fit(a, b, {}, None, None, a_to_b=0.6, b_to_a=0.7)
    assert fit.shared_basis == "cross_affinity"
    assert fit.shared_taste == 30.0  # tags alone would say ~77
    assert fit.fresh_reach == 40.0  # shown, not scored
    assert fit.fit == round(0.6 * 30.0 + 0.4 * 50.0, 1)
    assert fit.a_to_b == 0.6 and fit.b_to_a == 0.7
    assert fit.formula == "fit = 0.6*shared_taste + 0.4*audience_alignment"


def test_non_taste_tags_are_dropped():
    rows_a = [Tag(tag_id="star", name="Three Star", affinity=1.0, type="urn:tag:hotel_rating:qloo"),
              Tag(tag_id="visa", name="VISA", affinity=1.0, type="urn:tag:credit_card"),
              Tag(tag_id="folk", name="folk", affinity=1.0, type="urn:tag:genre:qloo")]
    rows_b = [Tag(tag_id="star", name="Three Star", affinity=1.0, type="urn:tag:hotel_rating:qloo"),
              Tag(tag_id="indie", name="indie", affinity=0.9, type="urn:tag:genre:qloo")]
    a, b, _ = vectors_from_tags(rows_a, rows_b)
    assert a == {"folk": 1.0} and b == {"indie": 0.9}


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
