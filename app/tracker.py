from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .db import ActivityDB
from .detector import ActiveWindow, WindowDetector
from .idle import IdleDetector
from .privacy import PrivacyFilter


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
        effective_idle_seconds: int = 8,
        sleep_gap_seconds: int = 90,
        privacy_filter: PrivacyFilter | None = None,
    ) -> None:
        self.db = db
        self.detector = detector
        self.interval_seconds = max(0.5, float(interval_seconds))
        self.idle_detector = idle_detector
        self.idle_enabled = bool(idle_enabled)
        self.idle_threshold_seconds = max(1, int(idle_threshold_seconds))
        self.effective_idle_seconds = max(1, int(effective_idle_seconds))
        self.sleep_gap_seconds = max(15, int(sleep_gap_seconds))
        self.privacy_filter = privacy_filter

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._current: _CurrentSession | None = None
        self._paused = False
        self._last_idle_seconds: int | None = None
        self._last_idle_backend = "none"
        self._excluded_matches = 0
        self._sleep_segments = 0
        self._last_wall_ts: float | None = None
        self._last_mono_ts: float | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._last_wall_ts = time.time()
        self._last_mono_ts = time.monotonic()
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
                    "effective_idle_seconds": self.effective_idle_seconds,
                    "last_idle_seconds": self._last_idle_seconds,
                    "last_backend": self._last_idle_backend,
                },
                "sleep": {
                    "gap_threshold_seconds": self.sleep_gap_seconds,
                    "segments": self._sleep_segments,
                },
                "privacy": {
                    "excluded_matches": self._excluded_matches,
                },
            }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            now_wall = time.time()
            now_mono = time.monotonic()
            now_ts = int(now_wall)
            detected = self.detector.detect()
            idle_seconds, idle_backend = self._detect_idle()

            sleep_gap_start, sleep_gap_end = self._compute_sleep_gap(now_wall, now_mono)

            with self._lock:
                self._last_idle_seconds = idle_seconds
                self._last_idle_backend = idle_backend

                if self._paused:
                    self._flush_locked(now_ts)
                else:
                    if sleep_gap_start is not None and sleep_gap_end is not None:
                        self._record_sleep_gap_locked(start_ts=sleep_gap_start, end_ts=sleep_gap_end)

                    normalized = self._apply_idle_state(detected=detected, idle_seconds=idle_seconds)
                    self._ingest_locked(now_ts, normalized)

            self._last_wall_ts = now_wall
            self._last_mono_ts = now_mono
            self._stop_event.wait(self.interval_seconds)

    def _compute_sleep_gap(self, now_wall: float, now_mono: float) -> tuple[int | None, int | None]:
        if self._last_wall_ts is None or self._last_mono_ts is None:
            return (None, None)

        wall_delta = max(0.0, now_wall - self._last_wall_ts)
        mono_delta = max(0.0, now_mono - self._last_mono_ts)
        suspended_seconds = max(0.0, wall_delta - mono_delta)

        if suspended_seconds < self.sleep_gap_seconds:
            return (None, None)

        start_ts = int(self._last_wall_ts)
        end_ts = int(now_wall)
        if end_ts <= start_ts:
            return (None, None)
        return (start_ts, end_ts)

    def _record_sleep_gap_locked(self, start_ts: int, end_ts: int) -> None:
        self._flush_locked(start_ts)
        self.db.insert_session(
            start_ts=start_ts,
            end_ts=end_ts,
            app="Suspensión/Hibernación",
            title="",
            source="sleep",
        )
        self._sleep_segments += 1

    def _detect_idle(self) -> tuple[int | None, str]:
        if not self.idle_enabled or self.idle_detector is None:
            return (None, "disabled")

        idle_seconds = self.idle_detector.get_idle_seconds()
        caps = self.idle_detector.capabilities()
        backend = str(caps.get("last_backend") or "none")
        return (idle_seconds, backend)

    def _apply_idle_state(self, detected: ActiveWindow | None, idle_seconds: int | None) -> ActiveWindow | None:
        if detected is None:
            return None

        if idle_seconds is None:
            return detected

        if idle_seconds >= self.idle_threshold_seconds:
            return ActiveWindow(app="Inactivo", title="", source="idle")

        if idle_seconds >= self.effective_idle_seconds:
            return ActiveWindow(
                app=detected.app,
                title=detected.title,
                source=f"{detected.source}:idle",
                pid=detected.pid,
                window_id=detected.window_id,
            )

        return detected

    def _ingest_locked(self, now_ts: int, detected: ActiveWindow | None) -> None:
        if detected is None:
            self._flush_locked(now_ts)
            return

        if self._should_exclude(detected):
            self._excluded_matches += 1
            self._flush_locked(now_ts)
            return

        if self._is_unidentified(detected):
            if self._current is None:
                return
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

    def _should_exclude(self, detected: ActiveWindow) -> bool:
        if self.privacy_filter is None:
            return False
        return self.privacy_filter.is_excluded(app=detected.app, title=detected.title)
