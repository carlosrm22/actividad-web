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


@dataclass
class PrivacyRuleRow:
    id: int
    scope: str
    match_mode: str
    pattern: str
    enabled: bool
    updated_ts: int


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS privacy_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    match_mode TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_ts INTEGER NOT NULL,
                    CHECK(scope IN ('app', 'title')),
                    CHECK(match_mode IN ('contains', 'exact', 'regex'))
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_start_end ON sessions(start_ts, end_ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_app ON sessions(app)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_app_categories_category ON app_categories(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_privacy_rules_enabled ON privacy_rules(enabled)")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_privacy_rules_key ON privacy_rules(scope, match_mode, pattern)"
            )

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

    def bulk_insert_sessions(self, rows: list[tuple[int, int, str, str, str]]) -> int:
        if not rows:
            return 0

        normalized_rows: list[tuple[int, int, str, str, str]] = []
        for start_ts, end_ts, app, title, source in rows:
            if end_ts <= start_ts:
                continue
            normalized_rows.append(
                (
                    int(start_ts),
                    int(end_ts),
                    self._normalize_app_label(app),
                    str(title or ""),
                    str(source or ""),
                )
            )

        if not normalized_rows:
            return 0

        with self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO sessions (start_ts, end_ts, app, title, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                normalized_rows,
            )
        return len(normalized_rows)

    def recent_sessions(self, limit: int = 100) -> list[SessionRow]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, start_ts, end_ts, app, title, source
                FROM sessions
                ORDER BY end_ts DESC
                LIMIT ?
                """,
                (max(1, min(limit, 5000)),),
            ).fetchall()

        return [self._map_session_row(row) for row in rows]

    def all_sessions(self) -> list[SessionRow]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, start_ts, end_ts, app, title, source
                FROM sessions
                ORDER BY start_ts ASC
                """
            ).fetchall()
        return [self._map_session_row(row) for row in rows]

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

        return [self._map_session_row(row) for row in rows]

    def clear_sessions(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM sessions")
        return int(cur.rowcount or 0)

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

    def bulk_set_app_categories(self, entries: list[tuple[str, str]]) -> int:
        saved = 0
        for app, category in entries:
            app_norm = (app or "").strip()
            if not app_norm:
                continue
            self.set_app_category(app_norm, category)
            saved += 1
        return saved

    def delete_app_category(self, app: str) -> bool:
        app_norm = self._normalize_app_label(app)
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM app_categories WHERE app = ?", (app_norm,))
        return bool(cur.rowcount)

    def clear_app_categories(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM app_categories")
        return int(cur.rowcount or 0)

    def list_privacy_rules(self) -> list[PrivacyRuleRow]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, scope, match_mode, pattern, enabled, updated_ts
                FROM privacy_rules
                ORDER BY enabled DESC, updated_ts DESC, id DESC
                """
            ).fetchall()
        return [self._map_privacy_rule(row) for row in rows]

    def upsert_privacy_rule(
        self,
        scope: str,
        pattern: str,
        match_mode: str,
        enabled: bool = True,
    ) -> PrivacyRuleRow:
        scope_norm = self._normalize_rule_scope(scope)
        mode_norm = self._normalize_match_mode(match_mode)
        pattern_norm = self._normalize_rule_pattern(pattern)
        now_ts = int(time.time())
        enabled_int = 1 if enabled else 0

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO privacy_rules (scope, match_mode, pattern, enabled, updated_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope, match_mode, pattern) DO UPDATE SET
                    enabled=excluded.enabled,
                    updated_ts=excluded.updated_ts
                """,
                (scope_norm, mode_norm, pattern_norm, enabled_int, now_ts),
            )
            row = conn.execute(
                """
                SELECT id, scope, match_mode, pattern, enabled, updated_ts
                FROM privacy_rules
                WHERE scope = ? AND match_mode = ? AND pattern = ?
                LIMIT 1
                """,
                (scope_norm, mode_norm, pattern_norm),
            ).fetchone()

        if row is None:
            raise RuntimeError("No se pudo guardar la regla de privacidad")
        return self._map_privacy_rule(row)

    def set_privacy_rule_enabled(self, rule_id: int, enabled: bool) -> PrivacyRuleRow | None:
        now_ts = int(time.time())
        enabled_int = 1 if enabled else 0
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE privacy_rules
                SET enabled = ?, updated_ts = ?
                WHERE id = ?
                """,
                (enabled_int, now_ts, int(rule_id)),
            )
            row = conn.execute(
                """
                SELECT id, scope, match_mode, pattern, enabled, updated_ts
                FROM privacy_rules
                WHERE id = ?
                LIMIT 1
                """,
                (int(rule_id),),
            ).fetchone()

        if row is None:
            return None
        return self._map_privacy_rule(row)

    def delete_privacy_rule(self, rule_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM privacy_rules WHERE id = ?", (int(rule_id),))
        return bool(cur.rowcount)

    def clear_privacy_rules(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM privacy_rules")
        return int(cur.rowcount or 0)

    def _map_session_row(self, row: sqlite3.Row) -> SessionRow:
        return SessionRow(
            id=int(row["id"]),
            start_ts=int(row["start_ts"]),
            end_ts=int(row["end_ts"]),
            app=self._normalize_app_label(row["app"]),
            title=str(row["title"] or ""),
            source=str(row["source"] or ""),
        )

    def _map_privacy_rule(self, row: sqlite3.Row) -> PrivacyRuleRow:
        return PrivacyRuleRow(
            id=int(row["id"]),
            scope=str(row["scope"]),
            match_mode=str(row["match_mode"]),
            pattern=str(row["pattern"]),
            enabled=bool(int(row["enabled"])),
            updated_ts=int(row["updated_ts"]),
        )

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

    def _normalize_rule_scope(self, scope: str | None) -> str:
        value = (scope or "").strip().lower()
        if value not in {"app", "title"}:
            raise ValueError("scope debe ser app o title")
        return value

    def _normalize_match_mode(self, match_mode: str | None) -> str:
        value = (match_mode or "").strip().lower()
        if value not in {"contains", "exact", "regex"}:
            raise ValueError("match_mode debe ser contains, exact o regex")
        return value

    def _normalize_rule_pattern(self, pattern: str | None) -> str:
        value = (pattern or "").strip()
        if not value:
            raise ValueError("pattern no puede ser vacío")
        if len(value) > 200:
            value = value[:200].rstrip()
        return value
