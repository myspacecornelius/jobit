"""SQLite-backed tracker so we never double-apply and can report progress."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)


class ApplicationStatus(str, Enum):
    QUEUED = "queued"
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"
    NEEDS_MANUAL = "needs_manual"
    DRY_RUN = "dry_run"


@dataclass
class Application:
    job_url: str
    title: str
    company: str
    status: ApplicationStatus
    note: str = ""
    questions: Dict[str, str] = field(default_factory=dict)
    applied_at: datetime = field(default_factory=datetime.utcnow)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT DEFAULT '',
    questions_json TEXT DEFAULT '{}',
    applied_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_applications_url_active
  ON applications(job_url)
  WHERE status IN ('applied', 'dry_run');
"""


class ApplicationTracker:
    """Tiny SQLite wrapper. Connection opened per-call; cheap enough here."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def has_applied(self, job_url: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM applications "
                "WHERE job_url = ? AND status IN ('applied', 'dry_run') LIMIT 1",
                (job_url,),
            ).fetchone()
        return row is not None

    def record(self, app: Application) -> None:
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO applications "
                    "(job_url, title, company, status, note, questions_json, applied_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        app.job_url,
                        app.title,
                        app.company,
                        app.status.value,
                        app.note,
                        json.dumps(app.questions, ensure_ascii=False),
                        app.applied_at.isoformat(),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                logger.info("Already tracked: %s (%s)", app.title, app.job_url)

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT job_url, title, company, status, note, applied_at "
                "FROM applications ORDER BY applied_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def applied_in_last(self, seconds: int) -> int:
        """Count of real (applied) submissions within a sliding window."""
        cutoff_iso = (datetime.utcnow() - timedelta(seconds=seconds)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM applications "
                "WHERE status = 'applied' AND applied_at > ?",
                (cutoff_iso,),
            ).fetchone()
        return int(row["n"]) if row else 0
