"""TypedDict state schema for the QA graph."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class QAState(TypedDict):
    question: str
    top_k: int
    # Routing
    question_type: str  # "simple" | "comparison" | "review" | "complex"
    # Decomposition (complex questions)
    sub_questions: list[str]
    # Retrieval
    retrieved_papers: list[dict[str, Any]]
    # Generation
    draft_answer: str
    # Self-critique
    critique: str
    critique_passed: bool
    # Control
    iteration: int
    max_iterations: int
    # Output
    final_answer: str
    conversation_id: str
    # Error
    error: str
