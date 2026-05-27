from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.llm import cosine, llm_client


class VectorStore:
    COLLECTION = "paper_summaries"

    def __init__(self) -> None:
        settings.vector_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        self.client: Any = None
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            if settings.chroma_host:
                self.client = chromadb.HttpClient(
                    host=settings.chroma_host,
                    port=settings.chroma_port,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
            else:
                self.client = chromadb.PersistentClient(
                    path=str(settings.vector_dir),
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
        except Exception:
            self.client = None

    def upsert(
        self,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        embeddings = llm_client.embed_texts(texts)
        if self.client:
            try:
                col = self.client.get_or_create_collection(self.COLLECTION)
                col.upsert(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)
                return
            except Exception:
                self.client = None
        self._local_upsert(ids, texts, metadatas, embeddings)

    def query(self, text: str, top_k: int = 5) -> list[dict[str, Any]]:
        if self.client:
            try:
                col = self.client.get_or_create_collection(self.COLLECTION)
                result = col.query(
                    query_embeddings=llm_client.embed_texts([text]),
                    n_results=top_k,
                    include=["documents", "metadatas", "distances"],
                )
                rows: list[dict[str, Any]] = []
                for idx, doc_id in enumerate(result.get("ids", [[]])[0]):
                    distance = result.get("distances", [[]])[0][idx]
                    rows.append(
                        {
                            "id": doc_id,
                            "text": result.get("documents", [[]])[0][idx],
                            "metadata": result.get("metadatas", [[]])[0][idx],
                            "score": 1.0 / (1.0 + float(distance)),
                        }
                    )
                return rows
            except Exception:
                self.client = None
        return self._local_query(text, top_k)

    def delete(self, ids: list[str] | None = None, where: dict[str, Any] | None = None) -> None:
        if self.client:
            try:
                col = self.client.get_or_create_collection(self.COLLECTION)
                col.delete(ids=ids, where=where)
                return
            except Exception:
                self.client = None
        records = self._read_local()
        kept = []
        for row in records:
            if ids and row["id"] in ids:
                continue
            if where and all(row["metadata"].get(k) == v for k, v in where.items()):
                continue
            kept.append(row)
        self._write_local(kept)

    def reset(self) -> None:
        if self.client:
            try:
                self.client.delete_collection(self.COLLECTION)
            except Exception:
                pass
            return
        path = settings.vector_dir / f"{self.COLLECTION}.json"
        path.unlink(missing_ok=True)

    # ── Local JSON fallback ─────────────────────────────────────

    def _local_upsert(
        self,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> None:
        existing = {row["id"]: row for row in self._read_local()}
        for idx, item_id in enumerate(ids):
            existing[item_id] = {
                "id": item_id,
                "text": texts[idx],
                "metadata": metadatas[idx],
                "embedding": embeddings[idx],
            }
        self._write_local(list(existing.values()))

    def _local_query(self, text: str, top_k: int) -> list[dict[str, Any]]:
        query_vector = llm_client.embed_texts([text])[0]
        rows = []
        for row in self._read_local():
            score = cosine(query_vector, row.get("embedding", []))
            rows.append(
                {
                    "id": row["id"],
                    "text": row["text"],
                    "metadata": row["metadata"],
                    "score": score,
                }
            )
        return sorted(rows, key=lambda item: item["score"], reverse=True)[:top_k]

    def _local_path(self) -> Path:
        return settings.vector_dir / f"{self.COLLECTION}.json"

    def _read_local(self) -> list[dict[str, Any]]:
        path = self._local_path()
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_local(self, rows: list[dict[str, Any]]) -> None:
        self._local_path().write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )


vector_store = VectorStore()
