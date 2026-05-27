from __future__ import annotations

import threading
import time

from app.db.sqlite import db


class ParseQueue:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def enqueue_parse(self, paper_id: str) -> int:
        db.update_parse_status(paper_id, "queued", 0, "等待后台解析")
        task_id = db.enqueue_parse_task(paper_id)
        self.start()
        return task_id

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._recover_stuck_tasks()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="paper-parse-worker", daemon=True)
            self._thread.start()

    def _recover_stuck_tasks(self) -> None:
        """Reset tasks left in 'running' state after an unclean shutdown."""
        import sqlite3
        try:
            conn = sqlite3.connect(str(db.db_path))
            conn.execute(
                "UPDATE parse_tasks SET status = 'queued', locked_at = NULL "
                "WHERE status = 'running' AND task_type = 'parse_paper'"
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            task = db.claim_next_parse_task()
            if not task:
                time.sleep(1.0)
                continue

            task_id = int(task["id"])
            paper_id = task["paper_id"]
            try:
                from app.agents.paper_ingestion import paper_ingestion_agent

                paper_ingestion_agent.process_paper(paper_id)
                db.complete_parse_task(task_id)
            except Exception as exc:
                db.fail_parse_task(task_id, str(exc))
                db.update_parse_status(paper_id, "failed", 0, "解析失败", str(exc))


parse_queue = ParseQueue()

