"""
Responder Node — the single streaming output node.

Runs after the Supervisor decides FINISH. Takes the full conversation
context and papers_context, then generates the final user-facing response
via chat.astream() for SSE token-by-token streaming.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.core.langchain_factory import get_chat_model
from app.supervisor.state import SupervisorState

chat = get_chat_model()


def _format_references(papers: list[dict[str, Any]]) -> str:
    """Build a References section from papers_context."""
    lines = ["", "---", "**References:**"]
    for idx, p in enumerate(papers, start=1):
        title = p.get("title", "Unknown")
        authors = ", ".join(p.get("authors", [])[:3]) or "Unknown"
        year = f" ({p.get('year')})" if p.get("year") else ""
        lines.append(f"[{idx}] *{title}* — {authors}{year}")
    return "\n".join(lines)


def _build_prompt(
    messages: list,
    papers: list[dict[str, Any]],
    agent_history: list[str],
) -> str:
    """Build the responder prompt based on what agents ran and what context is available."""
    # Extract user question
    question = ""
    for msg in messages:
        if isinstance(msg, HumanMessage):
            question = msg.content  # last human message wins

    # Build paper context block for LLM reference
    paper_context_block = ""
    if papers:
        entries = []
        for idx, p in enumerate(papers, start=1):
            title = p.get("title", "Unknown")
            authors = ", ".join(p.get("authors", [])[:5]) or "Unknown"
            year = f" ({p.get('year')})" if p.get("year") else ""
            snippet = (p.get("snippet") or "")[:400]
            entries.append(f"[{idx}] {title} — {authors}{year}\n{snippet}")
        paper_context_block = "\n\n".join(entries)

    # Determine the response mode from agent_history
    has_analysis = "analysis" in agent_history
    has_retrieval = "retrieval" in agent_history
    has_library = "library" in agent_history

    if has_analysis or (has_retrieval and papers):
        # Research QA mode: the user asked a question about papers
        base = (
            "You are a research assistant. Answer the user's question based on "
            "the provided paper context. Cite paper sources inline as [n]. "
            "If comparing, structure with clear comparison points. "
            "If info is insufficient, say so clearly.\n\n"
        )
    elif has_library or not papers:
        # Library management or general response: present data clearly
        base = (
            "You are PaperAgent, a research paper assistant. Present the following "
            "information to the user in a clear, helpful way. "
            "Keep the original data and structure intact. Be concise.\n\n"
        )
    else:
        base = (
            "You are PaperAgent, a research paper assistant. Respond to the user "
            "in a helpful, concise manner.\n\n"
        )

    parts = [base]
    if question:
        parts.append(f"User question: {question}\n")
    if paper_context_block:
        parts.append(f"Paper context:\n{paper_context_block}\n")

    return "\n".join(parts)


async def responder_node(state: SupervisorState) -> dict[str, Any]:
    """Generate the final streaming response based on the full conversation."""
    messages = state.get("messages", [])
    papers = state.get("papers_context", [])
    agent_history = state.get("agent_history", [])

    prompt = _build_prompt(messages, papers, agent_history)

    try:
        full_response = ""
        async for chunk in chat.astream(prompt):
            if chunk.content:
                full_response += chunk.content

        # Append references section if papers were cited
        if papers:
            full_response += _format_references(papers)

        return {"messages": [AIMessage(content=full_response)]}
    except Exception as exc:
        return {"messages": [AIMessage(content=f"Error generating response: {exc}")]}
