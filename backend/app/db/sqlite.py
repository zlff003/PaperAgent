from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from app.core.config import settings


class SQLiteStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or settings.sqlite_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS papers (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    authors TEXT,
                    year INTEGER,
                    abstract TEXT,
                    abstract_zh TEXT,
                    contributions TEXT,
                    methods TEXT,
                    results TEXT,
                    limitations TEXT,
                    conclusion TEXT,
                    keywords TEXT,
                    domain TEXT,
                    file_path TEXT NOT NULL,
                    page_count INTEGER DEFAULT 0,
                    is_favorite INTEGER DEFAULT 0,
                    parse_status TEXT DEFAULT 'queued',
                    parse_progress INTEGER DEFAULT 0,
                    parse_step TEXT,
                    parse_error TEXT,
                    parsed_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tags (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS paper_tags (
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY (paper_id, tag_id)
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    cited_papers TEXT,
                    session_id TEXT,
                    turn_index INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS parse_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    task_type TEXT NOT NULL DEFAULT 'parse_paper',
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    locked_at DATETIME,
                    finished_at DATETIME
                );
                """
            )
            self._migrate_columns(conn)

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        # Note: defaults omitted in migration to support older SQLite (<3.37)
        self._ensure_columns(
            conn,
            "papers",
            {
                "abstract_zh": "TEXT",
                "contributions": "TEXT",
                "methods": "TEXT",
                "results": "TEXT",
                "limitations": "TEXT",
                "conclusion": "TEXT",
                "keywords": "TEXT",
                "domain": "TEXT",
                "is_favorite": "INTEGER",
                "parse_status": "TEXT",
                "parse_progress": "INTEGER",
                "parse_step": "TEXT",
                "parse_error": "TEXT",
                "parsed_at": "DATETIME",
                "updated_at": "DATETIME",
            },
        )
        self._ensure_columns(
            conn,
            "conversations",
            {
                "cited_papers": "TEXT",
                "session_id": "TEXT",
                "turn_index": "INTEGER",
            },
        )
        # Ensure index exists (CREATE INDEX IF NOT EXISTS is safe)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)"
        )

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    # ── Paper CRUD ──────────────────────────────────────────────

    def insert_paper(self, item: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO papers
                (id, title, authors, year, abstract, abstract_zh,
                 contributions, methods, results, limitations, conclusion,
                 keywords, domain, file_path, page_count, is_favorite,
                 parse_status, parse_progress, parse_step, parse_error, parsed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["title"],
                    json.dumps(item.get("authors", []), ensure_ascii=False),
                    item.get("year"),
                    item.get("abstract"),
                    item.get("abstract_zh"),
                    item.get("contributions"),
                    item.get("methods"),
                    item.get("results"),
                    item.get("limitations"),
                    item.get("conclusion"),
                    json.dumps(item.get("keywords", []), ensure_ascii=False),
                    item.get("domain"),
                    item["file_path"],
                    item.get("page_count", 0),
                    item.get("is_favorite", 0),
                    item.get("parse_status", "queued"),
                    item.get("parse_progress", 0),
                    item.get("parse_step"),
                    item.get("parse_error"),
                    item.get("parsed_at"),
                ),
            )
        return self.get_paper(item["id"])

    def update_paper_metadata(self, paper_id: str, item: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE papers
                SET title = ?, authors = ?, year = ?, abstract = ?,
                    abstract_zh = ?, contributions = ?, methods = ?, results = ?,
                    limitations = ?, conclusion = ?, keywords = ?, domain = ?,
                    page_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    item["title"],
                    json.dumps(item.get("authors", []), ensure_ascii=False),
                    item.get("year"),
                    item.get("abstract"),
                    item.get("abstract_zh"),
                    item.get("contributions"),
                    item.get("methods"),
                    item.get("results"),
                    item.get("limitations"),
                    item.get("conclusion"),
                    json.dumps(item.get("keywords", []), ensure_ascii=False),
                    item.get("domain"),
                    item.get("page_count", 0),
                    datetime.utcnow().isoformat(),
                    paper_id,
                ),
            )
        return self.get_paper(paper_id)

    def update_parse_status(
        self, paper_id: str, status: str, progress: int,
        step: str | None = None, error: str | None = None,
    ) -> dict[str, Any] | None:
        parsed_at = datetime.utcnow().isoformat() if status in {"ready", "partial_ready"} else None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE papers
                SET parse_status = ?, parse_progress = ?, parse_step = ?,
                    parse_error = ?, parsed_at = COALESCE(?, parsed_at),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, progress, step, error, parsed_at,
                 datetime.utcnow().isoformat(), paper_id),
            )
        return self.get_paper(paper_id)

    def update_paper_fields(self, paper_id: str, update: dict[str, Any]) -> dict[str, Any] | None:
        set_parts = ["updated_at = ?"]
        params: list[Any] = [datetime.utcnow().isoformat()]
        for key in ("domain", "is_favorite"):
            if key in update:
                set_parts.append(f"{key} = ?")
                params.append(update[key])
        params.append(paper_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )
        # handle tags separately
        if "tags" in update and isinstance(update["tags"], list):
            self.set_paper_tags(paper_id, update["tags"])
        return self.get_paper(paper_id)

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return self._paper(row) if row else None

    def list_papers(
        self,
        query: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        domain: str | None = None,
        tags: list[str] | None = None,
        is_favorite: bool | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []

        if query:
            conditions.append(
                "(title LIKE ? OR authors LIKE ? OR keywords LIKE ? OR domain LIKE ?)"
            )
            pattern = f"%{query}%"
            params.extend([pattern, pattern, pattern, pattern])

        if year_from is not None:
            conditions.append("year >= ?")
            params.append(year_from)
        if year_to is not None:
            conditions.append("year <= ?")
            params.append(year_to)
        if domain:
            conditions.append("domain = ?")
            params.append(domain)
        if is_favorite is not None:
            conditions.append("is_favorite = ?")
            params.append(1 if is_favorite else 0)

        if tags:
            tag_placeholders = ", ".join(["?"] * len(tags))
            conditions.append(
                f"id IN (SELECT paper_id FROM paper_tags pt "
                f"JOIN tags t ON pt.tag_id = t.id WHERE t.name IN ({tag_placeholders}))"
            )
            params.extend(tags)

        sql = "SELECT * FROM papers"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC"

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._paper(row) for row in rows]

    def delete_paper(self, paper_id: str) -> dict[str, Any] | None:
        item = self.get_paper(paper_id)
        if item:
            with self.connect() as conn:
                conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        return item

    # ── Tags ────────────────────────────────────────────────────

    def create_tag(self, name: str) -> dict[str, Any]:
        tag_id = name.strip().lower().replace(" ", "_")
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tags (id, name) VALUES (?, ?)",
                (tag_id, name.strip()),
            )
            row = conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
        return dict(row)

    def list_tags(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.*, COUNT(pt.paper_id) AS paper_count
                FROM tags t
                LEFT JOIN paper_tags pt ON t.id = pt.tag_id
                GROUP BY t.id
                ORDER BY t.name
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_tag(self, tag_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            return cursor.rowcount > 0

    def set_paper_tags(self, paper_id: str, tag_names: list[str]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM paper_tags WHERE paper_id = ?", (paper_id,))
            for name in tag_names:
                tag = self.create_tag(name)
                conn.execute(
                    "INSERT OR IGNORE INTO paper_tags (paper_id, tag_id) VALUES (?, ?)",
                    (paper_id, tag["id"]),
                )

    def get_paper_tags(self, paper_id: str) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.name FROM tags t
                JOIN paper_tags pt ON t.id = pt.tag_id
                WHERE pt.paper_id = ?
                ORDER BY t.name
                """,
                (paper_id,),
            ).fetchall()
        return [row["name"] for row in rows]

    # ── Parse Tasks ─────────────────────────────────────────────

    def enqueue_parse_task(self, paper_id: str) -> int:
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM parse_tasks
                WHERE paper_id = ? AND task_type = 'parse_paper'
                  AND status IN ('queued', 'running')
                ORDER BY id DESC LIMIT 1
                """,
                (paper_id,),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cursor = conn.execute(
                """
                INSERT INTO parse_tasks (paper_id, task_type, status)
                VALUES (?, 'parse_paper', 'queued')
                """,
                (paper_id,),
            )
            return int(cursor.lastrowid)

    def claim_next_parse_task(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM parse_tasks
                WHERE task_type = 'parse_paper' AND status = 'queued'
                ORDER BY id LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE parse_tasks
                SET status = 'running', attempts = attempts + 1, locked_at = ?
                WHERE id = ?
                """,
                (datetime.utcnow().isoformat(), row["id"]),
            )
            return dict(row)

    def complete_parse_task(self, task_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE parse_tasks SET status = 'done', finished_at = ?, error = NULL
                WHERE id = ?
                """,
                (datetime.utcnow().isoformat(), task_id),
            )

    def fail_parse_task(self, task_id: int, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE parse_tasks SET status = 'failed', finished_at = ?, error = ?
                WHERE id = ?
                """,
                (datetime.utcnow().isoformat(), error[:2000], task_id),
            )

    # ── Conversations ───────────────────────────────────────────

    def insert_conversation(self, item: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, question, answer, cited_papers, session_id, turn_index)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["question"],
                    item["answer"],
                    json.dumps(item.get("cited_papers", []), ensure_ascii=False),
                    item.get("session_id"),
                    item.get("turn_index", 0),
                ),
            )
        return self.get_conversation(item["id"])

    def list_conversations(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY created_at DESC"
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return self._conversation(row) if row else None

    def list_conversations_by_session(
        self, session_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return all turns for a session, ordered by turn_index."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversations
                WHERE session_id = ?
                ORDER BY turn_index ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def get_last_conversations(
        self, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Return the most recent conversations across all sessions."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversations
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def delete_conversation(self, conversation_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            return cursor.rowcount > 0

    def delete_conversations_by_session(self, session_id: str) -> int:
        """Delete ALL conversation turns in a session. Returns count of deleted rows."""
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM conversations WHERE session_id = ?", (session_id,)
            )
            return cursor.rowcount

    # ── Reset ───────────────────────────────────────────────────

    def reset(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                DELETE FROM parse_tasks;
                DELETE FROM conversations;
                DELETE FROM paper_tags;
                DELETE FROM tags;
                DELETE FROM papers;
                """
            )

    # ── Internal helpers ────────────────────────────────────────

    def _paper(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["authors"] = json.loads(item.get("authors") or "[]")
        item["keywords"] = json.loads(item.get("keywords") or "[]")
        item["is_favorite"] = bool(item.get("is_favorite", 0))
        item["tags"] = self.get_paper_tags(item["id"])
        # Handle columns that may be NULL due to migration from older schema
        if not item.get("updated_at"):
            item["updated_at"] = item.get("created_at") or datetime.utcnow().isoformat()
        if not item.get("created_at"):
            item["created_at"] = datetime.utcnow().isoformat()
        return item

    @staticmethod
    def _conversation(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["cited_papers"] = json.loads(item.get("cited_papers") or "[]")
        # Coerce NULLs from legacy rows to defaults
        item["turn_index"] = item.get("turn_index") or 0
        item["session_id"] = item.get("session_id") or None
        return item


db = SQLiteStore()
