from __future__ import annotations

from app.db.sqlite import db
from app.mcp.server.server import mcp


@mcp.prompt()
def paper_qa(question: str) -> str:
    """Generate a prompt that asks an LLM to answer a research question
    using the PaperAgent paper library. The LLM should use the search_papers
    and get_paper tools to find relevant papers before answering."""
    return (
        f"You are a research assistant with access to a personal paper library. "
        f"The user wants to know: {question}\n\n"
        f"First, use the `search_papers` tool to find relevant papers in the library. "
        f"Then use `get_paper` to read the full details of the most relevant ones. "
        f"Finally, synthesize an answer that cites specific papers with their IDs. "
        f"If comparing papers, structure your response with clear comparison points. "
        f"If the library doesn't contain enough information, say so clearly."
    )


@mcp.prompt()
def paper_review(paper_id: str) -> str:
    """Generate a prompt for reviewing or summarizing a specific paper.
    The LLM should read the paper via the paper:// resource and provide
    a structured review."""
    paper = db.get_paper(paper_id)
    title = paper["title"] if paper else paper_id

    return (
        f"Please review the following academic paper: **{title}**\n\n"
        f"Read the full paper details using the `paper://{paper_id}` resource "
        f"or the `get_paper` tool.\n\n"
        f"Then provide a structured review covering:\n"
        f"1. **Summary**: What problem does this paper address and what is the key idea?\n"
        f"2. **Strengths**: What are the main contributions and novel aspects?\n"
        f"3. **Weaknesses/Limitations**: What are the acknowledged or potential shortcomings?\n"
        f"4. **Impact**: How does this work relate to or advance the broader field?\n"
        f"5. **Questions/Further Reading**: What questions remain open? What papers would you read next?"
    )
