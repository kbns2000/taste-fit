import time

from fastapi.testclient import TestClient

from app.agent.pipeline import execute
from app.config import Settings
from app.main import app


def _cfg():
    cfg = Settings()
    cfg.qloo_mode = "mock"
    return cfg


def test_fit_check_end_to_end_on_mock():
    events = []
    res = execute({"mode": "fit", "a": "Patagonia", "b": "Bon Iver", "city": "New York"},
                  lambda k, p: events.append(k), _cfg())
    assert res["fit"]["band"] in ("strong", "workable", "weak")
    assert res["qloo_calls"] <= 16
    assert {"artist", "podcast", "place"} <= set(res["picks"])
    assert res["zones"] and res["heatmap"]
    assert res["verification"]["passed"], res["verification"]
    assert res["catalog"]["a_count"] > 0 and res["catalog_cites"]
    assert any(e["endpoint"] == "/v2/analysis/compare" for e in res["ledger"])
    assert res["brief"]["activations"] and all(a["cites"] for a in res["brief"]["activations"])
    ids = {e["id"] for e in res["ledger"]}
    for why in res["brief"]["why"]:
        assert set(why["cites"]) <= ids
    assert "step" in events and events[-1] == "done"


def test_fit_check_without_city_skips_map():
    res = execute({"mode": "fit", "a": "Patagonia", "b": "Supreme"}, lambda k, p: None, _cfg())
    assert res["zones"] == [] and "place" not in res["picks"]
    assert res["fit"]["band"] == "weak" and res["brief"]["verdict"] == "no_go"


def test_scout_ranks_candidates():
    res = execute({"mode": "scout", "a": "Allbirds", "city": "San Francisco"}, lambda k, p: None, _cfg())
    assert len(res["ranked"]) == 3
    fits = [r["fit"]["fit"] for r in res["ranked"]]
    assert fits == sorted(fits, reverse=True)
    assert res["qloo_calls"] <= 16


def _wait(client, rid):
    for _ in range(100):
        st = client.get(f"/api/runs/{rid}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_api_run_and_sse():
    client = TestClient(app)
    assert client.get("/api/health").json()["qloo_source"] == "mock"
    assert client.get("/").status_code == 200
    r = client.post("/api/runs", json={"mode": "fit", "a": "Glossier", "b": "Blue Bottle Coffee", "city": "San Francisco"})
    assert r.status_code == 202
    rid = r.json()["run_id"]
    kinds = []
    with client.stream("GET", f"/api/runs/{rid}/events") as s:
        for line in s.iter_lines():
            if line.startswith("event:"):
                kinds.append(line.split(":", 1)[1].strip())
    assert kinds[0] == "step" and kinds[-1] == "end" and "fit" in kinds
    st = _wait(client, rid)
    assert st["status"] == "done" and st["result"]["brief"]["headline"]


def test_api_validation_and_unknown_entity():
    client = TestClient(app)
    assert client.post("/api/runs", json={"mode": "fit", "a": "Patagonia"}).status_code == 422
    rid = client.post("/api/runs", json={"mode": "fit", "a": "Zzz Unknown", "b": "Bon Iver"}).json()["run_id"]
    st = _wait(client, rid)
    assert st["status"] == "failed" and "Could not find" in st["error"]
    assert client.get("/api/runs/nope").status_code == 404
    found = client.post("/api/resolve", json={"query": "pata"}).json()["results"]
    assert found[0]["name"] == "Patagonia"
