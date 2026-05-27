from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.db.chroma import vector_store
from app.db.sqlite import db

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/export", response_class=PlainTextResponse)
def export_papers() -> str:
    papers = db.list_papers()
    lines = ["# PaperAgent 论文导出", "", f"共 {len(papers)} 篇论文", ""]
    for paper in papers:
        lines.append(f"## {paper['title']}")
        if paper.get("authors"):
            lines.append(f"- 作者: {', '.join(paper['authors'])}")
        if paper.get("year"):
            lines.append(f"- 年份: {paper['year']}")
        if paper.get("domain"):
            lines.append(f"- 领域: {paper['domain']}")
        if paper.get("keywords"):
            lines.append(f"- 关键词: {', '.join(paper['keywords'])}")
        lines.append("")
        if paper.get("abstract"):
            lines.append(f"### 摘要\n{paper['abstract']}\n")
        if paper.get("abstract_zh"):
            lines.append(f"### 中文摘要\n{paper['abstract_zh']}\n")
        if paper.get("contributions"):
            lines.append(f"### 主要贡献\n{paper['contributions']}\n")
        if paper.get("methods"):
            lines.append(f"### 方法/模型\n{paper['methods']}\n")
        if paper.get("results"):
            lines.append(f"### 实验与结果\n{paper['results']}\n")
        if paper.get("limitations"):
            lines.append(f"### 局限性\n{paper['limitations']}\n")
        if paper.get("conclusion"):
            lines.append(f"### 结论\n{paper['conclusion']}\n")
        lines.append("---\n")
    return "\n".join(lines)


@router.post("/reset")
def reset_data() -> dict[str, str]:
    db.reset()
    vector_store.reset()
    return {"status": "reset"}
