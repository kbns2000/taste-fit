"""Provider-neutral message and response types for the writer agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: str  # user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str  # end_turn | tool_use | max_tokens | refusal | other
    usage: dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse: ...


class LLMError(Exception):
    pass
