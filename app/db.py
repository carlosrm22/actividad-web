from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class SessionRow:
    id: int
    start_ts: int
    end_ts: int
    app: str
    title: str
    source: str


class ActivityDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_ts INTEGER NOT NULL,
                    end_ts INTEGER NOT NULL,
                    app TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_start_end ON sessions(start_ts, end_ts)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_app ON sessions(app)")

    def insert_session(
        self,
        start_ts: int,
        end_ts: int,
        app: str,
        title: str,
        source: str,
    ) -> None:
        if end_ts <= start_ts:
            return
        app = self._normalize_app_label(app)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sessions (start_ts, end_ts, app, title, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (start_ts, end_ts, app, title, source),
            )

    def recent_sessions(self, limit: int = 100) -> list[SessionRow]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, start_ts, end_ts, app, title, source
                FROM sessions
                ORDER BY end_ts DESC
                LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            ).fetchall()

        return [
            SessionRow(
                id=row["id"],
                start_ts=row["start_ts"],
                end_ts=row["end_ts"],
                app=self._normalize_app_label(row["app"]),
                title=row["title"],
                source=row["source"],
            )
            for row in rows
        ]

    def overlapping_sessions(self, start_ts: int, end_ts: int) -> list[SessionRow]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, start_ts, end_ts, app, title, source
                FROM sessions
                WHERE end_ts > ? AND start_ts < ?
                ORDER BY start_ts ASC
                """,
                (start_ts, end_ts),
            ).fetchall()

        return [
            SessionRow(
                id=row["id"],
                start_ts=row["start_ts"],
                end_ts=row["end_ts"],
                app=self._normalize_app_label(row["app"]),
                title=row["title"],
                source=row["source"],
            )
            for row in rows
        ]

    def _normalize_app_label(self, app: str | None) -> str:
        value = (app or "").strip()
        if not value:
            return "Proceso"
        if value.casefold() == "desconocido":
            return "Proceso"
        return value
