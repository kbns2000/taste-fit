"""Regressions from the pre-submission review: city 400 handling, ledger integrity,
brief validation fallback, proxy IP parsing and SSE resume."""
import time

from fastapi.testclient import TestClient

from app.agent.ledger import Ledger
from app.agent.llm.anthropic_provider import AnthropicProvider
from app.agent.pipeline import RunFailed, execute, run_fit_check
from app.agent.tools import ToolBox
from app.config import Settings
from app.main import _client_ip, app
from app.qloo.client import CallRecord
from app.qloo.mock import MockQlooClient
from app.qloo.models import QlooError


def _cfg():
    cfg = Settings()
    cfg.qloo_mode = "mock"
    return cfg


class _LiveLike(MockQlooClient):
    """Mock that reports itself as live and rejects the city with HTTP 400, like Qloo does for an unknown locality."""
    source = "live"

    def __init__(self, fail_demographics: bool = False):
        super().__init__()
        self.fail_demographics = fail_demographics

    def recommend(self, entity_ids, target_type, take=10, filter_location=None, signal_location=None, **kw):
        if filter_location or signal_location:
            raise QlooError("HTTP 400: could not resolve locality", status=400)
        return super().recommend(entity_ids, target_type, take=take, **kw)

    def heatmap(self, entity_ids, location_query, trace=None):
        raise QlooError("HTTP 400: could not resolve locality", status=400)

    def demographics(self, entity_ids, trace=None):
        if self.fail_demographics:  # the live client files the record before raising; mirror that
            if trace is not None:
                trace.append(CallRecord(endpoint="/v2/insights", params={"filter.type": "urn:demographics"},
                                        status="error", error="QLOO_HTTP_503: upstream", http_status=503))
            raise QlooError("HTTP 503: upstream", status=503, retryable=True)
        return super().demographics(entity_ids, trace=trace)


def test_unknown_city_warns_and_continues_on_live_data():
    events = []
    res = execute({"mode": "fit", "a": "Patagonia", "b": "Bon Iver", "city": "Nowheresville"},
                  lambda k, p: events.append((k, p)), _cfg(), client=_LiveLike())
    warns = [p["text"] for k, p in events if k == "warn"]
    assert any("could not find the city 'Nowheresville'" in w for w in warns)
    assert res["source"] == "live" and "fallback_reason" not in res  # a bad city is not an outage
    assert ("reset", {"reason": "x"}) not in events and not [k for k, _ in events if k == "reset"]
    assert res["picks"]["artist"] and res["zones"] == []


def test_unknown_city_in_scout_retries_without_city():
    events = []
    res = execute({"mode": "scout", "a": "Allbirds", "city": "Nowheresville"},
                  lambda k, p: events.append((k, p)), _cfg(), client=_LiveLike())
    assert res["city"] is None and len(res["ranked"]) == 3 and res["source"] == "live"
    assert any("could not find the city" in p["text"] for k, p in events if k == "warn")


def test_live_outage_falls_back_to_demo_and_says_so():
    events = []
    res = execute({"mode": "fit", "a": "Patagonia", "b": "Bon Iver"},
                  lambda k, p: events.append((k, p)), _cfg(), client=_LiveLike(fail_demographics=True))
    assert res["source"] == "mock" and "503" in res["fallback_reason"]
    assert [k for k, _ in events if k == "reset"] == ["reset"]


def test_failed_call_is_filed_in_the_ledger():
    ledger = Ledger()
    box = ToolBox(_LiveLike(fail_demographics=True), ledger, max_calls=16)
    a = box.resolve("Patagonia")
    try:
        box.demographics(a.entity_id)
    except QlooError:
        pass
    purposes = [row["purpose"] for row in ledger.as_list()]
    assert any(p.endswith("(failed)") for p in purposes), purposes


class _FakeAnthropic:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.beta = type("B", (), {})()
        self.beta.messages = type("M", (), {})()
        self.beta.messages.create = self._create

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        blocks = self.script.pop(0)
        content = [type("U", (), {"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]})() for b in blocks]
        usage = type("Usage", (), {"input_tokens": 1, "output_tokens": 1})()
        return type("R", (), {"content": content, "stop_reason": "tool_use", "usage": usage, "model": "fake"})()


def test_invalid_finalize_payload_gets_one_retry_then_scripted_fallback():
    bad = {"headline": "no verdict here", "why": "not a list"}
    fake = _FakeAnthropic([
        [{"id": "f1", "name": "finalize_brief", "input": bad}],
        [{"id": "f2", "name": "finalize_brief", "input": bad}],
    ])
    provider = AnthropicProvider(api_key="not-real", client=fake)
    res = run_fit_check("Patagonia", "Bon Iver", None, MockQlooClient(), provider, lambda k, p: None, _cfg())
    assert len(fake.calls) == 2  # one resubmit chance, then give up
    assert res["writer"]["fallback"] == "scripted" and "schema" in res["writer"]["error"]
    assert res["brief"]["headline"] and res["verification"]["passed"]


def test_client_ip_uses_last_forwarded_hop():
    class Req:
        def __init__(self, headers, host="10.0.0.1"):
            self.headers = headers
            self.client = type("C", (), {"host": host})()

    assert _client_ip(Req({"x-forwarded-for": "1.1.1.1, 2.2.2.2"})) == "2.2.2.2"
    assert _client_ip(Req({"x-forwarded-for": " , "})) == "10.0.0.1"
    assert _client_ip(Req({})) == "10.0.0.1"


def _wait(client, rid):
    for _ in range(200):
        st = client.get(f"/api/runs/{rid}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_sse_resume_with_last_event_id_and_sequence_ids():
    client = TestClient(app)
    rid = client.post("/api/runs", json={"mode": "fit", "a": "Patagonia", "b": "Supreme"}).json()["run_id"]
    _wait(client, rid)
    ids = []
    with client.stream("GET", f"/api/runs/{rid}/events") as s:
        for line in s.iter_lines():
            if line.startswith("id:"):
                ids.append(int(line.split(":", 1)[1]))
    assert ids == sorted(ids) and ids[0] == 1 and len(ids) > 3
    cut = ids[len(ids) // 2]
    resumed = []
    with client.stream("GET", f"/api/runs/{rid}/events", headers={"Last-Event-ID": str(cut)}) as s:
        for line in s.iter_lines():
            if line.startswith("id:"):
                resumed.append(int(line.split(":", 1)[1]))
    assert resumed and resumed[0] == cut + 1 and resumed[-1] == ids[-1]


def test_api_rejects_bad_type_hints():
    client = TestClient(app)
    r = client.post("/api/runs", json={"mode": "fit", "a": "Patagonia", "b": "Bon Iver", "a_type": "urn:entity:brand; drop"})
    assert r.status_code == 422
