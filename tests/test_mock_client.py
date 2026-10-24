from app.qloo.client import CallRecord
from app.qloo.mock import MockQlooClient, geohash_encode


def test_search_is_fuzzy_and_typed():
    m = MockQlooClient()
    assert m.search("patagonia")[0].name == "Patagonia"
    assert m.search("bon iver", types=["artist"])[0].type == "artist"
    assert m.search("nyc coffee shop that does not exist") == []
    assert all(e.type == "brand" for e in m.search("blue", types=["brand"]))


def test_trace_records_every_call():
    m = MockQlooClient()
    trace: list[CallRecord] = []
    pat = m.search("patagonia", trace=trace)[0]
    m.taste_tags([pat.entity_id], trace=trace)
    m.demographics([pat.entity_id], trace=trace)
    assert [r.endpoint for r in trace] == ["/search", "/v2/insights", "/v2/insights"]
    assert trace[1].params["filter.type"] == "urn:tag"
    assert trace[2].params["filter.type"] == "urn:demographics"
    assert all(r.source == "mock" for r in trace)


def test_recommend_is_deterministic_and_explains():
    m = MockQlooClient()
    a = m.search("patagonia")[0].entity_id
    b = m.search("bon iver")[0].entity_id
    r1 = m.recommend([a, b], "artist", take=5)
    r2 = m.recommend([a, b], "artist", take=5)
    assert [e.name for e in r1] == [e.name for e in r2]
    assert all(0 <= (e.affinity or 0) <= 1 for e in r1)
    assert a not in [e.entity_id for e in r1] and b not in [e.entity_id for e in r1]
    for e in r1:
        assert set(e.explainability) == {a, b}
        assert abs(sum(e.explainability.values()) - 1) < 0.01


def test_recommend_place_filters_by_city():
    m = MockQlooClient()
    a = m.search("patagonia")[0].entity_id
    places = m.recommend([a], "place", take=10, filter_location="Brooklyn")
    assert places and all(p.locality == "New York" for p in places)
    assert m.recommend([a], "place", take=10, filter_location="Atlantis") == []


def test_heatmap_and_compare():
    m = MockQlooClient()
    a = m.search("patagonia")[0].entity_id
    b = m.search("supreme")[0].entity_id
    cells = m.heatmap([a, b], "sf")
    assert len(cells) == 6 * 9
    assert all(c.geohash for c in cells)
    assert max(c.affinity_rank for c in cells) == 1.0
    cat = m.compare([a], [b])
    assert cat.a_tags and cat.b_tags
    assert {t.tag_id for t in cat.common} == {t.tag_id for t in cat.a_tags} & {t.tag_id for t in cat.b_tags}
    assert m.heatmap([a], "nowhere") == []


def test_geohash_known_value():
    # 57.64911, 10.40744 -> u4pruydqqvj (Wikipedia example)
    assert geohash_encode(57.64911, 10.40744, precision=11) == "u4pruydqqvj"
