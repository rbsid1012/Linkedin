"""SQLite log: dedupe jobs, enforce daily limits, remember answers you taught it."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    title TEXT, company TEXT, url TEXT,
    profile TEXT, resume TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS unanswered (
    question TEXT, job_id TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS learned (
    question TEXT PRIMARY KEY, answer TEXT NOT NULL
);
"""

# statuses: matched (scan only) | rehearsed | applied | skipped | needs_manual | failed


class Tracker:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def seen(self, job_id: str) -> bool:
        row = self.db.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        # 'matched'/'rehearsed' are non-final: they must not block a later real apply.
        return bool(row) and row[0] not in ("matched", "rehearsed")

    def record(self, job_id, status, *, title="", company="", url="", profile="", resume="", detail=""):
        self.db.execute(
            "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, title, company, url, profile, str(resume), status, detail, time.time()),
        )
        self.db.commit()

    def applied_today(self) -> int:
        since = time.time() - 24 * 3600
        return self.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='applied' AND ts>=?", (since,)
        ).fetchone()[0]

    def log_unanswered(self, question: str, job_id: str):
        self.db.execute("INSERT INTO unanswered VALUES (?,?,?)", (question, job_id, time.time()))
        self.db.commit()

    def unanswered_summary(self) -> list[tuple[str, int]]:
        return self.db.execute(
            "SELECT question, COUNT(*) c FROM unanswered GROUP BY question ORDER BY c DESC"
        ).fetchall()

    def learn(self, question_norm: str, answer: str):
        self.db.execute("INSERT OR REPLACE INTO learned VALUES (?,?)", (question_norm, answer))
        self.db.commit()

    def learned(self, question_norm: str) -> str | None:
        row = self.db.execute("SELECT answer FROM learned WHERE question=?", (question_norm,)).fetchone()
        return row[0] if row else None

    def stats(self) -> list[tuple[str, str, int]]:
        return self.db.execute(
            "SELECT profile, status, COUNT(*) FROM jobs GROUP BY profile, status ORDER BY profile, status"
        ).fetchall()

    def close(self):
        self.db.close()
