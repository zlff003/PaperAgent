"""SupervisorState — shared state for the supervisor graph."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    next: str  # "retrieval" | "analysis" | "library" | "FINISH"
    papers_context: list[dict[str, Any]]
    session_id: str  # current chat session
    agent_history: list[str]  # tracks which agents have been called, e.g. ["retrieval", "analysis"]
