from __future__ import annotations

import json
from typing import Any

from app.agents.paper_retrieval import paper_retrieval_agent
from app.core.langchain_factory import get_chat_model
from app.db.sqlite import db
from app.mcp.server.server import mcp

_chat = get_chat_model()


def _format_paper_brief(p: dict[str, Any]) -> str:
    authors = ", ".join(p.get("authors", [])[:5]) or "Unknown"
    year = f" ({p.get('year')})" if p.get("year") else ""
    domain = f" [{p.get('domain')}]" if p.get("domain") else ""
    tags = f" tags: {', '.join(p.get('tags', []))}" if p.get("tags") else ""
    fav = " [FAV]" if p.get("is_favorite") else ""
    return f"- **{p['title']}** — {authors}{year}{domain}{fav}{tags}"


@mcp.tool()
def search_papers(
    query: str,
    top_k: int = 6,
    year_from: int | None = None,
    year_to: int | None = None,
    domain: str | None = None,
    tags: list[str] | None = None,
    is_favorite: bool | None = None,
) -> str:
    """Semantic search across the paper library. Finds papers by meaning, not just keywords.
    Returns matching papers with relevance snippets and scores.
    Use this when you need to find papers about a topic, concept, or research question."""
    results = paper_retrieval_agent.search(
        query=query,
        top_k=top_k,
        year_from=year_from,
        year_to=year_to,
        domain=domain,
        tags=tags,
        is_favorite=is_favorite,
    )
    if not results:
        return "No papers matched the query."

    lines = [f"Found {len(results)} papers matching \"{query}\":\n"]
    for i, r in enumerate(results, 1):
        authors = ", ".join(r.authors[:3]) if r.authors else "Unknown"
        year = f" ({r.year})" if r.year else ""
        lines.append(f"## [{i}] {r.title} — {authors}{year}")
        lines.append(f"  ID: `{r.id}`")
        if r.snippet:
            lines.append(f"  {r.snippet[:300]}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def get_paper(paper_id: str) -> str:
    """Get the full structured details of a specific paper by its ID.
    Returns title, authors, year, abstract (CN/EN), contributions, methods,
    results, limitations, conclusion, keywords, domain, tags, and favorite status."""
    paper = db.get_paper(paper_id)
    if not paper:
        return f"Paper not found: {paper_id}"

    sections = []
    sections.append(f"# {paper['title']}")
    authors = ", ".join(paper.get("authors", [])) if paper.get("authors") else "Unknown"
    year = str(paper.get("year")) if paper.get("year") else "Unknown year"
    sections.append(f"**Authors**: {authors} | **Year**: {year}")

    if paper.get("domain"):
        sections.append(f"**Domain**: {paper['domain']}")
    if paper.get("keywords"):
        sections.append(f"**Keywords**: {', '.join(paper['keywords'])}")
    if paper.get("tags"):
        sections.append(f"**Tags**: {', '.join(paper['tags'])}")
    if paper.get("is_favorite"):
        sections.append("**Favorite**: Yes")

    sections.append(f"\n**PDF**: `{paper['file_path']}`")

    if paper.get("abstract"):
        sections.append(f"\n## Abstract\n{paper['abstract']}")
    if paper.get("abstract_zh"):
        sections.append(f"\n## 中文摘要\n{paper['abstract_zh']}")
    if paper.get("contributions"):
        sections.append(f"\n## Contributions\n{paper['contributions']}")
    if paper.get("methods"):
        sections.append(f"\n## Methods\n{paper['methods']}")
    if paper.get("results"):
        sections.append(f"\n## Results\n{paper['results']}")
    if paper.get("limitations"):
        sections.append(f"\n## Limitations\n{paper['limitations']}")
    if paper.get("conclusion"):
        sections.append(f"\n## Conclusion\n{paper['conclusion']}")

    return "\n".join(sections)


@mcp.tool()
def list_papers(
    query: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    domain: str | None = None,
    tags: list[str] | None = None,
    is_favorite: bool | None = None,
) -> str:
    """List/filter papers in the library by metadata fields.
    Use this to browse papers by year, domain, tags, or favorites.
    Does NOT do semantic search — use search_papers for that."""
    papers = db.list_papers(
        query=query,
        year_from=year_from,
        year_to=year_to,
        domain=domain,
        tags=tags,
        is_favorite=is_favorite,
    )
    if not papers:
        return "No papers found matching the filters."

    lines = [f"Total papers: {len(papers)}\n"]
    for p in papers:
        lines.append(_format_paper_brief(p))
        lines.append(f"  ID: `{p['id']}`")
    return "\n".join(lines)


@mcp.tool()
def ask_paper_qa(question: str, top_k: int = 6) -> str:
    """Ask a research question and get an AI-powered answer based on the paper library.
    The system retrieves the most relevant papers, feeds their structured summaries
    to an LLM, and returns an answer with paper citations.
    Supports cross-paper comparison and literature review questions."""
    import asyncio

    async def _run():
        papers = await paper_retrieval_agent.search_async(query=question, top_k=top_k)
        if not papers:
            return "No relevant papers found to answer this question."

        context_parts = []
        cited = []
        for i, p in enumerate(papers, 1):
            authors = ", ".join(p.authors[:3]) if p.authors else "Unknown"
            year = f" ({p.year})" if p.year else ""
            snippet = (p.snippet or "")[:400]
            context_parts.append(f"[{i}] {p.title} — {authors}{year}\n{snippet}")
            cited.append({"title": p.title, "authors": p.authors[:3], "year": p.year, "id": p.id})

        context = "\n\n".join(context_parts)
        prompt = (
            "You are a research assistant. Answer the question based on the provided paper context.\n"
            "Cite paper sources inline as [n]. If info is insufficient, say so clearly.\n\n"
            f"Question: {question}\n\nPaper library context:\n{context}"
        )

        response = await _chat.ainvoke(prompt)
        answer = response.content if hasattr(response, "content") else str(response)

        lines = [answer, ""]
        if cited:
            lines.append("---")
            lines.append("**Cited papers:**")
            for i, p in enumerate(cited, 1):
                authors = ", ".join(p["authors"]) if p["authors"] else "Unknown"
                year = f" ({p['year']})" if p["year"] else ""
                lines.append(f"[{i}] {p['title']} — {authors}{year}")
        return "\n".join(lines)

    return asyncio.run(_run())


@mcp.tool()
def list_tags() -> str:
    """List all user-defined tags and how many papers are associated with each tag."""
    tags = db.list_tags()
    if not tags:
        return "No tags defined yet."

    lines = ["Tags:"]
    for t in tags:
        lines.append(f"- {t['name']} ({t.get('paper_count', 0)} papers)")
    return "\n".join(lines)


@mcp.tool()
def export_papers() -> str:
    """Export all papers in the library as structured Markdown.
    Each paper includes its complete extracted information."""
    papers = db.list_papers()
    if not papers:
        return "No papers in the library."

    parts = [f"# PaperAgent Export — {len(papers)} papers\n"]
    for p in papers:
        parts.append(f"## {p['title']}")
        authors = ", ".join(p.get("authors", [])) if p.get("authors") else "Unknown"
        parts.append(f"**Authors**: {authors}")
        parts.append(f"**Year**: {p.get('year', 'Unknown')}")
        if p.get("domain"):
            parts.append(f"**Domain**: {p['domain']}")
        if p.get("keywords"):
            parts.append(f"**Keywords**: {', '.join(p['keywords'])}")
        if p.get("abstract"):
            parts.append(f"\n### Abstract\n{p['abstract']}")
        if p.get("contributions"):
            parts.append(f"\n### Contributions\n{p['contributions']}")
        if p.get("methods"):
            parts.append(f"\n### Methods\n{p['methods']}")
        if p.get("results"):
            parts.append(f"\n### Results\n{p['results']}")
        if p.get("conclusion"):
            parts.append(f"\n### Conclusion\n{p['conclusion']}")
        parts.append("\n---\n")
    return "\n".join(parts)
