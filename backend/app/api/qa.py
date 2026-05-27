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
        final_answer = ""
        cited_papers = []
        try:
            async for ev in research_qa_agent.answer_stream(payload.question, payload.top_k):
                kind = ev.get("event")
                if kind == "token":
                    data = ev.get("data", "")
                    yield f"event: token\ndata: {data}\n\n"
                elif kind == "node_done":
                    import json
                    node_data = ev.get("data", {})
                    # Capture final answer info as it passes through
                    if node_data.get("final_answer"):
                        final_answer = node_data["final_answer"]
                    if node_data.get("retrieved_papers"):
                        cited_papers = node_data["retrieved_papers"]
                    # Don't send intermediate node completions to frontend;
                    # only token events and the final answer matter for UX
        except Exception as e:
            yield f"event: error\ndata: {str(e)}\n\n"
        finally:
            import json
            # Emit a final summary event so the frontend always gets answer + citations
            final = {
                "event": "node_done",
                "node": "format_save",
                "data": {
                    "final_answer": final_answer,
                    "cited_papers": [p.model_dump() if hasattr(p, 'model_dump') else p for p in cited_papers],
                },
            }
            yield f"event: node_done\ndata: {json.dumps(final)}\n\n"
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
