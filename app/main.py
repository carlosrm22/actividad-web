from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
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


def _seconds_to_human(total_seconds: int) -> str:
    hours, rem = divmod(max(0, int(total_seconds)), 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _resolve_day_bounds(date_text: str | None) -> tuple[datetime, datetime]:
    now = datetime.now().astimezone()
    tz = now.tzinfo

    if date_text:
        try:
            selected = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="La fecha debe estar en formato YYYY-MM-DD") from exc
    else:
        selected = now.date()

    start = datetime(selected.year, selected.month, selected.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


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
    total_seconds = 0

    for segment in segments:
        duration = segment.end_ts - segment.start_ts
        total_seconds += duration
        by_app[segment.app] = by_app.get(segment.app, 0) + duration

        cur_dt = datetime.fromtimestamp(segment.start_ts, tz=tzinfo)
        end_dt = datetime.fromtimestamp(segment.end_ts, tz=tzinfo)
        while cur_dt < end_dt:
            next_hour = cur_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            chunk_end = min(end_dt, next_hour)
            by_hour[cur_dt.hour] += int((chunk_end - cur_dt).total_seconds())
            cur_dt = chunk_end

    top_apps = sorted(by_app.items(), key=lambda item: item[1], reverse=True)
    top_app_payload = []
    for app, seconds in top_apps[:10]:
        percentage = (seconds / total_seconds * 100.0) if total_seconds else 0.0
        top_app_payload.append(
            {
                "app": app,
                "seconds": seconds,
                "human": _seconds_to_human(seconds),
                "percentage": round(percentage, 1),
            }
        )

    return {
        "range_start_ts": range_start,
        "range_end_ts": range_end,
        "total_seconds": total_seconds,
        "total_human": _seconds_to_human(total_seconds),
        "distinct_apps": len(by_app),
        "top_apps": top_app_payload,
        "by_hour_seconds": by_hour,
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
    def overview(date: str | None = Query(default=None, description="Formato YYYY-MM-DD")) -> dict[str, object]:
        day_start, day_end = _resolve_day_bounds(date)
        range_start = int(day_start.timestamp())
        range_end = int(day_end.timestamp())

        rows = db.overlapping_sessions(range_start, range_end)
        segments: list[Segment] = []
        for row in rows:
            clipped = _clip_segment(row, range_start, range_end)
            if clipped:
                segments.append(clipped)

        tracker_status = tracker.status()
        now_ts = int(time.time())
        current = tracker_status.get("current")
        if current and isinstance(current, dict):
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

        payload = _build_overview(segments, range_start, range_end, day_start.tzinfo)
        payload["date"] = day_start.date().isoformat()
        payload["updated_at_ts"] = now_ts
        return payload

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
