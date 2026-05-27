from __future__ import annotations

from app.db.sqlite import db
from app.mcp.server.server import mcp


@mcp.resource("paper://{paper_id}")
def paper_resource(paper_id: str) -> str:
    """Full structured details of a paper (Markdown format)."""
    paper = db.get_paper(paper_id)
    if not paper:
        return f"Paper not found: {paper_id}"

    sections = [f"# {paper['title']}"]
    authors = ", ".join(paper.get("authors", [])) if paper.get("authors") else "Unknown"
    year = str(paper.get("year")) if paper.get("year") else "Unknown"
    sections.append(f"Authors: {authors} | Year: {year}")
    if paper.get("domain"):
        sections.append(f"Domain: {paper['domain']}")
    if paper.get("keywords"):
        sections.append(f"Keywords: {', '.join(paper['keywords'])}")

    for label, field in [
        ("Abstract", "abstract"),
        ("中文摘要", "abstract_zh"),
        ("Contributions", "contributions"),
        ("Methods", "methods"),
        ("Results", "results"),
        ("Limitations", "limitations"),
        ("Conclusion", "conclusion"),
    ]:
        if paper.get(field):
            sections.append(f"\n## {label}\n{paper[field]}")

    return "\n".join(sections)


@mcp.resource("paper://{paper_id}/summary")
def paper_summary_resource(paper_id: str) -> str:
    """Compact summary of a paper: abstract + methods + contributions.
    Optimized for LLM context windows."""
    paper = db.get_paper(paper_id)
    if not paper:
        return f"Paper not found: {paper_id}"

    parts = [f"Title: {paper['title']}"]
    authors = ", ".join(paper.get("authors", [])) if paper.get("authors") else "Unknown"
    parts.append(f"Authors: {authors}")
    if paper.get("year"):
        parts.append(f"Year: {paper['year']}")
    if paper.get("domain"):
        parts.append(f"Domain: {paper['domain']}")

    for label, field in [
        ("Abstract", "abstract"),
        ("Contributions", "contributions"),
        ("Methods", "methods"),
        ("Results", "results"),
        ("Conclusion", "conclusion"),
    ]:
        if paper.get(field):
            parts.append(f"\n{label}: {paper[field][:500]}")

    return "\n".join(parts)


@mcp.resource("papers://list")
def papers_list_resource() -> str:
    """List of all papers in the library (ID, title, year, domain)."""
    papers = db.list_papers()
    if not papers:
        return "No papers in the library."

    lines = [f"Total papers: {len(papers)}\n"]
    for p in papers:
        authors = ", ".join(p.get("authors", [])[:3]) if p.get("authors") else "?"
        year = str(p.get("year")) if p.get("year") else "-"
        domain = p.get("domain", "-")
        lines.append(f"`{p['id']}` | {p['title'][:80]} | {authors} | {year} | {domain}")
    return "\n".join(lines)
