from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PaperBasicInfo(BaseModel):
    """LLM structured output for Phase 1 extraction."""
    title: str
    authors: list[str] = []
    year: int | None = None


class PaperDeepInfo(BaseModel):
    """LLM structured output for Phase 2 extraction."""
    abstract: str | None = None
    abstract_zh: str | None = None
    contributions: str | None = None
    methods: str | None = None
    results: str | None = None
    limitations: str | None = None
    conclusion: str | None = None
    keywords: list[str] = []
    domain: str | None = None


class PaperCreate(BaseModel):
    title: str
    authors: list[str] = []
    year: int | None = None
    abstract: str | None = None
    abstract_zh: str | None = None
    contributions: str | None = None
    methods: str | None = None
    results: str | None = None
    limitations: str | None = None
    conclusion: str | None = None
    keywords: list[str] = []
    domain: str | None = None


class Paper(PaperCreate):
    id: str
    file_path: str
    page_count: int = 0
    is_favorite: bool = False
    tags: list[str] = []
    parse_status: str = "queued"
    parse_progress: int = 0
    parse_step: str | None = None
    parse_error: str | None = None
    parsed_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PaperUpdate(BaseModel):
    domain: str | None = None
    is_favorite: bool | None = None
    tags: list[str] | None = None


class PaperBrief(BaseModel):
    id: str
    title: str
    authors: list[str] = []
    year: int | None = None
    snippet: str = ""


class SearchQuery(BaseModel):
    query: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    domain: str | None = None
    tags: list[str] | None = None
    is_favorite: bool | None = None


class ParseStatus(BaseModel):
    paper_id: str
    status: str
    progress: int
    current_step: str | None = None
    error: str | None = None


class Tag(BaseModel):
    id: str
    name: str


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
