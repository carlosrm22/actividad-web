from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .db import ActivityDB
from .detector import ActiveWindow, WindowDetector
from .idle import IdleDetector


@dataclass
class _CurrentSession:
    app: str
    title: str
    source: str
    start_ts: int


class ActivityTracker:
    def __init__(
        self,
        db: ActivityDB,
        detector: WindowDetector,
        interval_seconds: float = 2.0,
        idle_detector: IdleDetector | None = None,
        idle_enabled: bool = True,
        idle_threshold_seconds: int = 60,
    ) -> None:
        self.db = db
        self.detector = detector
        self.interval_seconds = max(0.5, float(interval_seconds))
        self.idle_detector = idle_detector
        self.idle_enabled = bool(idle_enabled)
        self.idle_threshold_seconds = max(1, int(idle_threshold_seconds))

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._current: _CurrentSession | None = None
        self._paused = False
        self._last_idle_seconds: int | None = None
        self._last_idle_backend = "none"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="activity-tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

        with self._lock:
            self._flush_locked(int(time.time()))

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = bool(paused)
            if self._paused:
                self._flush_locked(int(time.time()))

    def status(self) -> dict[str, object]:
        with self._lock:
            current = self._current
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "paused": self._paused,
                "interval_seconds": self.interval_seconds,
                "current": {
                    "app": current.app,
                    "title": current.title,
                    "source": current.source,
                    "start_ts": current.start_ts,
                }
                if current
                else None,
                "idle": {
                    "enabled": self.idle_enabled,
                    "threshold_seconds": self.idle_threshold_seconds,
                    "last_idle_seconds": self._last_idle_seconds,
                    "last_backend": self._last_idle_backend,
                },
            }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            now_ts = int(time.time())
            detected = self.detector.detect()
            idle_seconds, idle_backend = self._detect_idle()

            with self._lock:
                self._last_idle_seconds = idle_seconds
                self._last_idle_backend = idle_backend

                if self._paused:
                    self._flush_locked(now_ts)
                else:
                    if idle_seconds is not None and idle_seconds >= self.idle_threshold_seconds:
                        detected = ActiveWindow(app="Inactivo", title="", source="idle")
                    self._ingest_locked(now_ts, detected)

            self._stop_event.wait(self.interval_seconds)

    def _detect_idle(self) -> tuple[int | None, str]:
        if not self.idle_enabled or self.idle_detector is None:
            return (None, "disabled")

        idle_seconds = self.idle_detector.get_idle_seconds()
        caps = self.idle_detector.capabilities()
        backend = str(caps.get("last_backend") or "none")
        return (idle_seconds, backend)

    def _ingest_locked(self, now_ts: int, detected: ActiveWindow | None) -> None:
        if detected is None:
            self._flush_locked(now_ts)
            return

        # Evita registrar "Proceso" sin título (suele ser metadata faltante/transitoria).
        if self._is_unidentified(detected):
            if self._current is None:
                return
            # Si ya tenemos una sesión útil abierta, no la cortamos por este ruido.
            return

        if self._current is None:
            self._current = _CurrentSession(
                app=detected.app,
                title=detected.title,
                source=detected.source,
                start_ts=now_ts,
            )
            return

        unchanged = (
            self._current.app == detected.app
            and self._current.title == detected.title
            and self._current.source == detected.source
        )
        if unchanged:
            return

        self._flush_locked(now_ts)
        self._current = _CurrentSession(
            app=detected.app,
            title=detected.title,
            source=detected.source,
            start_ts=now_ts,
        )

    def _flush_locked(self, end_ts: int) -> None:
        if self._current is None:
            return

        self.db.insert_session(
            start_ts=self._current.start_ts,
            end_ts=end_ts,
            app=self._current.app,
            title=self._current.title,
            source=self._current.source,
        )
        self._current = None

    def _is_unidentified(self, detected: ActiveWindow) -> bool:
        app = (detected.app or "").strip().casefold()
        title = (detected.title or "").strip()
        return app in {"proceso", "desconocido"} and not title
