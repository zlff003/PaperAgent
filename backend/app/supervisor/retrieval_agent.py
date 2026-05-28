"""
Retrieval Agent — searches the paper library and returns structured results.
No LLM streaming — the Responder node handles final output.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from app.agents.paper_retrieval import paper_retrieval_agent
from app.core.langchain_factory import get_chat_model
from app.supervisor.state import SupervisorState


QUERY_REWRITE_PROMPT = """Given the conversation history, rewrite the user's latest question
into a standalone, keyword-rich search query for a research paper database.

Rules:
- Resolve pronouns and references (e.g., "the first one", "that paper", "his method")
  into specific terms from the conversation
- Preserve the original search intent and technical terms
- Output ONLY the rewritten query on a single line, no explanation

Conversation:
{context}

Original: {question}
Rewritten:"""


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

    # Rewrite query with conversation context for better retrieval
    search_query = await _rewrite_query(messages, query)

    papers = await paper_retrieval_agent.search_async(query=search_query, top_k=6)

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


async def _rewrite_query(messages: list, question: str) -> str:
    """Rewrite a user query using conversation context for better retrieval."""
    # Build a brief context summary from recent non-system messages
    context_lines = []
    for msg in messages[-10:]:  # last 10 messages max
        if hasattr(msg, "content") and msg.content:
            role = "User" if hasattr(msg, "type") and msg.type == "human" else "Assistant"
            content = msg.content[:300]
            context_lines.append(f"{role}: {content}")
    context = "\n".join(context_lines) if context_lines else "(no prior context)"

    prompt = QUERY_REWRITE_PROMPT.format(context=context, question=question)

    try:
        chat = get_chat_model()
        response = await chat.ainvoke(prompt)
        rewritten = response.content.strip() if hasattr(response, "content") else question
        # Safety: if the LLM returned garbage, fall back to original
        return rewritten if rewritten and len(rewritten) >= 3 else question
    except Exception:
        return question  # fallback: keep original query


def _append_history(state: SupervisorState, agent: str) -> list[str]:
    return state.get("agent_history", []) + [agent]
