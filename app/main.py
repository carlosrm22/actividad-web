from __future__ import annotations

import csv
import io
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import ActivityDB, PrivacyRuleRow, SessionRow
from .detector import ActiveWindow, WindowDetector
from .idle import IdleDetector
from .privacy import PrivacyFilter, PrivacyRule
from .tracker import ActivityTracker


@dataclass
class Segment:
    app: str
    title: str
    source: str
    start_ts: int
    end_ts: int


@dataclass
class RangeSpec:
    mode: str
    start: datetime
    end: datetime
    anchor_date: date_cls


class CategoryUpdate(BaseModel):
    category: str


class PauseUpdate(BaseModel):
    paused: bool


class PrivacyRuleCreate(BaseModel):
    scope: str
    match_mode: str
    pattern: str
    enabled: bool = True


class PrivacyRuleStateUpdate(BaseModel):
    enabled: bool


class BackupSession(BaseModel):
    start_ts: int
    end_ts: int
    app: str
    title: str = ""
    source: str = "restore"


class BackupCategory(BaseModel):
    app: str
    category: str = "Sin categoría"


class BackupPrivacyRule(BaseModel):
    scope: str
    match_mode: str
    pattern: str
    enabled: bool = True


class BackupRestoreRequest(BaseModel):
    sessions: list[BackupSession] = Field(default_factory=list)
    categories: list[BackupCategory] = Field(default_factory=list)
    privacy_rules: list[BackupPrivacyRule] = Field(default_factory=list)


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _seconds_to_human(total_seconds: int) -> str:
    hours, rem = divmod(max(0, int(total_seconds)), 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _parse_iso_date(raw: str, field_name: str) -> date_cls:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} debe estar en formato YYYY-MM-DD") from exc


def _resolve_range(
    mode: str,
    anchor_date_raw: str | None,
    start_date_raw: str | None,
    end_date_raw: str | None,
) -> RangeSpec:
    now = datetime.now().astimezone()
    tz = now.tzinfo
    mode_norm = (mode or "day").strip().lower()
    if mode_norm not in {"day", "week", "month", "custom"}:
        raise HTTPException(status_code=400, detail="mode debe ser day, week, month o custom")

    anchor = _parse_iso_date(anchor_date_raw, "anchor_date") if anchor_date_raw else now.date()

    if mode_norm == "day":
        start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=tz)
        end = start + timedelta(days=1)
        return RangeSpec(mode=mode_norm, start=start, end=end, anchor_date=anchor)

    if mode_norm == "week":
        start_date = anchor - timedelta(days=anchor.weekday())
        start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
        end = start + timedelta(days=7)
        return RangeSpec(mode=mode_norm, start=start, end=end, anchor_date=anchor)

    if mode_norm == "month":
        start_date = anchor.replace(day=1)
        if start_date.month == 12:
            next_month = start_date.replace(year=start_date.year + 1, month=1, day=1)
        else:
            next_month = start_date.replace(month=start_date.month + 1, day=1)
        start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
        end = datetime(next_month.year, next_month.month, next_month.day, tzinfo=tz)
        return RangeSpec(mode=mode_norm, start=start, end=end, anchor_date=anchor)

    if not start_date_raw or not end_date_raw:
        raise HTTPException(status_code=400, detail="Para mode=custom debes enviar start_date y end_date")

    start_date = _parse_iso_date(start_date_raw, "start_date")
    end_date = _parse_iso_date(end_date_raw, "end_date")
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date no puede ser menor que start_date")

    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
    end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=tz) + timedelta(days=1)
    day_span = (end_date - start_date).days + 1
    if day_span > 365:
        raise HTTPException(status_code=400, detail="Rango custom demasiado grande (máximo 365 días)")
    return RangeSpec(mode=mode_norm, start=start, end=end, anchor_date=anchor)


def _clip_segment(row: SessionRow, range_start: int, range_end: int) -> Segment | None:
    start_ts = max(row.start_ts, range_start)
    end_ts = min(row.end_ts, range_end)
    if end_ts <= start_ts:
        return None

    return Segment(
        app=row.app,
        title=row.title,
        source=row.source,
        start_ts=start_ts,
        end_ts=end_ts,
    )


