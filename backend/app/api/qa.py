from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.research_qa import research_qa_agent
from app.db.sqlite import db
from app.models.conversation import Conversation, QARequest, QAResponse

router = APIRouter(prefix="/qa", tags=["qa"])


@router.post("/ask", response_model=QAResponse)
def ask(payload: QARequest) -> QAResponse:
    return research_qa_agent.answer(payload.question, payload.top_k)


@router.post("/ask/stream")
async def ask_stream(payload: QARequest):
    async def event_generator():
        try:
            async for ev in research_qa_agent.answer_stream(payload.question, payload.top_k):
                kind = ev.get("event")
                if kind == "token":
                    # token content is plain text
                    data = ev.get("data", "")
                    yield f"event: token\ndata: {data}\n\n"
                elif kind == "node_done":
                    import json
                    yield f"event: node_done\ndata: {json.dumps(ev)}\n\n"
        except Exception as e:
            # send error event then stop
            yield f"event: error\ndata: {str(e)}\n\n"
        finally:
            # Ensure final saved conversation is available: call synchronous answer
            try:
                final = research_qa_agent.answer(payload.question, payload.top_k)
                import json
                node = {"event": "node_done", "node": "format_save", "data": {"final_answer": final.answer, "cited_papers": [p.__dict__ for p in final.cited_papers]}}
                yield f"event: node_done\ndata: {json.dumps(node)}\n\n"
            except Exception:
                # ignore
                pass
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/history", response_model=list[Conversation])
def history() -> list[dict]:
    return db.list_conversations()


@router.get("/history/{conversation_id}", response_model=Conversation)
def history_item(conversation_id: str) -> dict:
    item = db.get_conversation(conversation_id)
    if not item:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return item


@router.delete("/history/{conversation_id}")
def delete_history_item(conversation_id: str) -> dict:
    ok = db.delete_conversation(conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"status": "deleted"}
