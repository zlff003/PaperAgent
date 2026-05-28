"""
Library Agent — manages and queries the paper library via chat.
Supports listing, status checks, deletion, re-extraction, and library stats.
No LLM streaming — the Responder node handles final output.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage

from app.agents.paper_retrieval import paper_retrieval_agent
from app.db.sqlite import db
from app.supervisor.state import SupervisorState

CMD_KEYWORDS = [
    "删除", "删掉", "移除", "去掉", "delete", "remove", "drop",
    "重新提取", "重新解析", "re-extract", "re extract",
]


def _append_history(state: SupervisorState, agent: str) -> list[str]:
    return state.get("agent_history", []) + [agent]


def _make_paper_ctx(paper: dict[str, Any]) -> dict[str, Any]:
    """Extract a minimal papers_context entry from a DB paper dict."""
    return {
        "id": paper.get("id", ""),
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
        "year": paper.get("year"),
        # snippet intentionally empty for library — analysis will enrich from DB
    }


def _extract_search_terms(query: str) -> str:
    q = query
    for kw in CMD_KEYWORDS:
        q = q.replace(kw, " ")
    return " ".join(q.split()).strip()


def _format_paper_hit(p: dict[str, Any], index: int) -> str:
    title = p.get("title", "Unknown")
    authors = ", ".join(p.get("authors", [])[:2]) or "Unknown"
    year = f" ({p.get('year')})" if p.get("year") else ""
    return f"**{index}.** **{title}** — {authors}{year}"


async def _search_then_act(
    query: str,
    action: str,
    state: SupervisorState,
) -> dict[str, Any]:
    """Semantic search for papers matching the user's description, then act or present choices."""
    search_terms = _extract_search_terms(query)

    if not search_terms:
        return {
            "messages": [
                AIMessage(content=(
                    f"要{action}哪篇论文？请说明论文标题或关键词。\n"
                    f"例如：\"{action} Attention Is All You Need\""
                ))
            ],
            "agent_history": _append_history(state, "library"),
        }

    matches = await paper_retrieval_agent.search_async(query=search_terms, top_k=6)

    if not matches:
        return {
            "messages": [
                AIMessage(content=f'No papers found matching "{search_terms}".')
            ],
            "agent_history": _append_history(state, "library"),
        }

    if len(matches) == 1:
        p = matches[0]
        return await _execute_action(p.id, p.title, action, state)

    # Multiple matches — list for disambiguation; populate papers_context for follow-up
    lines = [
        f'Found {len(matches)} papers matching **"{search_terms}"**. '
        f"Which one should I {action}?\n",
    ]
    papers_ctx = []
    for i, p in enumerate(matches, 1):
        snippet = (p.snippet or "")[:100]
        lines.append(_format_paper_hit({"title": p.title, "authors": p.authors, "year": p.year}, i))
        if snippet:
            lines.append(f"  > {snippet}")
        papers_ctx.append({
            "id": p.id,
            "title": p.title,
            "authors": p.authors,
            "year": p.year,
        })
    lines.append(f"\nReply with the number (1-{len(matches)}) or the exact title.")

    return {
        "messages": [AIMessage(content="\n".join(lines))],
        "papers_context": papers_ctx,
        "agent_history": _append_history(state, "library"),
    }


async def _execute_action(
    paper_id: str, title: str, action: str, state: SupervisorState
) -> dict[str, Any]:
    if action == "delete":
        try:
            from app.db.chroma import vector_store as _vs
            _vs.delete(where={"paper_id": paper_id})
        except Exception:
            pass
        db.delete_paper(paper_id)
        return {
            "messages": [AIMessage(content=f"Deleted: **{title}**")],
            "agent_history": _append_history(state, "library"),
        }

    if action == "re-extract":
        from app.core.task_queue import parse_queue
        parse_queue.enqueue_parse(paper_id)
        return {
            "messages": [AIMessage(content=f"Re-extraction queued for: **{title}**")],
            "agent_history": _append_history(state, "library"),
        }

    return {
        "messages": [AIMessage(content="Unknown action.")],
        "agent_history": _append_history(state, "library"),
    }


