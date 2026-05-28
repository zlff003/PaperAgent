"""
Chat API — SSE streaming endpoint backed by the Supervisor Graph.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.db.sqlite import db
from app.supervisor.supervisor import supervisor_graph

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None


@router.get("/history")
async def list_history(session_id: str | None = None):
    """List conversations. If session_id provided, return all turns for that session.
    Otherwise, return one entry per session (deduplicated)."""
    if session_id:
        return db.list_conversations_by_session(session_id)
    return db.list_conversations()


@router.delete("/history/session/{session_id}")
async def delete_session_history(session_id: str):
    """Delete ALL conversation turns in a session."""
    count = db.delete_conversations_by_session(session_id)
    return {"status": "deleted", "count": count}


@router.delete("/history/{conversation_id}")
async def delete_history(conversation_id: str):
    """Delete a single conversation by ID."""
    ok = db.delete_conversation(conversation_id)
    return {"status": "deleted" if ok else "not_found"}


@router.post("")
async def chat(payload: ChatRequest):
    """SSE streaming chat endpoint using the supervisor graph."""

    def _format_recent_context(recent: list[dict]) -> str:
        """Format recent conversations as a brief context summary for long-term memory."""
        lines = [
            "[System: Below are recent conversations for context. "
            "Use them to understand user references but do not mention them unless relevant.]\n"
        ]
        for conv in recent:
            lines.append(f"Q: {conv['question'][:200]}")
            answer_brief = conv["answer"][:200].replace("\n", " ")
            lines.append(f"A: {answer_brief}...\n")
        return "\n".join(lines)

    async def event_generator():
        # 1. Load session history (short-term memory)
        history_messages: list = []
        papers_ctx: list[dict] = []
        if payload.session_id:
            prior_turns = db.list_conversations_by_session(payload.session_id, limit=20)
            for turn in prior_turns:
                history_messages.append(HumanMessage(content=turn["question"]))
                # Truncate old answers to save context window
                answer = turn["answer"]
                if len(answer) > 600:
                    answer = answer[:600] + "..."
                history_messages.append(AIMessage(content=answer))
            # Restore papers_context from the last turn's cited_papers
            # so follow-up questions like "第一篇讲了什么" can reference them
            if prior_turns:
                last_turn = prior_turns[-1]
                cited = last_turn.get("cited_papers", [])
                if cited:
                    papers_ctx = [
                        {
                            "id": p.get("id", ""),
                            "title": p.get("title", ""),
                            "authors": p.get("authors", []),
                            "year": p.get("year"),
                            "snippet": "",  # will be filled by analysis if needed
                        }
                        for p in cited
                    ]

        # 2. Inject long-term memory for new sessions
        if not payload.session_id:
            recent = db.get_last_conversations(limit=5)
            if recent:
                summary = _format_recent_context(recent)
                history_messages.insert(0, SystemMessage(content=summary))

        # 3. Build initial state with history + current message
        initial_state = {
            "messages": [*history_messages, HumanMessage(content=payload.message)],
            "next": "",
            "papers_context": papers_ctx,
            "session_id": payload.session_id or "",
            "agent_history": [],
        }

        final_answer = ""
        final_papers: list[dict] = []

        try:
            async for event in supervisor_graph.astream_events(
                initial_state,
                version="v2",
                config={"recursion_limit": 50},
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                metadata = event.get("metadata", {})

                if kind == "on_chat_model_stream":
                    # Stream tokens only from the responder node (single source)
                    node = metadata.get("langgraph_node", "")
                    if node == "responder":
                        chunk = event.get("data", {}).get("chunk", None)
                        if chunk and hasattr(chunk, "content") and chunk.content:
                            yield f"event: token\ndata: {chunk.content}\n\n"

                elif kind == "on_chain_end" and name in (
                    "retrieval",
                    "analysis",
                    "library",
                    "responder",
                ):
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        # Capture papers context from retrieval
                        papers_ctx = output.get("papers_context", [])
                        if papers_ctx:
                            final_papers = papers_ctx

                        # Capture final answer from agent messages
                        msgs = output.get("messages", [])
                        for msg in msgs:
                            if hasattr(msg, "content") and msg.content:
                                final_answer = msg.content

                elif kind == "on_chain_end" and name == "supervisor":
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict) and output.get("next") == "FINISH":
                        pass  # supervisor decided to finish

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # Save conversation for ALL agent types (library, retrieval, analysis)
            if final_answer and payload.message:
                try:
                    session_id = payload.session_id or ""
                    cited = [
                        {
                            "id": p.get("id", ""),
                            "title": p.get("title", ""),
                            "authors": p.get("authors", []),
                            "year": p.get("year"),
                        }
                        for p in final_papers
                    ]
                    turn_index = 0
                    if session_id:
                        prior = db.list_conversations_by_session(session_id)
                        turn_index = len(prior)
                    db.insert_conversation({
                        "id": str(uuid.uuid4()),
                        "question": payload.message,
                        "answer": final_answer,
                        "cited_papers": cited,
                        "session_id": session_id or None,
                        "turn_index": turn_index,
                    })
                except Exception:
                    pass  # best-effort save

            # Emit final event with cited papers for the frontend
            final_data = {
                "final_answer": final_answer,
                "cited_papers": final_papers,
            }
            yield f"event: node_done\ndata: {json.dumps(final_data)}\n\n"
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
