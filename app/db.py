from __future__ import annotations

import sqlite3
import time
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
                """
                CREATE TABLE IF NOT EXISTS app_categories (
                    app TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    updated_ts INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_start_end ON sessions(start_ts, end_ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_app ON sessions(app)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_app_categories_category ON app_categories(category)")

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

    def get_app_categories(self) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT app, category
                FROM app_categories
                ORDER BY app COLLATE NOCASE ASC
                """
            ).fetchall()

        return {str(row["app"]): str(row["category"]) for row in rows}

    def set_app_category(self, app: str, category: str) -> tuple[str, str]:
        app_norm = self._normalize_app_label(app)
        category_norm = self._normalize_category_label(category)
        now_ts = int(time.time())
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO app_categories (app, category, updated_ts)
                VALUES (?, ?, ?)
                ON CONFLICT(app) DO UPDATE SET
                    category=excluded.category,
                    updated_ts=excluded.updated_ts
                """,
                (app_norm, category_norm, now_ts),
            )
        return (app_norm, category_norm)

    def delete_app_category(self, app: str) -> bool:
        app_norm = self._normalize_app_label(app)
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM app_categories WHERE app = ?", (app_norm,))
        return bool(cur.rowcount)

    def _normalize_app_label(self, app: str | None) -> str:
        value = (app or "").strip()
        if not value:
            return "Proceso"
        if value.casefold() == "desconocido":
            return "Proceso"
        return value

    def _normalize_category_label(self, category: str | None) -> str:
        value = (category or "").strip()
        if not value:
            return "Sin categoría"
        if len(value) > 64:
            value = value[:64].rstrip()
        return value
