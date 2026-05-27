"""
Research QA Agent — thin wrapper around the QA LangGraph graph.

The graph handles question classification, retrieval, answer generation,
self-critique with reflection, and conversation persistence.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from app.graph.qa_state import QAState

from app.models.conversation import QAResponse
from app.models.paper import PaperBrief


class ResearchQAAgent:
    """QA agent backed by a LangGraph StateGraph."""

    def answer(self, question: str, top_k: int = 6) -> QAResponse:
        """Synchronous: invoke the graph and return a complete QAResponse."""
        from app.graph.qa_graph import qa_graph

        initial: dict = {
            "question": question,
            "top_k": top_k,
            "question_type": "simple",
            "sub_questions": [],
            "retrieved_papers": [],
            "draft_answer": "",
            "critique": "",
            "critique_passed": False,
            "iteration": 0,
            "max_iterations": 2,
            "final_answer": "",
            "conversation_id": "",
            "error": "",
        }

        result = asyncio.run(qa_graph.ainvoke(initial))

        papers = [
            PaperBrief(**p)
            for p in result.get("retrieved_papers", [])
        ]

        return QAResponse(
            answer=result.get("final_answer", ""),
            cited_papers=papers,
            conversation_id=result.get("conversation_id", ""),
        )

    async def answer_stream(
        self, question: str, top_k: int = 6
    ) -> AsyncIterator[dict]:
        """Async generator that yields state deltas as the graph progresses."""
        from app.graph.qa_graph import qa_graph

        initial: dict = {
            "question": question,
            "top_k": top_k,
            "question_type": "simple",
            "sub_questions": [],
            "retrieved_papers": [],
            "draft_answer": "",
            "critique": "",
            "critique_passed": False,
            "iteration": 0,
            "max_iterations": 2,
            "final_answer": "",
            "conversation_id": "",
            "error": "",
        }

        async for event in qa_graph.astream_events(initial, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")
            metadata = event.get("metadata", {})

            if kind == "on_chat_model_stream":
                # Only stream tokens from the generate_answer node
                node = metadata.get("langgraph_node", "")
                if node == "generate_answer":
                    chunk = event.get("data", {}).get("chunk", None)
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        yield {"event": "token", "data": chunk.content}

            elif kind == "on_chain_end" and name in (
                "classify_question", "simple_retrieve", "decompose",
                "generate_answer", "critique", "reformulate", "format_save",
            ):
                output = event.get("data", {}).get("output", {})
                yield {"event": "node_done", "node": name, "data": output}


research_qa_agent = ResearchQAAgent()