def _is_afk_label(app_label: str) -> bool:
    return app_label.strip().casefold() in {"inactivo", "idle", "afk"}


def _category_for_app(app_label: str, category_map: dict[str, str]) -> str:
    app = (app_label or "").strip()
    if _is_afk_label(app):
        return "Inactividad"
    if app.casefold() in {"proceso", "desconocido"}:
        return "No identificado"
    return category_map.get(app, "Sin categoría")


def _sorted_payload(by_key: dict[str, int], total_seconds: int) -> list[dict[str, object]]:
    rows = sorted(by_key.items(), key=lambda item: item[1], reverse=True)
    payload: list[dict[str, object]] = []
    for key, seconds in rows:
        percentage = (seconds / total_seconds * 100.0) if total_seconds else 0.0
        payload.append(
            {
                "app": key,
                "seconds": seconds,
                "human": _seconds_to_human(seconds),
                "percentage": round(percentage, 1),
            }
        )
    return payload


def _build_overview(
    segments: list[Segment],
    range_start: int,
    range_end: int,
    tzinfo,
    category_map: dict[str, str],
    group_by: str,
) -> dict[str, object]:
    by_group: dict[str, int] = {}
    by_app: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_hour = [0] * 24
    by_day: dict[str, int] = {}
    total_seconds = 0
    active_seconds = 0
    afk_seconds = 0
    unattributed_seconds = 0

    for segment in segments:
        duration = segment.end_ts - segment.start_ts
        total_seconds += duration

        app_label = (segment.app or "").strip()
        title = (segment.title or "").strip()
        is_afk = _is_afk_label(app_label)
        if is_afk:
            afk_seconds += duration
        else:
            active_seconds += duration

        is_unattributed = app_label.casefold() in {"proceso", "desconocido"} and not title
        if is_unattributed:
            unattributed_seconds += duration
        else:
            by_app[app_label] = by_app.get(app_label, 0) + duration
            category_label = _category_for_app(app_label, category_map)
            by_category[category_label] = by_category.get(category_label, 0) + duration
            if group_by == "category":
                by_group[category_label] = by_group.get(category_label, 0) + duration
            else:
                by_group[app_label] = by_group.get(app_label, 0) + duration

        cur_dt = datetime.fromtimestamp(segment.start_ts, tz=tzinfo)
        end_dt = datetime.fromtimestamp(segment.end_ts, tz=tzinfo)
        while cur_dt < end_dt:
            next_hour = cur_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            next_day = cur_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            chunk_end = min(end_dt, next_hour, next_day)
            chunk_seconds = int((chunk_end - cur_dt).total_seconds())
            by_hour[cur_dt.hour] += chunk_seconds
            day_key = cur_dt.date().isoformat()
            by_day[day_key] = by_day.get(day_key, 0) + chunk_seconds
            cur_dt = chunk_end

    by_day_payload = [
        {
            "date": day,
            "seconds": seconds,
            "human": _seconds_to_human(seconds),
        }
        for day, seconds in sorted(by_day.items())
    ]

    top_payload = _sorted_payload(by_group, total_seconds)[:50]

    return {
        "range_start_ts": range_start,
        "range_end_ts": range_end,
        "group_by": group_by,
        "total_seconds": total_seconds,
        "total_human": _seconds_to_human(total_seconds),
        "active_seconds": active_seconds,
        "active_human": _seconds_to_human(active_seconds),
        "afk_seconds": afk_seconds,
        "afk_human": _seconds_to_human(afk_seconds),
        "unattributed_seconds": unattributed_seconds,
        "unattributed_human": _seconds_to_human(unattributed_seconds),
        "distinct_apps": len(by_app),
        "distinct_categories": len(by_category),
        "top_apps": top_payload,
        "by_app": _sorted_payload(by_app, total_seconds),
        "by_category": _sorted_payload(by_category, total_seconds),
        "by_hour_seconds": by_hour,
        "by_day": by_day_payload,
    }


