from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.agents.paper_ingestion import paper_ingestion_agent
from app.db.chroma import vector_store
from app.db.sqlite import db
from app.core.task_queue import parse_queue
from app.models.paper import Paper, PaperUpdate, ParseStatus

router = APIRouter(prefix="/papers", tags=["papers"])


@router.post("/upload", response_model=Paper)
def upload_paper(file: UploadFile = File(...)) -> dict:
    try:
        return paper_ingestion_agent.ingest_upload(file)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[Paper])
def list_papers(
    q: str | None = Query(default=None),
    year_from: int | None = Query(default=None),
    year_to: int | None = Query(default=None),
    domain: str | None = Query(default=None),
    tags: str | None = Query(default=None),
    is_favorite: bool | None = Query(default=None),
) -> list[dict]:
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    return db.list_papers(
        query=q,
        year_from=year_from,
        year_to=year_to,
        domain=domain,
        tags=tag_list,
        is_favorite=is_favorite,
    )


@router.get("/{paper_id}", response_model=Paper)
def get_paper(paper_id: str) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found.")
    return paper


@router.get("/{paper_id}/parse-status", response_model=ParseStatus)
def get_parse_status(paper_id: str) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found.")
    return {
        "paper_id": paper_id,
        "status": paper.get("parse_status") or "unknown",
        "progress": paper.get("parse_progress") or 0,
        "current_step": paper.get("parse_step"),
        "error": paper.get("parse_error"),
    }


@router.post("/{paper_id}/re-extract", response_model=ParseStatus)
def re_extract_paper(paper_id: str) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found.")
    parse_queue.enqueue_parse(paper_id)
    return get_parse_status(paper_id)


@router.get("/{paper_id}/download")
def download_paper(paper_id: str) -> FileResponse:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found.")
    path = Path(paper["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{paper['title'][:60]}.pdf",
        content_disposition_type="attachment",
    )


@router.put("/{paper_id}", response_model=Paper)
def update_paper(paper_id: str, payload: PaperUpdate) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found.")
    update_data = payload.model_dump(exclude_none=True)
    updated = db.update_paper_fields(paper_id, update_data)
    return updated


@router.delete("/{paper_id}")
def delete_paper(paper_id: str) -> dict[str, str]:
    paper = db.delete_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found.")
    Path(paper["file_path"]).unlink(missing_ok=True)
    vector_store.delete(where={"paper_id": paper_id})
    return {"status": "deleted", "paper_id": paper_id}
