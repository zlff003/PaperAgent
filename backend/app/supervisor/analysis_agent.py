"""
Analysis Agent — generates research answers based on retrieved paper context.
No streaming — the Responder node handles final streaming output.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from app.core.langchain_factory import get_chat_model
from app.db.sqlite import db
from app.supervisor.state import SupervisorState

chat = get_chat_model()


async def analysis_node(state: SupervisorState) -> dict[str, Any]:
    """Generate an answer based on papers_context and the user's question."""
    messages = state.get("messages", [])
    papers = state.get("papers_context", [])

    # Extract the user's question (the last human message)
    question = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            question = msg.content
            break

    if not question:
        return {
            "messages": [AIMessage(content="No question found to analyze.")],
            "agent_history": _append_history(state, "analysis"),
        }

    if not papers:
        return {
            "messages": [
                AIMessage(content="[analysis] No papers available. Retrieval needed first.")
            ],
            "agent_history": _append_history(state, "analysis"),
        }

    # Enrich paper context: fetch full details from DB for papers lacking snippets
    enriched_papers: list[dict[str, Any]] = []
    for p in papers:
        pid = p.get("id", "")
        snippet = p.get("snippet", "")
        if pid and not snippet:
            try:
                full = db.get_paper(pid)
                if full:
                    enriched_papers.append({
                        "id": full.get("id", pid),
                        "title": full.get("title", p.get("title", "")),
                        "authors": full.get("authors", p.get("authors", [])),
                        "year": full.get("year", p.get("year")),
                        "snippet": (full.get("abstract") or "")[:400],
                    })
                    continue
            except Exception:
                pass
        enriched_papers.append(p)

    # Build paper context for the LLM
    context_parts = []
    for idx, p in enumerate(enriched_papers, start=1):
        authors = ", ".join(p.get("authors", [])[:5]) or "Unknown"
        year = f" ({p.get('year')})" if p.get("year") else ""
        snippet = p.get("snippet", "")[:400]
        context_parts.append(
            f"[{idx}] {p.get('title', 'Unknown')} — {authors}{year}\n{snippet}"
        )

    context = "\n\n".join(context_parts)

    prompt = (
        "You are a research assistant. Answer the question based on the provided paper context.\n"
        "Cite paper sources inline as [n]. "
        "If comparing, structure with clear comparison points. "
        "If info is insufficient, say so clearly.\n\n"
        f"Question: {question}\n\n"
        f"Paper library context:\n{context}"
    )

    try:
        response = await chat.ainvoke(prompt)
        answer = response.content if hasattr(response, "content") else str(response)

        return {
            "messages": [AIMessage(content=answer)],
            "papers_context": enriched_papers,
            "agent_history": _append_history(state, "analysis"),
        }
    except Exception as exc:
        return {
            "messages": [AIMessage(content=f"Error generating answer: {exc}")],
            "agent_history": _append_history(state, "analysis"),
        }


def _append_history(state: SupervisorState, agent: str) -> list[str]:
    return state.get("agent_history", []) + [agent]