async def library_node(state: SupervisorState) -> dict[str, Any]:
    """
    Handle library management commands from chat.
    Keyword-routed: no LLM call for decision, just structured operations.
    """
    messages = state.get("messages", [])
    query = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            query = msg.content
            break

    if not query:
        return _help(state)

    q = query.strip()

    # ── Number selection (follow-up to search results) ────────────
    number_match = re.match(r"^\s*(\d+)\s*$", q)
    if number_match:
        idx = int(number_match.group(1)) - 1
        action = _detect_pending_action(messages)
        if action and 0 <= idx < 6:
            prev_query = _get_prev_user_query(messages)
            if prev_query:
                search_terms = _extract_search_terms(prev_query)
                matches = await paper_retrieval_agent.search_async(query=search_terms, top_k=6)
                if 0 <= idx < len(matches):
                    p = matches[idx]
                    return await _execute_action(p.id, p.title, action, state)

    ql = q.lower()

    # ── Delete ────────────────────────────────────────────────────
    if any(kw in ql for kw in ("删除", "删掉", "delete", "移除", "remove")):
        return await _search_then_act(query, "delete", state)

    # ── Re-extract ────────────────────────────────────────────────
    if any(kw in ql for kw in ("re-extract", "re extract", "重新提取", "重新解析")):
        return await _search_then_act(query, "re-extract", state)

    # ── Processing status ─────────────────────────────────────────
    if any(kw in ql for kw in ("处理", "解析", "进度", "状态", "提取完了", "processing", "status", "progress", "ready")):
        return _processing_status(state)

    # ── Library statistics ────────────────────────────────────────
    if any(kw in ql for kw in ("统计", "多少篇", "数量", "stat", "count", "overview", "总览")):
        return _library_stats(state)

    # ── Favorites ─────────────────────────────────────────────────
    if any(kw in ql for kw in ("收藏", "favorite", "星标")):
        return _list_favorites(state)

    # ── List papers ───────────────────────────────────────────────
    if any(kw in ql for kw in (
        "列出", "有哪些", "我有哪些", "所有论文", "论文列表",
        "list", "show", "papers", "all papers", "库",
    )):
        return _list_all_papers(query, state)

    # ── Default: help ─────────────────────────────────────────────
    return _help(state)


def _detect_pending_action(messages: list) -> str | None:
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "ai":
            content = msg.content.lower() if hasattr(msg, "content") else ""
            if "delete" in content or "删除" in content:
                return "delete"
            if "re-extract" in content or "re extract" in content or "重新提取" in content:
                return "re-extract"
    return None


def _get_prev_user_query(messages: list) -> str:
    user_msgs = []
    for msg in messages:
        if hasattr(msg, "type") and msg.type == "human":
            user_msgs.append(msg.content)
    return user_msgs[-2] if len(user_msgs) >= 2 else ""


# ── Handlers ──────────────────────────────────────────────────────────


def _help(state: SupervisorState) -> dict[str, Any]:
    return {
        "messages": [
            AIMessage(content=(
                "[library] Manage your paper library:\n\n"
                "- 列出所有论文\n"
                "- 查看处理状态\n"
                "- 论文库统计\n"
                "- 收藏的论文\n"
                "- 删除关于XXX的论文\n"
                "- 重新提取关于XXX的论文"
            ))
        ],
        "agent_history": _append_history(state, "library"),
    }


def _list_all_papers(query: str, state: SupervisorState) -> dict[str, Any]:
    papers = db.list_papers()

    if not papers:
        return {
            "messages": [AIMessage(content="[library] The paper library is empty.")],
            "papers_context": [],
            "agent_history": _append_history(state, "library"),
        }

    # Filter by domain if specified
    domains = {"nlp": "NLP", "cv": "CV", "systems": "Systems", "theory": "Theory",
               "ml": "ML", "ai": "AI", "robotics": "Robotics", "data mining": "Data Mining"}
    filtered_domain = None
    for key, label in domains.items():
        if key in query.lower():
            filtered_domain = label
            break

    filtered = papers
    if filtered_domain:
        filtered = [p for p in papers if (p.get("domain") or "").lower() == filtered_domain.lower()]

    lines = [f"[library] {len(filtered)} papers" + (f" in {filtered_domain}" if filtered_domain else "")]
    for i, p in enumerate(filtered[:20], 1):
        title = p.get("title", "Unknown")
        authors = ", ".join(p.get("authors", [])[:2]) or "Unknown"
        year = str(p.get("year")) if p.get("year") else "—"
        domain = f" [{p.get('domain')}]" if p.get("domain") else ""
        status = "⭐" if p.get("is_favorite") else ""
        parse_status = p.get("parse_status", "unknown")
        status_icon = {"ready": "✅", "extracting": "🔄", "queued": "⏳", "failed": "❌"}.get(parse_status, "❓")
        lines.append(f"**{i}.** {status_icon}{status} **{title}** — {authors} ({year}){domain}")

    if len(filtered) > 20:
        lines.append(f"\n... and {len(filtered) - 20} more papers.")

    # Populate papers_context so follow-up questions can reference these papers
    papers_ctx = [_make_paper_ctx(p) for p in filtered]

    return {
        "messages": [AIMessage(content="\n".join(lines))],
        "papers_context": papers_ctx,
        "agent_history": _append_history(state, "library"),
    }


