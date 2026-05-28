from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from app.core.langchain_factory import get_chat_model
from app.db.chroma import vector_store
from app.db.sqlite import db
from app.models.paper import PaperBrief


class RerankResult(BaseModel):
    """LLM reranking: indices sorted by relevance (most relevant first)."""
    ranked_indices: list[int]


RERANK_PROMPT = """Rank the following papers by relevance to the search query.
Return a JSON object with "ranked_indices" listing ALL candidate indices in descending order of relevance.
Irrelevant papers should be ranked last.

Query: {query}

Candidates:
{candidates}"""


class PaperRetrievalAgent:
    # ── Sync API (used by FastAPI routes) ────────────────────────

    def search(
        self,
        query: str | None = None,
        top_k: int = 6,
        year_from: int | None = None,
        year_to: int | None = None,
        domain: str | None = None,
        tags: list[str] | None = None,
        is_favorite: bool | None = None,
    ) -> list[PaperBrief]:
        results: list[PaperBrief] = []

        if query and query.strip():
            # Fetch more candidates for reranking
            fetch_k = max(top_k * 3, 20)
            hits = vector_store.query(query.strip(), top_k=fetch_k)
            for hit in hits:
                meta = hit.get("metadata", {})
                paper_id = meta.get("paper_id", "")
                paper = db.get_paper(paper_id)
                if not paper:
                    continue
                if not self._matches_filters(paper, year_from, year_to, domain, tags, is_favorite):
                    continue
                results.append(
                    PaperBrief(
                        id=paper["id"],
                        title=paper["title"],
                        authors=paper.get("authors", []),
                        year=paper.get("year"),
                        snippet=hit.get("text", "")[:500],
                    )
                )
            if len(results) > top_k:
                results = self._rerank_sync(query.strip(), results)[:top_k]
        else:
            papers = db.list_papers(
                query=query,
                year_from=year_from,
                year_to=year_to,
                domain=domain,
                tags=tags,
                is_favorite=is_favorite,
            )
            for paper in papers:
                results.append(
                    PaperBrief(
                        id=paper["id"],
                        title=paper["title"],
                        authors=paper.get("authors", []),
                        year=paper.get("year"),
                        snippet=paper.get("abstract_zh") or paper.get("abstract") or "",
                    )
                )

        return results[:top_k]

    # ── Async API (used by QA graph for parallel search) ─────────

    async def search_async(
        self,
        query: str | None = None,
        top_k: int = 6,
        year_from: int | None = None,
        year_to: int | None = None,
        domain: str | None = None,
        tags: list[str] | None = None,
        is_favorite: bool | None = None,
    ) -> list[PaperBrief]:
        """Run semantic and metadata searches in parallel, then merge and rerank."""
        if not query or not query.strip():
            return self.search(
                query=query, top_k=top_k,
                year_from=year_from, year_to=year_to,
                domain=domain, tags=tags, is_favorite=is_favorite,
            )

        fetch_k = max(top_k * 3, 20)

        async def semantic() -> list[dict[str, Any]]:
            return await asyncio.to_thread(vector_store.query, query.strip(), fetch_k)

        async def metadata() -> list[dict[str, Any]]:
            return await asyncio.to_thread(
                db.list_papers,
                query=None,  # metadata filter only, semantic handles content
                year_from=year_from, year_to=year_to,
                domain=domain, tags=tags, is_favorite=is_favorite,
            )

        semantic_hits, metadata_papers = await asyncio.gather(
            semantic(), metadata(),
        )

        # Merge: semantic results first (ranked), then fill with metadata matches
        seen: set[str] = set()
        results: list[PaperBrief] = []

        for hit in semantic_hits:
            meta = hit.get("metadata", {})
            paper_id = meta.get("paper_id", "")
            if paper_id in seen:
                continue
            paper = db.get_paper(paper_id)
            if not paper:
                continue
            if not self._matches_filters(paper, year_from, year_to, domain, tags, is_favorite):
                continue
            seen.add(paper_id)
            results.append(
                PaperBrief(
                    id=paper["id"],
                    title=paper["title"],
                    authors=paper.get("authors", []),
                    year=paper.get("year"),
                    snippet=hit.get("text", "")[:500],
                )
            )

        # Append metadata-only matches not already included
        for paper in metadata_papers:
            if paper["id"] in seen:
                continue
            seen.add(paper["id"])
            results.append(
                PaperBrief(
                    id=paper["id"],
                    title=paper["title"],
                    authors=paper.get("authors", []),
                    year=paper.get("year"),
                    snippet=paper.get("abstract_zh") or paper.get("abstract") or "",
                )
            )

        # Rerank if we have more than top_k candidates
        if len(results) > top_k:
            results = await self._rerank_async(query.strip(), results)

        return results[:top_k]

    # ── QA helper (called by QA graph nodes) ─────────────────────

    def retrieve_for_qa(self, question: str, top_k: int = 6) -> list[PaperBrief]:
        """Synchronous convenience wrapper — single-query retrieval."""
        return self.search(query=question, top_k=top_k)

    async def retrieve_for_qa_async(self, question: str, top_k: int = 6) -> list[PaperBrief]:
        """Async retrieval with parallel semantic + metadata search."""
        return await self.search_async(query=question, top_k=top_k)

    # ── Reranking ─────────────────────────────────────────────────

    @staticmethod
    def _format_candidate(c: PaperBrief, idx: int) -> str:
        """Format a single candidate for the reranking prompt."""
        authors = ", ".join(c.authors[:3]) if c.authors else "Unknown"
        year = f" ({c.year})" if c.year else ""
        snippet = c.snippet[:300] if c.snippet else ""
        return f"[{idx}] {c.title} — {authors}{year}\n    {snippet}"

    def _rerank_sync(self, query: str, candidates: list[PaperBrief]) -> list[PaperBrief]:
        """LLM listwise reranking — synchronous."""
        if len(candidates) <= 1:
            return candidates

        formatted = "\n\n".join(
            self._format_candidate(c, i) for i, c in enumerate(candidates)
        )
        prompt = RERANK_PROMPT.format(query=query, candidates=formatted)

        try:
            chat = get_chat_model()
            structured = chat.with_structured_output(RerankResult, method="json_schema")
            result: RerankResult = structured.invoke(prompt)
            ranked = self._apply_rank(result, candidates)
            return ranked if ranked else candidates
        except Exception:
            return candidates  # fallback: keep original order

    async def _rerank_async(self, query: str, candidates: list[PaperBrief]) -> list[PaperBrief]:
        """LLM listwise reranking — asynchronous."""
        if len(candidates) <= 1:
            return candidates

        formatted = "\n\n".join(
            self._format_candidate(c, i) for i, c in enumerate(candidates)
        )
        prompt = RERANK_PROMPT.format(query=query, candidates=formatted)

        try:
            chat = get_chat_model()
            structured = chat.with_structured_output(RerankResult, method="json_schema")
            result: RerankResult = await structured.ainvoke(prompt)
            ranked = self._apply_rank(result, candidates)
            return ranked if ranked else candidates
        except Exception:
            return candidates  # fallback: keep original order

    @staticmethod
    def _apply_rank(result: RerankResult, candidates: list[PaperBrief]) -> list[PaperBrief]:
        """Reorder candidates by LLM-ranked indices. Missing/invalid indices go last."""
        n = len(candidates)
        valid = [i for i in result.ranked_indices if 0 <= i < n]
        missing = [i for i in range(n) if i not in valid]
        indices = valid + missing
        return [candidates[i] for i in indices[:n]]

    # ── Filters ──────────────────────────────────────────────────

    @staticmethod
    def _matches_filters(
        paper: dict[str, Any],
        year_from: int | None,
        year_to: int | None,
        domain: str | None,
        tags: list[str] | None,
        is_favorite: bool | None,
    ) -> bool:
        year = paper.get("year")
        if year_from is not None and (year is None or year < year_from):
            return False
        if year_to is not None and (year is None or year > year_to):
            return False
        if domain and paper.get("domain") != domain:
            return False
        if is_favorite is not None and bool(paper.get("is_favorite")) != is_favorite:
            return False
        if tags:
            paper_tags = set(paper.get("tags", []))
            if not paper_tags.intersection(tags):
                return False
        return True


paper_retrieval_agent = PaperRetrievalAgent()
