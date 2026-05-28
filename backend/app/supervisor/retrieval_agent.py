"""
Retrieval Agent — searches the paper library and returns structured results.
No LLM streaming — the Responder node handles final output.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from app.agents.paper_retrieval import paper_retrieval_agent
from app.supervisor.state import SupervisorState


async def retrieval_node(state: SupervisorState) -> dict[str, Any]:
    """Search papers based on the user's latest query and return formatted results."""
    messages = state.get("messages", [])
    if not messages:
        return _no_query(state)

    # Extract the latest user message as the search query
    query = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            query = msg.content
            break

    if not query:
        return _no_query(state)

    papers = await paper_retrieval_agent.search_async(query=query, top_k=6)

    if not papers:
        return {
            "messages": [AIMessage(content=f'No papers found for: "{query}"')],
            "papers_context": [],
            "agent_history": _append_history(state, "retrieval"),
        }

    papers_context = [
        {
            "id": p.id,
            "title": p.title,
            "authors": p.authors,
            "year": p.year,
            "snippet": p.snippet,
        }
        for p in papers
    ]

    # Concise pre-formatted output for Supervisor context (no LLM)
    lines = [f"[retrieval] Found {len(papers)} papers for: {query}"]
    for i, p in enumerate(papers, 1):
        authors = ", ".join(p.authors[:3]) if p.authors else "Unknown"
        year = f" ({p.year})" if p.year else ""
        lines.append(f"[{i}] {p.title} — {authors}{year}")

    return {
        "messages": [AIMessage(content="\n".join(lines))],
        "papers_context": papers_context,
        "agent_history": _append_history(state, "retrieval"),
    }


def _no_query(state: SupervisorState) -> dict[str, Any]:
    return {
        "messages": [AIMessage(content="No query found.")],
        "agent_history": _append_history(state, "retrieval"),
    }


def _append_history(state: SupervisorState, agent: str) -> list[str]:
    return state.get("agent_history", []) + [agent]
