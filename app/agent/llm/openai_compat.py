"""OpenAI-compatible chat completions over plain httpx.

Lets the hosted demo run on free tiers (Hugging Face Inference router,
Gemini's OpenAI endpoint, Groq, a local server) without adding a second SDK.
Tool calling follows the `tools=[{type: function}]` convention.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from .base import LLMError, LLMResponse, Message, ToolCall


def to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                item["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                    for c in m.tool_calls
                ]
            out.append(item)
        elif m.role == "tool":
            for r in m.tool_results:
                out.append({"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content})
    return out


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"],
    }} for t in tools]


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str, model: str, max_tokens: int = 4000,
                 timeout_s: float = 120.0, transport: Optional[httpx.BaseTransport] = None):
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout_s, transport=transport,
        )

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(system, messages),
            "max_tokens": self.max_tokens,
            "temperature": 0.4,
        }
        if tools:
            body["tools"] = to_openai_tools(tools)
            body["tool_choice"] = "auto"
        try:
            response = self.http.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM endpoint unreachable: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise LLMError(f"LLM endpoint returned HTTP {response.status_code}")
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM endpoint returned an unexpected payload") from exc

        calls: list[ToolCall] = []
        for i, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=call.get("id") or f"call_{i}", name=fn.get("name", ""), input=args))
        finish = choice.get("finish_reason") or "other"
        stop = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens",
                "content_filter": "refusal"}.get(finish, finish)
        usage = data.get("usage") or {}
        return LLMResponse(text=message.get("content") or "", tool_calls=calls, stop_reason=stop,
                           usage=usage, model=data.get("model"))


def build_provider(cfg) -> Optional[Any]:
    """Pick the provider from settings; None means 'scripted writer only'."""
    provider = cfg.llm_provider
    if provider == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(cfg.anthropic_api_key, model=cfg.anthropic_model, effort=cfg.anthropic_effort)
    if provider == "openai":
        return OpenAICompatProvider(cfg.openai_api_key, cfg.openai_base_url, cfg.openai_model)
    return None
