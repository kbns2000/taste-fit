import json

import httpx

from app.agent.ledger import Ledger
from app.agent.llm.anthropic_provider import AnthropicProvider, to_anthropic_messages
from app.agent.llm.base import Message, ToolCall, ToolResult
from app.agent.llm.openai_compat import OpenAICompatProvider, to_openai_messages
from app.agent.pipeline import run_fit_check
from app.agent.verifier import verify_brief
from app.config import Settings
from app.qloo.client import CallRecord
from app.qloo.mock import MockQlooClient


def _ledger():
    led = Ledger()
    led.trace.append(CallRecord(endpoint="/search", params={}, count=1))
    led.absorb("resolve", ["Patagonia"])
    led.trace.append(CallRecord(endpoint="/v2/insights", params={}, count=2))
    led.absorb("artists", ["Bon Iver", "Fleet Foxes"])
    return led


def test_verifier_drops_unknown_entities_and_bad_cites():
    brief = {"headline": "A x B: 70/100", "verdict": "go",
             "why": [{"text": "x", "cites": ["Q1"]}, {"text": "y", "cites": ["Q9"]}],
             "activations": [{"title": "t", "format": "f", "entities": ["Fleet Foxes", "Taylor Swift"], "rationale": "r", "cites": ["Q2"]}]}
    out = verify_brief(brief, _ledger(), {"fit": 71.0, "band": "strong"}, ["Patagonia", "Bon Iver"])
    assert not out["passed"]
    texts = " ".join(i["text"] for i in out["issues"])
    assert "Q9" in texts and "Taylor Swift" in texts
    assert brief["activations"][0]["entities"] == ["Fleet Foxes"]
    assert brief["activations"][0]["unverified_entities"] == ["Taylor Swift"]


def test_verifier_checks_headline_score_and_verdict():
    brief = {"headline": "A x B: 40/100", "verdict": "go", "why": [{"text": "x", "cites": ["Q1"]}], "activations": []}
    out = verify_brief(brief, _ledger(), {"fit": 72.4, "band": "strong"}, [])
    assert any("headline" in i["where"] for i in out["issues"])
    brief = {"headline": "ok", "verdict": "go", "why": [{"text": "x", "cites": ["Q1"]}], "activations": []}
    out = verify_brief(brief, _ledger(), {"fit": 30.0, "band": "weak"}, [])
    assert out["passed"] and any(i["where"] == "verdict" for i in out["issues"])


def test_message_conversion_round_trip():
    msgs = [Message("user", "hi"), Message("assistant", "ok", [ToolCall("c1", "heatmap", {"city": "NYC"})]),
            Message("tool", tool_results=[ToolResult("c1", "{}", False)])]
    a = to_anthropic_messages(msgs)
    assert a[1]["content"][1]["type"] == "tool_use" and a[2]["content"][0]["tool_use_id"] == "c1"
    o = to_openai_messages("sys", msgs)
    assert o[0]["role"] == "system" and o[2]["tool_calls"][0]["function"]["name"] == "heatmap"
    assert o[3]["role"] == "tool" and o[3]["tool_call_id"] == "c1"


class _FakeAnthropic:
    """Minimal stand-in for anthropic.Anthropic with the beta.messages.create surface."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.beta = type("B", (), {})()
        self.beta.messages = type("M", (), {})()
        self.beta.messages.create = self._create

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        blocks = self.script.pop(0)
        content = []
        for b in blocks:
            if b["type"] == "text":
                content.append(type("T", (), {"type": "text", "text": b["text"]})())
            else:
                content.append(type("U", (), {"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]})())
        usage = type("Usage", (), {"input_tokens": 10, "output_tokens": 5})()
        return type("R", (), {"content": content, "stop_reason": "tool_use", "usage": usage, "model": "fake"})()


def test_anthropic_agent_loop_calls_tools_then_finalizes():
    mock = MockQlooClient()
    final = {"headline": "Patagonia x Bon Iver: strong fit (75.6/100).", "verdict": "go",
             "why": [{"text": "shared core", "cites": ["Q3"]}, {"text": "aligned", "cites": ["Q4", "Q5"]}],
             "target_segments": ["25-29"], "message_angles": ["a", "b"], "channels": ["x", "y"],
             "activations": [{"title": "Playlist", "format": "playlist", "entities": ["Fleet Foxes", "Made Up Band"],
                              "rationale": "r", "cites": ["Q6"]},
                             {"title": "Pop-up", "format": "event", "entities": ["Brooklyn Boulders"], "rationale": "r", "cites": ["Q10"]}],
             "risks": ["r"], "next_steps": ["s", "t"]}
    fake = _FakeAnthropic([
        [{"type": "text", "text": "Let me check places."},
         {"type": "tool_use", "id": "t1", "name": "recommend", "input": {"target_type": "place", "city": "New York", "take": 4}}],
        [{"type": "tool_use", "id": "t2", "name": "finalize_brief", "input": final}],
    ])
    provider = AnthropicProvider(api_key="not-real", client=fake)
    cfg = Settings()
    cfg.qloo_mode = "mock"
    events = []
    res = run_fit_check("Patagonia", "Bon Iver", "New York", mock, provider, lambda k, p: events.append((k, p)), cfg)
    assert res["writer"]["provider"] == "anthropic" and res["writer"]["tool_calls"] == 1
    assert ("agent_tool", {"tool": "recommend", "input": {"target_type": "place", "city": "New York", "take": 4}}) in events
    # strict tools + fallbacks were requested
    kwargs = fake.calls[0]
    assert all(t["strict"] for t in kwargs["tools"]) and kwargs["fallbacks"] == "default"
    assert kwargs["output_config"] == {"effort": "medium"}
    # verifier removed the invented band and kept the real one
    act = res["brief"]["activations"][0]
    assert act["entities"] == ["Fleet Foxes"] and act["unverified_entities"] == ["Made Up Band"]
    assert not res["verification"]["passed"]


def test_llm_failure_falls_back_to_scripted_writer():
    class Boom:
        name, model = "anthropic", "x"

        def complete(self, *a, **k):
            from app.agent.llm.base import LLMError
            raise LLMError("down")

    cfg = Settings()
    cfg.qloo_mode = "mock"
    res = run_fit_check("Patagonia", "Bon Iver", None, MockQlooClient(), Boom(), lambda k, p: None, cfg)
    assert res["writer"]["fallback"] == "scripted" and res["brief"]["headline"]
    assert res["verification"]["passed"]


def test_openai_compat_provider_parses_tool_calls():
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["tools"][0]["type"] == "function"
        return httpx.Response(200, json={"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {
            "content": None, "tool_calls": [{"id": "c9", "type": "function", "function": {
                "name": "find_tags", "arguments": json.dumps({"query": "coffee"})}}]}}], "usage": {"total_tokens": 3}})

    p = OpenAICompatProvider("k", "https://example.test/v1", "m", transport=httpx.MockTransport(handler))
    out = p.complete("sys", [Message("user", "hi")], [{"name": "find_tags", "description": "d", "input_schema": {"type": "object", "properties": {}}}])
    assert out.stop_reason == "tool_use" and out.tool_calls[0].name == "find_tags"
    assert out.tool_calls[0].input == {"query": "coffee"}
