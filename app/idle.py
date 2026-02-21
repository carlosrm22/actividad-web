from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass
class IdleSample:
    seconds: int | None
    backend: str
    available: bool
    checked_ts: int


class IdleDetector:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        self._has_xprintidle = shutil.which("xprintidle") is not None
        self._has_xssstate = shutil.which("xssstate") is not None
        self._has_gdbus = shutil.which("gdbus") is not None
        self._lock = threading.Lock()
        self._last_sample = IdleSample(seconds=None, backend="none", available=False, checked_ts=0)

    def capabilities(self) -> dict[str, object]:
        backends: list[str] = []
        if self._has_xprintidle:
            backends.append("xprintidle")
        if self._has_xssstate:
            backends.append("xssstate")
        if self._has_gdbus:
            backends.append("screensaver_dbus")

        preferred = "none"
        if self._has_xprintidle:
            preferred = "xprintidle"
        elif self._has_xssstate:
            preferred = "xssstate"
        elif self._has_gdbus:
            preferred = "screensaver_dbus"

        with self._lock:
            sample = self._last_sample

        return {
            "enabled": self.enabled,
            "available": self.enabled and bool(backends),
            "backends": backends,
            "preferred_backend": preferred,
            "last_backend": sample.backend,
            "last_idle_seconds": sample.seconds,
            "last_checked_ts": sample.checked_ts,
        }

    def get_idle_seconds(self) -> int | None:
        if not self.enabled:
            self._store(None, "disabled", False)
            return None

        if self._has_xprintidle:
            value = self._get_idle_xprintidle()
            if value is not None:
                self._store(value, "xprintidle", True)
                return value

        if self._has_xssstate:
            value = self._get_idle_xssstate()
            if value is not None:
                self._store(value, "xssstate", True)
                return value

        if self._has_gdbus:
            value = self._get_idle_screensaver_dbus()
            if value is not None:
                self._store(value, "screensaver_dbus", True)
                return value

        self._store(None, "none", False)
        return None

    def _store(self, seconds: int | None, backend: str, available: bool) -> None:
        with self._lock:
            self._last_sample = IdleSample(
                seconds=seconds,
                backend=backend,
                available=available,
                checked_ts=int(time.time()),
            )

    def _run(self, args: list[str], timeout: float = 1.2) -> str | None:
        env = None
        if args and args[0] == "gdbus":
            env = os.environ.copy()
            runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
            bus_path = f"{runtime_dir}/bus"
            if "XDG_RUNTIME_DIR" not in env and os.path.exists(runtime_dir):
                env["XDG_RUNTIME_DIR"] = runtime_dir
            if not env.get("DBUS_SESSION_BUS_ADDRESS") and os.path.exists(bus_path):
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"

        try:
            out = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except (subprocess.SubprocessError, OSError):
            return None

        if out.returncode != 0:
            return None
        return out.stdout.strip()

    def _normalize_idle_value(self, raw_value: int) -> int:
        value = max(0, int(raw_value))
        if value >= 1000:
            return value // 1000
        return value

    def _get_idle_xprintidle(self) -> int | None:
        raw = self._run(["xprintidle"], timeout=0.8)
        if not raw:
            return None
        try:
            milliseconds = int(raw.strip())
        except ValueError:
            return None
        return self._normalize_idle_value(milliseconds)

    def _get_idle_xssstate(self) -> int | None:
        raw = self._run(["xssstate", "-i"], timeout=0.8)
        if not raw:
            return None
        match = re.search(r"(\d+)", raw)
        if not match:
            return None
        try:
            milliseconds = int(match.group(1))
        except ValueError:
            return None
        return self._normalize_idle_value(milliseconds)

    def _get_idle_screensaver_dbus(self) -> int | None:
        raw = self._run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.ScreenSaver",
                "--object-path",
                "/org/freedesktop/ScreenSaver",
                "--method",
                "org.freedesktop.ScreenSaver.GetSessionIdleTime",
            ],
            timeout=1.4,
        )
        if not raw:
            return None

        match = re.search(r"(\d+)", raw)
        if not match:
            return None

        try:
            value = int(match.group(1))
        except ValueError:
            return None

        return self._normalize_idle_value(value)
