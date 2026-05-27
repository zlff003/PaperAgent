from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.core.config import settings
from app.core.langchain_factory import get_chat_model
from app.db.chroma import vector_store
from app.db.sqlite import db
from app.models.paper import PaperBasicInfo, PaperDeepInfo


class PaperIngestionAgent:
    # ── Public API ───────────────────────────────────────────────

    def ingest_upload(self, file: UploadFile) -> dict[str, Any]:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise ValueError("Only PDF files are supported.")

        paper_id = str(uuid.uuid4())
        settings.paper_dir.mkdir(parents=True, exist_ok=True)
        target = settings.paper_dir / f"{paper_id}.pdf"
        with target.open("wb") as output:
            shutil.copyfileobj(file.file, output)

        page_count = self._quick_page_count(target)
        paper = db.insert_paper(
            {
                "id": paper_id,
                "title": Path(file.filename).stem,
                "authors": [],
                "year": None,
                "abstract": None,
                "abstract_zh": None,
                "contributions": None,
                "methods": None,
                "results": None,
                "limitations": None,
                "conclusion": None,
                "keywords": [],
                "domain": None,
                "file_path": str(target),
                "page_count": page_count,
                "is_favorite": 0,
                "parse_status": "queued",
                "parse_progress": 0,
                "parse_step": "等待后台提取",
            }
        )

        from app.core.task_queue import parse_queue

        parse_queue.enqueue_parse(paper_id)
        return paper

    def process_paper(self, paper_id: str) -> None:
        paper = db.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found.")
        path = Path(paper["file_path"])
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")

        try:
            db.update_parse_status(paper_id, "extracting", 15, "正在提取PDF文本")
            full_text = self._extract_full_text(path)

            db.update_parse_status(paper_id, "analyzing_basic", 35, "LLM 提取基础信息(标题/作者/年份)")
            basic_info = self._llm_extract_basic(full_text)

            db.update_parse_status(paper_id, "analyzing_deep", 65, "LLM 提取深度内容(摘要/方法/贡献等)")
            deep_info = self._llm_extract_deep(full_text, basic_info)

            merged = self._normalize_merged({**basic_info, **deep_info})

            db.update_paper_metadata(
                paper_id,
                {
                    "title": merged.get("title") or paper["title"],
                    "authors": merged.get("authors") or [],
                    "year": merged.get("year"),
                    "abstract": merged.get("abstract"),
                    "abstract_zh": merged.get("abstract_zh"),
                    "contributions": merged.get("contributions"),
                    "methods": merged.get("methods"),
                    "results": merged.get("results"),
                    "limitations": merged.get("limitations"),
                    "conclusion": merged.get("conclusion"),
                    "keywords": merged.get("keywords", []),
                    "domain": merged.get("domain"),
                    "page_count": self._quick_page_count(path),
                },
            )

            db.update_parse_status(paper_id, "indexing", 90, "正在写入向量索引")
            self._index_paper(paper_id)

            db.update_parse_status(paper_id, "ready", 100, "提取完成")
        except Exception as exc:
            db.update_parse_status(paper_id, "failed", 0, "提取失败", str(exc))
            raise

    def re_extract(self, paper_id: str) -> None:
        paper = db.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found.")
        path = Path(paper["file_path"])
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")

        vector_store.delete(where={"paper_id": paper_id})
        self.process_paper(paper_id)

    # ── PDF parsing ──────────────────────────────────────────────

    def _extract_full_text(self, path: Path) -> str:
        try:
            import fitz
        except Exception as exc:
            raise RuntimeError("PyMuPDF is required to parse PDF files.") from exc

        doc = fitz.open(path)
        texts: list[str] = []
        for page in doc:
            text = page.get_text("text") or ""
            if text.strip():
                texts.append(text)
        doc.close()
        full = "\n\n".join(texts)
        return full[:32000]

    def _quick_page_count(self, path: Path) -> int:
        try:
            import fitz
            with fitz.open(path) as doc:
                return doc.page_count
        except Exception:
            return 0

    # ── LLM extraction (LangChain structured output) ─────────────

    def _llm_extract_basic(self, text: str, *, attempt: int = 1) -> dict[str, Any]:
        chat = get_chat_model()
        structured = chat.with_structured_output(PaperBasicInfo, method="json_schema")
        try:
            result: PaperBasicInfo = structured.invoke(
                "Extract the paper's title, full author list, and publication year.\n\n"
                f"Paper text:\n{text[:12000]}"
            )
            return {"title": result.title, "authors": result.authors, "year": result.year}
        except Exception:
            if attempt < 3:
                return self._llm_extract_basic(text, attempt=attempt + 1)
            return {}

    def _llm_extract_deep(
        self, text: str, basic: dict[str, Any], *, attempt: int = 1
    ) -> dict[str, Any]:
        title = basic.get("title", "")
        chat = get_chat_model()
        structured = chat.with_structured_output(PaperDeepInfo, method="json_schema")
        try:
            result: PaperDeepInfo = structured.invoke(
                f"Paper title: {title}\n\n"
                "Extract the following from the paper text:\n"
                "- abstract: copy the original abstract verbatim if present\n"
                "- abstract_zh: a Chinese summary of the abstract (2-4 sentences)\n"
                "- contributions: main contributions and innovations (in Chinese, bullet-style)\n"
                "- methods: methodology, models, algorithms used (in Chinese, detailed)\n"
                "- results: key experimental results and findings (in Chinese)\n"
                "- limitations: limitations acknowledged by the authors (in Chinese)\n"
                "- conclusion: main conclusions (in Chinese)\n"
                "- keywords: list of 3-8 key topic words\n"
                "- domain: the research domain/field (e.g. NLP, CV, Systems, Theory)\n\n"
                "Use null for any field you cannot find.\n\n"
                f"Paper text:\n{text[:28000]}"
            )
            return {
                "abstract": result.abstract,
                "abstract_zh": result.abstract_zh,
                "contributions": result.contributions,
                "methods": result.methods,
                "results": result.results,
                "limitations": result.limitations,
                "conclusion": result.conclusion,
                "keywords": result.keywords,
                "domain": result.domain,
            }
        except Exception:
            if attempt < 3:
                return self._llm_extract_deep(text, basic, attempt=attempt + 1)
            return {}

    # ── Normalization ────────────────────────────────────────────

    @staticmethod
    def _normalize_merged(data: dict[str, Any]) -> dict[str, Any]:
        """Ensure all text fields are strings, not lists or other types."""
        keywords = data.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        elif not isinstance(keywords, list):
            keywords = []
        data["keywords"] = [str(k) for k in keywords if k]

        authors = data.get("authors", [])
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(",") if a.strip()]
        elif not isinstance(authors, list):
            authors = []
        data["authors"] = [str(a) for a in authors if a]

        text_fields = [
            "abstract", "abstract_zh", "contributions", "methods",
            "results", "limitations", "conclusion", "domain",
        ]
        for field in text_fields:
            value = data.get(field)
            if isinstance(value, list):
                data[field] = "\n".join(str(v) for v in value)
            elif value is not None and not isinstance(value, str):
                data[field] = str(value)

        year = data.get("year")
        if year is not None:
            try:
                data["year"] = int(year)
            except (ValueError, TypeError):
                data["year"] = None

        return data

    # ── Vector indexing ──────────────────────────────────────────

    def _index_paper(self, paper_id: str) -> None:
        paper = db.get_paper(paper_id)
        if not paper:
            return
        parts = [
            paper.get("abstract") or "",
            paper.get("contributions") or "",
            paper.get("methods") or "",
            paper.get("results") or "",
            paper.get("conclusion") or "",
        ]
        summary_text = " ".join(p for p in parts if p)
        if not summary_text:
            summary_text = paper.get("abstract_zh") or paper["title"]
        if not summary_text.strip():
            return
        vector_store.upsert(
            ids=[paper_id],
            texts=[summary_text],
            metadatas=[
                {
                    "paper_id": paper_id,
                    "title": paper["title"],
                    "authors": ", ".join(paper.get("authors", [])),
                    "year": paper.get("year") or 0,
                    "domain": paper.get("domain") or "",
                    "keywords": ", ".join(paper.get("keywords", [])),
                }
            ],
        )


paper_ingestion_agent = PaperIngestionAgent()
