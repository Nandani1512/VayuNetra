"""Agent state schema."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    user_message: str
    intent: Literal["forecast", "attribution", "enforce", "advisory", "general"]
    tool_result: Any
    response: str
