from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import ActivityDB, SessionRow
from .detector import WindowDetector
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

    # Rango inclusivo por día para UX.
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
    end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=tz) + timedelta(days=1)
    day_span = (end_date - start_date).days + 1
    if day_span > 180:
        raise HTTPException(status_code=400, detail="Rango custom demasiado grande (máximo 180 días)")
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


def _build_overview(segments: list[Segment], range_start: int, range_end: int, tzinfo) -> dict[str, object]:
    by_app: dict[str, int] = {}
    by_hour = [0] * 24
    by_day: dict[str, int] = {}
    total_seconds = 0
    unattributed_seconds = 0

    for segment in segments:
        duration = segment.end_ts - segment.start_ts
        total_seconds += duration

        app_label = (segment.app or "").strip()
        title = (segment.title or "").strip()
        is_unattributed = app_label.casefold() in {"proceso", "desconocido"} and not title
        if is_unattributed:
            unattributed_seconds += duration
        else:
            by_app[app_label] = by_app.get(app_label, 0) + duration

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

    top_apps = sorted(by_app.items(), key=lambda item: item[1], reverse=True)
    top_app_payload = []
    for app, seconds in top_apps[:50]:
        percentage = (seconds / total_seconds * 100.0) if total_seconds else 0.0
        top_app_payload.append(
            {
                "app": app,
                "seconds": seconds,
                "human": _seconds_to_human(seconds),
                "percentage": round(percentage, 1),
            }
        )

    by_day_payload = [
        {
            "date": day,
            "seconds": seconds,
            "human": _seconds_to_human(seconds),
        }
        for day, seconds in sorted(by_day.items())
    ]

    return {
        "range_start_ts": range_start,
        "range_end_ts": range_end,
        "total_seconds": total_seconds,
        "total_human": _seconds_to_human(total_seconds),
        "unattributed_seconds": unattributed_seconds,
        "unattributed_human": _seconds_to_human(unattributed_seconds),
        "distinct_apps": len(by_app),
        "top_apps": top_app_payload,
        "by_hour_seconds": by_hour,
        "by_day": by_day_payload,
    }


def create_app() -> FastAPI:
    db_path = os.getenv("ACTIVIDAD_DB_PATH", "data/actividad.db")
    interval_seconds = float(os.getenv("ACTIVIDAD_INTERVAL_SECONDS", "2"))

    db = ActivityDB(db_path)
    detector = WindowDetector()
    tracker = ActivityTracker(db=db, detector=detector, interval_seconds=interval_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init()
        tracker.start()
        try:
            yield
        finally:
            tracker.stop()

    app = FastAPI(
        title="Actividad Web",
        description="Monitor local de actividad tipo ActivityWatch, en español.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.db = db
    app.state.detector = detector
    app.state.tracker = tracker

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        caps = detector.capabilities()
        tracker_status = tracker.status()

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

        return {
            "ok": True,
            "db_path": db_path,
            "capabilities": caps,
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
    ) -> dict[str, object]:
        if date and not anchor_date:
            anchor_date = date

        spec = _resolve_range(mode=mode, anchor_date_raw=anchor_date, start_date_raw=start_date, end_date_raw=end_date)
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
            current_start = int(current["start_ts"])
            synthetic = SessionRow(
                id=-1,
                start_ts=current_start,
                end_ts=now_ts,
                app=str(current.get("app", "Desconocido")),
                title=str(current.get("title", "")),
                source=str(current.get("source", "")),
            )
            clipped = _clip_segment(synthetic, range_start, range_end)
            if clipped:
                segments.append(clipped)

        payload = _build_overview(segments, range_start, range_end, spec.start.tzinfo)
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
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, object]:
        base = overview(
            mode=mode,
            anchor_date=anchor_date,
            start_date=start_date,
            end_date=end_date,
            date=date,
        )
        return {
            "mode": base["mode"],
            "range_start_date": base["range_start_date"],
            "range_end_date_inclusive": base["range_end_date_inclusive"],
            "total_human": base["total_human"],
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
            by_app[win.app] = by_app.get(win.app, 0) + 1
            items.append(
                {
                    "app": win.app,
                    "title": win.title,
                    "source": win.source,
                    "pid": win.pid,
                    "window_id": win.window_id,
                }
            )

        app_counts = [
            {"app": app_name, "windows": count}
            for app_name, count in sorted(by_app.items(), key=lambda item: item[1], reverse=True)
        ]

        return {
            "count": len(items),
            "distinct_apps": len(by_app),
            "app_counts": app_counts,
            "items": items,
            "active": {
                "app": active.app,
                "title": active.title,
                "source": active.source,
                "pid": active.pid,
                "window_id": active.window_id,
            }
            if active
            else None,
        }

    return app


app = create_app()
