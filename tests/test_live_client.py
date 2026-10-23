"""Live adapter tests with a fake transport: no network, no key value in logs."""

import json

import httpx
import pytest

from app.qloo.client import LiveQlooClient
from app.qloo.models import QlooError, QuotaExceeded


def _client(handler, **kw):
    transport = httpx.MockTransport(handler)
    return LiveQlooClient(api_key="test-key-not-real", base_url="https://hackathon.api.qloo.com",
                          transport=transport, **kw)


def test_search_sends_key_header_and_parses_entities():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"success": True, "results": {"entities": [
            {"name": "Patagonia", "entity_id": "E1", "type": "urn:entity", "subtype": "urn:entity:brand",
             "popularity": 0.9, "properties": {"image": {"url": "http://img"}}, "tags": [{"name": "Outdoor"}]}]}})

    c = _client(handler)
    trace = []
    out = c.search("patagonia", types=["brand"], trace=trace)
    assert seen["key"] == "test-key-not-real"
    assert "types=urn%3Aentity%3Abrand" in seen["url"]
    assert out[0].type == "brand" and out[0].image == "http://img" and out[0].tags == ["Outdoor"]
    assert trace[0].status == "ok" and trace[0].count == 1 and trace[0].source == "live"
    assert "test-key" not in json.dumps(trace[0].as_dict())


def test_insights_params_match_docs():
    seen = {}

    def handler(request: httpx.Request):
        seen.setdefault("urls", []).append(str(request.url))
        return httpx.Response(200, json={"success": True, "results": {"entities": []}})

    c = _client(handler)
    c.recommend(["A", "B"], "artist", take=5, signal_location="New York", exclude_ids=["A"])
    url = seen["urls"][0]
    assert "filter.type=urn%3Aentity%3Aartist" in url
    assert "signal.interests.entities=A%2CB" in url
    assert "feature.explainability=true" in url
    assert "signal.location.query=New+York" in url
    assert "filter.exclude.entities=A" in url


def test_404_means_empty_and_cache_hits_are_marked():
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        return httpx.Response(404, json={"error": "no results"})

    c = _client(handler)
    trace = []
    assert c.search("zzz", trace=trace) == []
    assert c.search("zzz", trace=trace) == []
    assert calls["n"] == 1
    assert trace[0].status == "empty" and trace[1].cached is True


def test_429_retries_with_retry_after_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"results": {"tags": [{"tag_id": "t", "name": "T", "query": {"affinity": 0.7}}]}})

    c = _client(handler)
    rows = c.taste_tags(["A"])
    assert calls["n"] == 2 and rows[0].affinity == 0.7


def test_auth_error_raises_without_leaking():
    def handler(request: httpx.Request):
        return httpx.Response(401, json={"message": "unauthorized"})

    c = _client(handler)
    with pytest.raises(QlooError) as exc:
        c.search("x")
    assert "test-key" not in str(exc.value)


def test_daily_budget_guard():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"results": {"entities": []}})

    c = _client(handler, daily_budget=1)
    c.search("one")
    with pytest.raises(QuotaExceeded):
        c.search("two")
    assert c.budget_status()["calls_today"] == 1
