"""
Supervisor Graph — LangGraph StateGraph where an LLM supervisor dynamically
routes between Retrieval, Analysis, and Library sub-agents.
A Responder node handles final streaming output.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from app.core.langchain_factory import get_chat_model
from app.supervisor.analysis_agent import analysis_node
from app.supervisor.library_agent import library_node
from app.supervisor.responder import responder_node
from app.supervisor.retrieval_agent import retrieval_node
from app.supervisor.state import SupervisorState

chat = get_chat_model()


# ── Supervisor decision model ──────────────────────────────────────


class SupervisorDecision(BaseModel):
    next: Literal["retrieval", "analysis", "library", "FINISH"]
    reason: str = ""


SUPERVISOR_SYSTEM_PROMPT = """You are PaperAgent Supervisor, orchestrating a research paper assistant.

Available agents:
- **retrieval**: Semantic search for papers by topic, concept, or keywords.
- **analysis**: Answer research questions, compare papers, generate literature reviews. Requires papers context from retrieval first.
- **library**: Manage the paper library — list papers, check status, show stats, favorites, delete, re-extract.
- **FINISH**: All tasks are complete — the responder will present results to the user.

Routing rules:
1. User asks to search/find papers by topic/keyword → **retrieval**
2. User asks a research question (what/how/why/compare/review) → **retrieval** first, then **analysis**
3. User asks to manage the library (list/delete/status/stats/favorites/re-extract) → **library**
4. After retrieval and user only wanted to find papers → **FINISH**
5. After analysis returns an answer → **FINISH**
6. After library completes its operation → **FINISH**
7. If user greets or chats casually → **FINISH**

Follow-up handling:
- When the user says "the first one", "paper [1]", "that paper", or similar, they are referencing papers from earlier in the conversation.
- If papers_context is available from a prior turn, go directly to **analysis** for follow-up questions about those papers.
- Only call retrieval again if the follow-up is about a NEW topic not covered by existing papers_context.

Respond with the NEXT single agent to call."""


def _messages_to_text(messages: list) -> str:
    """Convert LangChain messages to a readable conversation transcript."""
    lines = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            lines.append(f"User: {msg.content}")
        elif isinstance(msg, AIMessage):
            # Truncate long agent responses for the supervisor's context
            content = msg.content
            if len(content) > 800:
                content = content[:800] + "..."
            lines.append(f"Assistant: {content}")
        elif isinstance(msg, SystemMessage):
            lines.append(f"System: {msg.content}")
    return "\n".join(lines)


async def supervisor_node(state: SupervisorState) -> dict[str, Any]:
    """LLM supervisor: reads conversation and decides the next agent."""
    messages = state.get("messages", [])

    conversation = _messages_to_text(messages)

    prompt = (
        f"{SUPERVISOR_SYSTEM_PROMPT}\n\n"
        f"Conversation so far:\n{conversation}\n\n"
        "Which agent should act next? "
        "Choose: retrieval, analysis, library, or FINISH."
    )

    try:
        structured = chat.with_structured_output(SupervisorDecision, method="json_schema")
        decision: SupervisorDecision = await structured.ainvoke(prompt)
        return {"next": decision.next}
    except Exception:
        # Fallback: use agent_history to decide
        history = state.get("agent_history", [])
        papers = state.get("papers_context", [])

        if len(history) >= 4:
            return {"next": "FINISH"}
        if not papers and messages:
            return {"next": "retrieval"}
        return {"next": "FINISH"}


# ── Routing ────────────────────────────────────────────────────────

MAX_AGENT_CALLS = 4


def _route(state: SupervisorState) -> Literal["retrieval", "analysis", "library", "responder"]:
    """Code-level routing guard. Enforces hard limits regardless of LLM decision."""
    next_agent = state.get("next", "FINISH")
    history = state.get("agent_history", [])

    # Guard 1: hard cap on total agent calls → force responder
    if len(history) >= MAX_AGENT_CALLS:
        return "responder"

    # Guard 2: never call the same agent twice in a row → force responder
    if history and next_agent == history[-1]:
        return "responder"

    # Supervisor says done → go to responder for streaming output
    if next_agent == "FINISH":
        return "responder"

    return next_agent  # type: ignore[return-value]


# ── Build graph ────────────────────────────────────────────────────


def _build_graph() -> StateGraph:
    builder = StateGraph(SupervisorState)

    builder.add_node("supervisor", supervisor_node)
    builder.add_node("retrieval", retrieval_node)
    builder.add_node("analysis", analysis_node)
    builder.add_node("library", library_node)
    builder.add_node("responder", responder_node)

    builder.set_entry_point("supervisor")

    builder.add_conditional_edges("supervisor", _route)

    # After each sub-agent, return to supervisor for next decision
    builder.add_edge("retrieval", "supervisor")
    builder.add_edge("analysis", "supervisor")
    builder.add_edge("library", "supervisor")

    # Responder is terminal — streams final output then ends
    builder.add_edge("responder", END)

    return builder


supervisor_graph = _build_graph().compile()
