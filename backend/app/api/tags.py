from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.db.sqlite import db
from app.models.paper import Tag, TagCreate

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[Tag])
def list_tags() -> list[dict]:
    return db.list_tags()


@router.post("", response_model=Tag)
def create_tag(payload: TagCreate) -> dict:
    return db.create_tag(payload.name)


@router.delete("/{tag_id}")
def delete_tag(tag_id: str) -> dict[str, str]:
    ok = db.delete_tag(tag_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Tag not found.")
    return {"status": "deleted", "tag_id": tag_id}
