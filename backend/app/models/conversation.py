from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .paper import PaperBrief


class QARequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = Field(default=6, ge=1, le=20)


class QAResponse(BaseModel):
    answer: str
    cited_papers: list[PaperBrief]
    conversation_id: str


class Conversation(BaseModel):
    id: str
    question: str
    answer: str
    cited_papers: list[PaperBrief] = []
    created_at: datetime
