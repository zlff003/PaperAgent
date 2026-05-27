"""
QA Graph — the only LangGraph StateGraph in PaperAgent.

Handles question classification, decomposition for complex questions,
parallel retrieval, self-critique reflection loop, and answer formatting.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.agents.paper_retrieval import paper_retrieval_agent
from app.core.langchain_factory import get_chat_model
from app.db.sqlite import db
from app.graph.qa_state import QAState

chat = get_chat_model()

# ── Node functions ────────────────────────────────────────────────


async def _classify_question(state: QAState) -> dict[str, Any]:
    """Determine question type: simple, comparison, review, or complex."""
    question = state["question"]
    prompt = (
        "Classify this research question into one of the following types:\n"
        '- "simple": a straightforward question about specific papers or facts\n'
        '- "comparison": asking to compare two or more papers or approaches\n'
        '- "review": asking for a literature review or survey on a topic\n'
        '- "complex": a multi-part question that needs breaking down\n\n'
        f"Question: {question}\n\n"
        "Reply with exactly one word: simple, comparison, review, or complex."
    )
    try:
        response = await chat.ainvoke(prompt)
        raw = response.content.strip().lower().replace('"', "")
        for t in ("comparison", "review", "complex", "simple"):
            if t in raw:
                return {"question_type": t}
    except Exception:
        pass
    return {"question_type": "simple"}


async def _simple_retrieve(state: QAState) -> dict[str, Any]:
    """Single-pass retrieval for simple/comparison/review questions."""
    question = state["question"]
    top_k = state.get("top_k", 6)
    papers = await paper_retrieval_agent.retrieve_for_qa_async(question, top_k=top_k)
    return {"retrieved_papers": [p.model_dump() for p in papers]}


async def _decompose(state: QAState) -> dict[str, Any]:
    """Break complex question into sub-questions and search each in parallel."""
    question = state["question"]
    top_k = state.get("top_k", 6)

    # Step 1: LLM decomposes
    prompt = (
        "Break the following research question into 2-4 simpler sub-questions "
        "that can be answered independently from a paper library. "
        "Return one sub-question per line, no numbering or bullets.\n\n"
        f"Question: {question}\n\nSub-questions:"
    )
    try:
        response = await chat.ainvoke(prompt)
        lines = [
            line.strip(" -•*0123456789.)\t")
            for line in response.content.strip().split("\n")
        ]
        sub_questions = [line for line in lines if len(line) > 10]
    except Exception:
        sub_questions = [question]

    if not sub_questions:
        sub_questions = [question]

    # Step 2: parallel search per sub-question
    async def _search_one(sub_q: str) -> list[dict[str, Any]]:
        papers = await paper_retrieval_agent.retrieve_for_qa_async(sub_q, top_k=max(3, top_k // len(sub_questions)))
        return [p.model_dump() for p in papers]

    all_results = await asyncio.gather(*[_search_one(q) for q in sub_questions])

    # Step 3: deduplicate and merge
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for batch in all_results:
        for paper in batch:
            pid = paper.get("id", "")
            if pid not in seen:
                seen.add(pid)
                merged.append(paper)

    return {
        "sub_questions": sub_questions,
        "retrieved_papers": merged[:top_k],
    }


async def _generate_answer(state: QAState) -> dict[str, Any]:
    """Generate answer from retrieved paper context."""
    question = state["question"]
    question_type = state.get("question_type", "simple")
    papers = state.get("retrieved_papers", [])

    if not papers:
        return {"draft_answer": "", "error": "No papers retrieved."}

    # Build context
    context_parts = []
    for idx, p in enumerate(papers, start=1):
        authors = ", ".join(p.get("authors", [])[:5]) or "Unknown"
        year = f" ({p.get('year')})" if p.get("year") else ""
        snippet = p.get("snippet", "")[:400]
        context_parts.append(f"[{idx}] {p.get('title', 'Unknown')} — {authors}{year}\n{snippet}")

    context = "\n\n".join(context_parts)

    # System prompt varies by question type
    type_guidance = {
        "comparison": "Provide a structured comparison across the papers. Use a table or clear comparison points.",
        "review": "Provide a mini literature review, organizing findings by theme or approach.",
        "complex": "Answer each part of the question clearly. Use the papers to support each point.",
        "simple": "Answer directly based on the paper evidence. Cite specific papers.",
    }
    guidance = type_guidance.get(question_type, type_guidance["simple"])

    prompt = (
        f"You are a research assistant. {guidance}\n"
        "Cite paper sources inline as [n]. "
        "If comparing, structure with clear comparison points. "
        "If info is insufficient, say so clearly.\n\n"
        f"Question: {question}\n\n"
        f"Paper library context:\n{context}"
    )

    try:
        full_response = ""
        async for chunk in chat.astream(prompt):
            if chunk.content:
                full_response += chunk.content
        return {"draft_answer": full_response, "error": ""}
    except Exception as exc:
        return {"draft_answer": "", "error": str(exc)}


async def _critique(state: QAState) -> dict[str, Any]:
    """Self-critique: LLM evaluates whether the answer is complete and accurate."""
    question = state["question"]
    answer = state.get("draft_answer", "")
    papers = state.get("retrieved_papers", [])

    if not answer:
        return {"critique": "No answer generated.", "critique_passed": False}

    context = "\n".join(
        f"[{i+1}] {p.get('title', 'Unknown')}" for i, p in enumerate(papers)
    ) or "No papers."

    prompt = (
        "Evaluate the following answer to a research question. Check for:\n"
        "1. Does it answer all parts of the question?\n"
        "2. Is every claim supported by the provided paper context?\n"
        "3. Are there any gaps or missing information?\n\n"
        f"Question: {question}\n\n"
        f"Answer: {answer}\n\n"
        f"Available papers:\n{context}\n\n"
        "Reply with YES if the answer is complete and well-supported, "
        "or NO with a brief explanation of what is missing."
    )

    try:
        response = await chat.ainvoke(prompt)
        text = response.content.strip()
        passed = text.upper().startswith("YES")
        return {"critique": text, "critique_passed": passed}
    except Exception:
        return {"critique": "Critique failed.", "critique_passed": True}


async def _reformulate_query(state: QAState) -> dict[str, Any]:
    """Generate a refined search query based on critique feedback."""
    question = state["question"]
    critique = state.get("critique", "")
    iteration = state.get("iteration", 0)

    prompt = (
        f"The original question was: {question}\n\n"
        f"The answer was evaluated as incomplete. Critique: {critique}\n\n"
        "Generate a refined search query that targets the missing information. "
        "Return only the query text, no commentary."
    )

    try:
        response = await chat.ainvoke(prompt)
        refined_query = response.content.strip()
    except Exception:
        refined_query = question

    # Retrieve with refined query
    top_k = state.get("top_k", 6)
    papers = await paper_retrieval_agent.retrieve_for_qa_async(refined_query, top_k=top_k)
    new_papers = [p.model_dump() for p in papers]

    # Merge with existing, keep unique
    existing = state.get("retrieved_papers", [])
    seen = {p.get("id") for p in existing}
    merged = list(existing)
    for p in new_papers:
        if p.get("id") not in seen:
            seen.add(p.get("id"))
            merged.append(p)

    return {
        "retrieved_papers": merged,
        "iteration": iteration + 1,
    }


async def _format_save(state: QAState) -> dict[str, Any]:
    """Format final answer with citations and save conversation."""
    answer = state.get("draft_answer", "")
    papers = state.get("retrieved_papers", [])

    # Build citation list
    if papers:
        citation_lines = ["", "---", "**参考文献:**"]
        for idx, p in enumerate(papers, start=1):
            authors = ", ".join(p.get("authors", [])[:3]) or "Unknown"
            year = f" ({p.get('year')})" if p.get("year") else ""
            citation_lines.append(f"[{idx}] *{p.get('title', 'Unknown')}* — {authors}{year}")
        final = answer + "\n" + "\n".join(citation_lines)
    else:
        final = answer or "无法生成回答，论文库中没有找到相关信息。"

    # Save to DB
    conversation_id = str(uuid.uuid4())
    try:
        db.insert_conversation(
            {
                "id": conversation_id,
                "question": state["question"],
                "answer": final,
                "cited_papers": papers,
            }
        )
    except Exception:
        pass

    return {
        "final_answer": final,
        "conversation_id": conversation_id,
        "retrieved_papers": papers,
    }

# ── Routing functions ──────────────────────────────────────────────


def _route_after_classify(state: QAState) -> Literal["simple_retrieve", "decompose"]:
    qtype = state.get("question_type", "simple")
    if qtype == "complex":
        return "decompose"
    return "simple_retrieve"


def _route_after_critique(state: QAState) -> Literal["format_save", "reformulate"]:
    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", 2)
    # Skip reflection for simple questions — one-shot generate is sufficient
    if state.get("question_type", "simple") == "simple":
        return "format_save"
    if state.get("critique_passed", True) or iteration >= max_iter:
        return "format_save"
    return "reformulate"

# ── Build graph ────────────────────────────────────────────────────


def _build_graph() -> StateGraph:
    builder = StateGraph(QAState)

    # Add nodes
    builder.add_node("classify_question", _classify_question)
    builder.add_node("simple_retrieve", _simple_retrieve)
    builder.add_node("decompose", _decompose)
    builder.add_node("generate_answer", _generate_answer)
    builder.add_node("critique", _critique)
    builder.add_node("reformulate", _reformulate_query)
    builder.add_node("format_save", _format_save)

    # Edges
    builder.set_entry_point("classify_question")
    builder.add_conditional_edges("classify_question", _route_after_classify)
    builder.add_edge("simple_retrieve", "generate_answer")
    builder.add_edge("decompose", "generate_answer")
    builder.add_edge("generate_answer", "critique")
    builder.add_conditional_edges("critique", _route_after_critique)
    builder.add_edge("reformulate", "generate_answer")  # loop: reformulate → generate
    builder.add_edge("format_save", END)

    return builder


qa_graph = _build_graph().compile()
