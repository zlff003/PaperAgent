from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agents.paper_retrieval import paper_retrieval_agent
from app.models.paper import PaperBrief, SearchQuery

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=list[PaperBrief])
def search(payload: SearchQuery) -> list:
    return paper_retrieval_agent.search(
        query=payload.query,
        top_k=10,
        year_from=payload.year_from,
        year_to=payload.year_to,
        domain=payload.domain,
        tags=payload.tags,
        is_favorite=payload.is_favorite,
    )


@router.get("/semantic", response_model=list[PaperBrief])
def semantic_search(q: str = "") -> list:
    if not q.strip():
        return []
    return paper_retrieval_agent.search(query=q, top_k=10)
