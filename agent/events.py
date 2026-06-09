"""
Agent event model for SSE streaming.

Each event carries a monotonic seq counter and epoch timestamp t.
The discriminated union AgentEvent covers the full lifecycle of a run.
EventSink is a Protocol so the loop stays decoupled from transport.
RecordingSink captures events in-memory for tests and diagnostics.
"""
from __future__ import annotations

import time
from typing import Annotated, Any, Literal, Union
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class _Base(BaseModel):
    seq: int
    t: float  # epoch seconds


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class RunStarted(_Base):
    type: Literal["run_started"] = "run_started"
    question: str
    config_summary: dict[str, Any]


class AssistantText(_Base):
    type: Literal["assistant_text"] = "assistant_text"
    text: str


class ToolCall(_Base):
    type: Literal["tool_call"] = "tool_call"
    name: str
    input: dict[str, Any]


class ToolResultEvent(_Base):
    type: Literal["tool_result"] = "tool_result"
    name: str
    summary: dict[str, Any]


class CompactionEvent(_Base):
    type: Literal["compaction"] = "compaction"
    before_tokens: int
    after_tokens: int


class ErrorEvent(_Base):
    type: Literal["error"] = "error"
    kind: Literal["tool", "loop"]
    message: str


class FinalAnswer(_Base):
    type: Literal["final_answer"] = "final_answer"
    answer: str


class RunFinished(_Base):
    type: Literal["run_finished"] = "run_finished"
    turns: int
    usage: dict[str, Any] | None = None


# Discriminated union — Pydantic resolves the correct subtype on parse.
AgentEvent = Annotated[
    Union[
        RunStarted,
        AssistantText,
        ToolCall,
        ToolResultEvent,
        CompactionEvent,
        ErrorEvent,
        FinalAnswer,
        RunFinished,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Sink protocol + recording implementation
# ---------------------------------------------------------------------------

@runtime_checkable
class EventSink(Protocol):
    def emit(self, event: _Base) -> None: ...


class RecordingSink:
    """Collects emitted events in a list. Used in tests and diagnostics."""

    def __init__(self) -> None:
        self.events: list[_Base] = []

    def emit(self, event: _Base) -> None:
        self.events.append(event)