def _segment_to_item(segment: Segment, tzinfo) -> dict[str, object]:
    duration = max(0, segment.end_ts - segment.start_ts)
    return {
        "start_ts": segment.start_ts,
        "end_ts": segment.end_ts,
        "start_iso": datetime.fromtimestamp(segment.start_ts, tz=tzinfo).isoformat(),
        "end_iso": datetime.fromtimestamp(segment.end_ts, tz=tzinfo).isoformat(),
        "duration_seconds": duration,
        "duration_human": _seconds_to_human(duration),
        "app": segment.app,
        "title": segment.title,
        "source": segment.source,
    }


def _privacy_row_payload(row: PrivacyRuleRow) -> dict[str, object]:
    return {
        "id": row.id,
        "scope": row.scope,
        "match_mode": row.match_mode,
        "pattern": row.pattern,
        "enabled": row.enabled,
        "updated_ts": row.updated_ts,
    }


def create_app() -> FastAPI:
    db_path = os.getenv("ACTIVIDAD_DB_PATH", "data/actividad.db")
    interval_seconds = float(os.getenv("ACTIVIDAD_INTERVAL_SECONDS", "2"))

    idle_enabled = _parse_bool(os.getenv("ACTIVIDAD_IDLE_ENABLED"), True)
    idle_threshold_seconds = int(os.getenv("ACTIVIDAD_IDLE_THRESHOLD_SECONDS", "60"))

    db = ActivityDB(db_path)
    detector = WindowDetector()
    idle_detector = IdleDetector(enabled=idle_enabled)
    privacy_filter = PrivacyFilter(rules=[])
    tracker = ActivityTracker(
        db=db,
        detector=detector,
        interval_seconds=interval_seconds,
        idle_detector=idle_detector,
        idle_enabled=idle_enabled,
        idle_threshold_seconds=idle_threshold_seconds,
        privacy_filter=privacy_filter,
    )

    def refresh_privacy_rules() -> list[PrivacyRuleRow]:
        rows = db.list_privacy_rules()
        privacy_filter.update_rules(
            [
                PrivacyRule(
                    id=row.id,
                    scope=row.scope,
                    match_mode=row.match_mode,
                    pattern=row.pattern,
                    enabled=row.enabled,
                    updated_ts=row.updated_ts,
                )
                for row in rows
            ]
        )
        return rows

    def collect_segments(spec: RangeSpec) -> tuple[list[Segment], int]:
        range_start = int(spec.start.timestamp())
        range_end = int(spec.end.timestamp())

        rows = db.overlapping_sessions(range_start, range_end)
        segments: list[Segment] = []
        for row in rows:
            clipped = _clip_segment(row, range_start, range_end)
            if clipped:
                segments.append(clipped)

        tracker_status = tracker.status()
        now_ts = int(time.time())
        current = tracker_status.get("current")
        if current and isinstance(current, dict) and (range_start <= now_ts < range_end):
            current_app = str(current.get("app", "Desconocido"))
            current_title = str(current.get("title", ""))
            if not privacy_filter.is_excluded(app=current_app, title=current_title):
                synthetic = SessionRow(
                    id=-1,
                    start_ts=int(current["start_ts"]),
                    end_ts=now_ts,
                    app=current_app,
                    title=current_title,
                    source=str(current.get("source", "")),
                )
                clipped = _clip_segment(synthetic, range_start, range_end)
                if clipped:
                    segments.append(clipped)

        return segments, now_ts

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init()
        refresh_privacy_rules()
        tracker.start()
        try:
            yield
        finally:
            tracker.stop()

    app = FastAPI(
        title="Actividad Web",
        description="Monitor local de actividad tipo ActivityWatch, en español.",
        version="0.3.0",
        lifespan=lifespan,
    )

    app.state.db = db
    app.state.detector = detector
    app.state.idle_detector = idle_detector
    app.state.tracker = tracker
    app.state.privacy_filter = privacy_filter

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        caps = detector.capabilities()
        idle_caps = idle_detector.capabilities()
        tracker_status = tracker.status()
        privacy_rows = db.list_privacy_rules()
        privacy_stats = privacy_filter.stats()

        notes: list[str] = []
        session_type = str(caps.get("session_type", "unknown"))
        can_x11 = bool(caps.get("can_detect_x11"))
        can_wayland_native = bool(caps.get("can_detect_wayland_native"))
        preferred_backend = str(caps.get("preferred_backend", "none"))
        kwin_dbus_enabled = bool(caps.get("kwin_dbus_enabled"))
        missing_x11_tools = [tool for tool in ("xdotool", "xprop") if not caps.get(tool)]

        if session_type == "wayland":
            if preferred_backend == "hyprctl":
                notes.append("Wayland detectado: usando backend nativo (hyprctl).")
            elif preferred_backend == "kdotool":
                notes.append("Wayland detectado: usando backend nativo KDE (kdotool).")
            elif preferred_backend == "kwin_dbus":
                notes.append("Wayland detectado: usando backend nativo KDE (KWin DBus).")
            elif can_x11:
                notes.append(
                    "Wayland detectado: usando fallback XWayland (xdotool/xprop). "
                    "Las apps nativas Wayland pueden no aparecer siempre."
                )
                if not kwin_dbus_enabled:
                    notes.append(
                        "Backend KDE (KWin DBus) desactivado por defecto para evitar interferencias del cursor. "
                        "Si quieres probarlo, usa ACTIVIDAD_ENABLE_KWIN_DBUS=1."
                    )
            else:
                notes.append(
                    "Wayland detectado sin backend compatible. "
                    "Instala hyprctl (Hyprland) o usa una sesión X11."
                )
        elif session_type == "x11":
            if can_x11:
                notes.append("X11 detectado: detección completa con xdotool/xprop.")
            else:
                notes.append(
                    "X11 detectado, pero faltan utilidades para detectar ventana activa: instala "
                    + ", ".join(missing_x11_tools)
                    + "."
                )
        elif not can_wayland_native and not can_x11:
            notes.append(
                "No se pudo identificar el tipo de sesión y faltan backends de detección. "
                "Instala xdotool y xprop para X11."
            )

        idle_health = {
            "enabled": idle_enabled,
            "threshold_seconds": idle_threshold_seconds,
            "available": bool(idle_caps.get("available")),
            "backends": idle_caps.get("backends", []),
            "preferred_backend": idle_caps.get("preferred_backend", "none"),
            "last_backend": tracker_status.get("idle", {}).get("last_backend")
            or idle_caps.get("last_backend", "none"),
            "last_idle_seconds": tracker_status.get("idle", {}).get("last_idle_seconds"),
            "last_checked_ts": idle_caps.get("last_checked_ts"),
        }

        if idle_enabled and not idle_health["available"]:
            notes.append(
                "Detección de inactividad no disponible. Instala xprintidle (o xssstate en Fedora) "
                "o habilita DBus de org.freedesktop.ScreenSaver."
            )

        return {
            "ok": True,
            "db_path": db_path,
            "capabilities": caps,
            "idle": idle_health,
            "privacy": {
                "rules": [_privacy_row_payload(row) for row in privacy_rows],
                "rules_count": len(privacy_rows),
                **privacy_stats,
            },
            "tracker": tracker_status,
            "notes": notes,
            "timestamp": int(time.time()),
        }

    @app.get("/api/overview")
    def overview(
        mode: str = Query(default="day", description="day | week | month | custom"),
        anchor_date: str | None = Query(default=None, description="Fecha base YYYY-MM-DD"),
        start_date: str | None = Query(default=None, description="Solo custom: YYYY-MM-DD"),
        end_date: str | None = Query(default=None, description="Solo custom: YYYY-MM-DD (inclusive)"),
        date: str | None = Query(default=None, description="Compatibilidad legacy (equivale a anchor_date)"),
        group_by: str = Query(default="app", description="app | category"),
    ) -> dict[str, object]:
        if date and not anchor_date:
            anchor_date = date

        group_by_norm = (group_by or "app").strip().lower()
        if group_by_norm not in {"app", "category"}:
            raise HTTPException(status_code=400, detail="group_by debe ser app o category")

        spec = _resolve_range(mode=mode, anchor_date_raw=anchor_date, start_date_raw=start_date, end_date_raw=end_date)
        range_start = int(spec.start.timestamp())
        range_end = int(spec.end.timestamp())

        segments, now_ts = collect_segments(spec)
        category_map = db.get_app_categories()
        payload = _build_overview(
            segments,
            range_start,
            range_end,
            spec.start.tzinfo,
            category_map=category_map,
            group_by=group_by_norm,
        )
        payload["mode"] = spec.mode
        payload["date"] = spec.start.date().isoformat()
        payload["anchor_date"] = spec.anchor_date.isoformat()
        payload["range_start_date"] = spec.start.date().isoformat()
        payload["range_end_date_exclusive"] = spec.end.date().isoformat()
        payload["range_end_date_inclusive"] = (spec.end - timedelta(days=1)).date().isoformat()
        payload["days_count"] = max(1, (spec.end.date() - spec.start.date()).days)
        payload["updated_at_ts"] = now_ts
        return payload

    @app.get("/api/ranking")
    def ranking(
        mode: str = Query(default="day", description="day | week | month | custom"),
        anchor_date: str | None = Query(default=None, description="Fecha base YYYY-MM-DD"),
        start_date: str | None = Query(default=None, description="Solo custom: YYYY-MM-DD"),
        end_date: str | None = Query(default=None, description="Solo custom: YYYY-MM-DD (inclusive)"),
        date: str | None = Query(default=None, description="Compatibilidad legacy (equivale a anchor_date)"),
        group_by: str = Query(default="app", description="app | category"),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, object]:
        base = overview(
            mode=mode,
            anchor_date=anchor_date,
            start_date=start_date,
            end_date=end_date,
            date=date,
            group_by=group_by,
        )
        return {
            "mode": base["mode"],
            "group_by": base["group_by"],
            "range_start_date": base["range_start_date"],
            "range_end_date_inclusive": base["range_end_date_inclusive"],
            "total_human": base["total_human"],
            "active_human": base.get("active_human", base["total_human"]),
            "afk_human": base.get("afk_human", "0s"),
            "unattributed_human": base["unattributed_human"],
            "items": base["top_apps"][:limit],
            "count": min(limit, len(base["top_apps"])),
            "updated_at_ts": base["updated_at_ts"],
        }

    @app.get("/api/recent")
    def recent(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, object]:
        rows = db.recent_sessions(limit=limit)
        items: list[dict[str, object]] = []
        for row in rows:
            duration = max(0, row.end_ts - row.start_ts)
            items.append(
                {
                    "id": row.id,
                    "start_ts": row.start_ts,
                    "end_ts": row.end_ts,
                    "start_iso": datetime.fromtimestamp(row.start_ts).astimezone().isoformat(),
                    "end_iso": datetime.fromtimestamp(row.end_ts).astimezone().isoformat(),
                    "duration_seconds": duration,
                    "duration_human": _seconds_to_human(duration),
                    "app": row.app,
                    "title": row.title,
                    "source": row.source,
                }
            )

        return {
            "items": items,
            "count": len(items),
        }

    @app.get("/api/windows")
    def windows(limit: int = Query(default=200, ge=1, le=2000)) -> dict[str, object]:
        active = detector.detect()
        open_windows = detector.list_windows(limit=limit)

        by_app: dict[str, int] = {}
        items: list[dict[str, object]] = []
        for win in open_windows:
            is_private = privacy_filter.is_excluded(app=win.app, title=win.title)
            app_name = "Privado" if is_private else win.app
            title = "Oculto por regla de privacidad" if is_private else win.title
            source = "privacy" if is_private else win.source
            by_app[app_name] = by_app.get(app_name, 0) + 1
            items.append(
                {
                    "app": app_name,
                    "title": title,
                    "source": source,
                    "pid": None if is_private else win.pid,
                    "window_id": None if is_private else win.window_id,
                    "private": is_private,
                }
            )

        app_counts = [
            {"app": app_name, "windows": count}
            for app_name, count in sorted(by_app.items(), key=lambda item: item[1], reverse=True)
        ]

        active_payload: dict[str, object] | None = None
        if active is not None:
            active_private = privacy_filter.is_excluded(app=active.app, title=active.title)
            active_payload = {
                "app": "Privado" if active_private else active.app,
                "title": "Oculto por regla de privacidad" if active_private else active.title,
                "source": "privacy" if active_private else active.source,
                "pid": None if active_private else active.pid,
                "window_id": None if active_private else active.window_id,
                "private": active_private,
            }

        return {
            "count": len(items),
            "distinct_apps": len(by_app),
            "app_counts": app_counts,
            "items": items,
            "active": active_payload,
        }

    @app.get("/api/categories")
    def categories() -> dict[str, object]:
        mapping = db.get_app_categories()
        items = [{"app": app_name, "category": category} for app_name, category in sorted(mapping.items())]
        return {"count": len(items), "items": items}

    @app.put("/api/categories/{app_name}")
    def upsert_category(app_name: str, payload: CategoryUpdate) -> dict[str, object]:
        raw_app = (app_name or "").strip()
        if not raw_app:
            raise HTTPException(status_code=400, detail="app_name no puede ser vacío")

        raw_category = (payload.category or "").strip()
        app_saved, category_saved = db.set_app_category(raw_app, raw_category)
        return {"ok": True, "app": app_saved, "category": category_saved}

    @app.delete("/api/categories/{app_name}")
    def remove_category(app_name: str) -> dict[str, object]:
        raw_app = (app_name or "").strip()
        if not raw_app:
            raise HTTPException(status_code=400, detail="app_name no puede ser vacío")
        removed = db.delete_app_category(raw_app)
        return {"ok": True, "removed": removed}

    @app.get("/api/privacy/rules")
    def list_privacy_rules() -> dict[str, object]:
        rows = db.list_privacy_rules()
        return {
            "count": len(rows),
            "items": [_privacy_row_payload(row) for row in rows],
        }

    @app.post("/api/privacy/rules")
    def create_privacy_rule(payload: PrivacyRuleCreate) -> dict[str, object]:
        try:
            row = db.upsert_privacy_rule(
                scope=payload.scope,
                match_mode=payload.match_mode,
                pattern=payload.pattern,
                enabled=payload.enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        refresh_privacy_rules()
        return {"ok": True, "item": _privacy_row_payload(row)}

    @app.patch("/api/privacy/rules/{rule_id}")
    def patch_privacy_rule(rule_id: int, payload: PrivacyRuleStateUpdate) -> dict[str, object]:
        row = db.set_privacy_rule_enabled(rule_id=rule_id, enabled=payload.enabled)
        if row is None:
            raise HTTPException(status_code=404, detail="Regla no encontrada")
        refresh_privacy_rules()
        return {"ok": True, "item": _privacy_row_payload(row)}

    @app.delete("/api/privacy/rules/{rule_id}")
    def remove_privacy_rule(rule_id: int) -> dict[str, object]:
        removed = db.delete_privacy_rule(rule_id=rule_id)
        refresh_privacy_rules()
        return {"ok": True, "removed": removed}

    @app.get("/api/export/sessions")
    def export_sessions(
        format: str = Query(default="json", description="json | csv"),
        mode: str = Query(default="day", description="day | week | month | custom"),
        anchor_date: str | None = Query(default=None, description="Fecha base YYYY-MM-DD"),
        start_date: str | None = Query(default=None, description="Solo custom: YYYY-MM-DD"),
        end_date: str | None = Query(default=None, description="Solo custom: YYYY-MM-DD (inclusive)"),
        date: str | None = Query(default=None, description="Compatibilidad legacy (equivale a anchor_date)"),
    ) -> Response:
        if date and not anchor_date:
            anchor_date = date

        fmt = (format or "json").strip().lower()
        if fmt not in {"json", "csv"}:
            raise HTTPException(status_code=400, detail="format debe ser json o csv")

        spec = _resolve_range(mode=mode, anchor_date_raw=anchor_date, start_date_raw=start_date, end_date_raw=end_date)
        segments, now_ts = collect_segments(spec)
        items = [_segment_to_item(segment, tzinfo=spec.start.tzinfo) for segment in sorted(segments, key=lambda x: x.start_ts)]

        if fmt == "json":
            return JSONResponse(
                {
                    "mode": spec.mode,
                    "range_start_date": spec.start.date().isoformat(),
                    "range_end_date_inclusive": (spec.end - timedelta(days=1)).date().isoformat(),
                    "count": len(items),
                    "items": items,
                    "exported_at_ts": now_ts,
                }
            )

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "start_iso",
                "end_iso",
                "duration_seconds",
                "duration_human",
                "app",
                "title",
                "source",
            ],
        )
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "start_iso": item["start_iso"],
                    "end_iso": item["end_iso"],
                    "duration_seconds": item["duration_seconds"],
                    "duration_human": item["duration_human"],
                    "app": item["app"],
                    "title": item["title"],
                    "source": item["source"],
                }
            )

        filename = f"actividad-{spec.mode}-{spec.start.date().isoformat()}.csv"
        return Response(
            content=output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/backup/export")
    def export_backup() -> Response:
        rows = db.all_sessions()
        category_map = db.get_app_categories()
        privacy_rows = db.list_privacy_rules()

        sessions_payload = [
            {
                "start_ts": row.start_ts,
                "end_ts": row.end_ts,
                "app": row.app,
                "title": row.title,
                "source": row.source,
            }
            for row in rows
        ]
        categories_payload = [
            {"app": app_name, "category": category}
            for app_name, category in sorted(category_map.items(), key=lambda item: item[0].casefold())
        ]
        privacy_payload = [_privacy_row_payload(row) for row in privacy_rows]

        now_ts = int(time.time())
        payload = {
            "schema_version": 1,
            "exported_at_ts": now_ts,
            "sessions": sessions_payload,
            "categories": categories_payload,
            "privacy_rules": privacy_payload,
        }
        filename = datetime.now().strftime("actividad-backup-%Y%m%d-%H%M%S.json")
        return JSONResponse(
            payload,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/backup/restore")
    def restore_backup(payload: BackupRestoreRequest, replace: bool = Query(default=False)) -> dict[str, object]:
        was_paused = bool(tracker.status().get("paused"))
        tracker.set_paused(True)

        inserted_sessions = 0
        saved_categories = 0
        saved_rules = 0
        skipped_rules = 0

        try:
            if replace:
                db.clear_sessions()
                db.clear_app_categories()
                db.clear_privacy_rules()

            session_rows: list[tuple[int, int, str, str, str]] = []
            for item in payload.sessions:
                if item.end_ts <= item.start_ts:
                    continue
                session_rows.append((item.start_ts, item.end_ts, item.app, item.title, item.source or "restore"))
            inserted_sessions = db.bulk_insert_sessions(session_rows)

            category_rows = [(item.app, item.category) for item in payload.categories if item.app.strip()]
            saved_categories = db.bulk_set_app_categories(category_rows)

            for rule in payload.privacy_rules:
                try:
                    db.upsert_privacy_rule(
                        scope=rule.scope,
                        match_mode=rule.match_mode,
                        pattern=rule.pattern,
                        enabled=rule.enabled,
                    )
                    saved_rules += 1
                except ValueError:
                    skipped_rules += 1

            refresh_privacy_rules()
        finally:
            if not was_paused:
                tracker.set_paused(False)

        return {
            "ok": True,
            "replace": replace,
            "inserted_sessions": inserted_sessions,
            "saved_categories": saved_categories,
            "saved_privacy_rules": saved_rules,
            "skipped_privacy_rules": skipped_rules,
        }

    @app.post("/api/control/pause")
    def pause_tracking() -> dict[str, object]:
        tracker.set_paused(True)
        return {"ok": True, "paused": True}

    @app.post("/api/control/resume")
    def resume_tracking() -> dict[str, object]:
        tracker.set_paused(False)
        return {"ok": True, "paused": False}

    @app.post("/api/control/state")
    def set_control_state(payload: PauseUpdate) -> dict[str, object]:
        tracker.set_paused(payload.paused)
        return {"ok": True, "paused": bool(payload.paused)}

    return app


app = create_app()
