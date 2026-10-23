"""Claude via the official Anthropic SDK (beta Messages API).

- strict tools: the SDK validates tool inputs against the JSON schema, so
  the writer never receives a malformed brief;
- `fallbacks="default"` with the server-side fallback beta keeps the demo
  up if the primary model is briefly unavailable;
- effort comes from `ANTHROPIC_EFFORT` (medium by default, cheap and fast).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .base import LLMError, LLMResponse, Message, ToolCall

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for call in m.tool_calls:
                blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no content)"}]})
        elif m.role == "tool":
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": r.is_error}
                for r in m.tool_results
            ]})
    return out


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", effort: str = "medium",
                 max_tokens: int = 6000, client: Optional[Any] = None):
        import anthropic  # local import keeps the dependency optional at import time

        self.model = model
        self.effort = effort if effort in ("low", "medium", "high", "xhigh", "max") else "medium"
        self.max_tokens = max_tokens
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120.0)

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        import anthropic

        strict_tools = [{**t, "strict": True} for t in tools]
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=to_anthropic_messages(messages),
                tools=strict_tools,
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIStatusError as exc:  # 4xx/5xx after SDK retries
            raise LLMError(f"Anthropic API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Anthropic API connection error") from exc

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                raw = block.input
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except json.JSONDecodeError:
                        raw = {}
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(raw or {})))
        stop = response.stop_reason or "other"
        usage = {}
        try:
            usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        except AttributeError:
            pass
        return LLMResponse(text="\n".join(text_parts), tool_calls=calls, stop_reason=stop, usage=usage,
                           model=getattr(response, "model", None))