def _processing_status(state: SupervisorState) -> dict[str, Any]:
    papers = db.list_papers()

    if not papers:
        return {
            "messages": [AIMessage(content="[library] No papers in the library yet.")],
            "papers_context": [],
            "agent_history": _append_history(state, "library"),
        }

    status_groups: dict[str, list[str]] = {"ready": [], "extracting": [], "queued": [], "failed": [], "unknown": []}
    for p in papers:
        s = p.get("parse_status", "unknown")
        title = p.get("title", "Unknown")
        status_groups.setdefault(s, []).append(title)

    lines = ["[library] Processing Status\n"]
    labels = {"ready": "✅ Ready", "extracting": "🔄 Extracting", "queued": "⏳ Queued", "failed": "❌ Failed", "unknown": "❓ Unknown"}
    for key, label in labels.items():
        items = status_groups.get(key, [])
        if items:
            lines.append(f"**{label}** ({len(items)}):")
            for title in items[:5]:
                lines.append(f"  - {title}")
            if len(items) > 5:
                lines.append(f"  ... and {len(items) - 5} more")
            lines.append("")

    papers_ctx = [_make_paper_ctx(p) for p in papers]

    return {
        "messages": [AIMessage(content="\n".join(lines))],
        "papers_context": papers_ctx,
        "agent_history": _append_history(state, "library"),
    }


def _library_stats(state: SupervisorState) -> dict[str, Any]:
    papers = db.list_papers()

    if not papers:
        return {
            "messages": [AIMessage(content="[library] No papers in the library yet.")],
            "agent_history": _append_history(state, "library"),
        }

    total = len(papers)
    domains: dict[str, int] = {}
    years: dict[int, int] = {}
    ready_count = 0
    favorite_count = 0
    total_pages = 0

    for p in papers:
        d = p.get("domain") or "Unknown"
        domains[d] = domains.get(d, 0) + 1
        y = p.get("year")
        if y:
            years[y] = years.get(y, 0) + 1
        if p.get("parse_status") == "ready":
            ready_count += 1
        if p.get("is_favorite"):
            favorite_count += 1
        total_pages += p.get("page_count", 0) or 0

    lines = [
        "[library] Statistics\n",
        f"- Total papers: **{total}**",
        f"- Fully extracted: **{ready_count}** ({ready_count * 100 // total if total else 0}%)",
        f"- Favorites: **{favorite_count}**",
        f"- Total pages: **{total_pages}**",
        "",
        "By Domain:",
    ]
    for d, c in sorted(domains.items(), key=lambda x: -x[1]):
        lines.append(f"  - {d}: {c}")

    if years:
        lines.append("\nBy Year:")
        for y, c in sorted(years.items(), reverse=True)[:10]:
            lines.append(f"  - {y}: {c}")

    return {
        "messages": [AIMessage(content="\n".join(lines))],
        "agent_history": _append_history(state, "library"),
    }


def _list_favorites(state: SupervisorState) -> dict[str, Any]:
    papers = [p for p in db.list_papers() if p.get("is_favorite")]

    if not papers:
        return {
            "messages": [AIMessage(content="[library] No favorite papers yet.")],
            "papers_context": [],
            "agent_history": _append_history(state, "library"),
        }

    lines = [f"[library] Favorites — {len(papers)} papers"]
    for i, p in enumerate(papers, 1):
        title = p.get("title", "Unknown")
        authors = ", ".join(p.get("authors", [])[:2]) or "Unknown"
        year = str(p.get("year")) if p.get("year") else "—"
        lines.append(f"**{i}.** ⭐ **{title}** — {authors} ({year})")

    papers_ctx = [_make_paper_ctx(p) for p in papers]

    return {
        "messages": [AIMessage(content="\n".join(lines))],
        "papers_context": papers_ctx,
        "agent_history": _append_history(state, "library"),
    }
