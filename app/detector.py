from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

import psutil


@dataclass
class ActiveWindow:
    app: str
    title: str
    source: str


class WindowDetector:
    """Detecta la ventana activa con varios métodos por orden de prioridad."""

    def __init__(self) -> None:
        self._has_xdotool = shutil.which("xdotool") is not None
        self._has_xprop = shutil.which("xprop") is not None
        self._has_hyprctl = shutil.which("hyprctl") is not None
        self._has_gdbus = shutil.which("gdbus") is not None
        self._enable_kwin_dbus = os.getenv("ACTIVIDAD_ENABLE_KWIN_DBUS", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._kwin_probe_cache: tuple[float, bool] | None = None

    def _session_type(self) -> Literal["wayland", "x11", "unknown"]:
        raw = os.getenv("XDG_SESSION_TYPE", "").strip().lower()
        if raw in {"wayland", "x11"}:
            return raw

        wayland_display = os.getenv("WAYLAND_DISPLAY", "").strip()
        x_display = os.getenv("DISPLAY", "").strip()
        if wayland_display:
            return "wayland"

        # Fallback por procesos cuando no tenemos entorno de sesión confiable.
        names = set()
        for proc in psutil.process_iter(attrs=["name"]):
            name = (proc.info.get("name") or "").lower()
            if name:
                names.add(name)

        if {"kwin_wayland", "gnome-shell", "hyprland", "sway"} & names:
            return "wayland"
        if x_display and not wayland_display:
            # En algunas configuraciones X11, XDG_SESSION_TYPE llega vacío.
            return "x11"
        if "xorg" in names:
            return "x11"
        return "unknown"

    def capabilities(self) -> dict[str, object]:
        session_type = self._session_type()
        can_x11 = self._has_xdotool and self._has_xprop
        can_kwin = self._enable_kwin_dbus and self._can_use_kwin_dbus()
        can_wayland = self._has_hyprctl or can_kwin
        preferred_backend = "none"
        if session_type == "x11":
            preferred_backend = "x11" if can_x11 else "none"
        elif session_type == "wayland":
            if self._has_hyprctl:
                preferred_backend = "hyprctl"
            elif can_kwin:
                preferred_backend = "kwin_dbus"
            elif can_x11:
                preferred_backend = "x11_fallback"
        else:
            if self._has_hyprctl:
                preferred_backend = "hyprctl"
            elif can_kwin:
                preferred_backend = "kwin_dbus"
            elif can_x11:
                preferred_backend = "x11"

        return {
            "xdotool": self._has_xdotool,
            "xprop": self._has_xprop,
            "hyprctl": self._has_hyprctl,
            "gdbus": self._has_gdbus,
            "kwin_dbus_enabled": self._enable_kwin_dbus,
            "kwin_dbus": can_kwin,
            "session_type": session_type,
            "wayland": session_type == "wayland",
            "can_detect_x11": can_x11,
            "can_detect_wayland_native": can_wayland,
            "preferred_backend": preferred_backend,
        }

    def detect(self) -> ActiveWindow | None:
        session_type = self._session_type()

        if session_type == "x11":
            return self._detect_x11_first()
        if session_type == "wayland":
            return self._detect_wayland_first()

        # Entorno desconocido: probamos ambas rutas.
        return self._detect_wayland_first() or self._detect_x11_first()

    def _detect_x11_first(self) -> ActiveWindow | None:
        if self._has_xdotool and self._has_xprop:
            detected = self._detect_x11()
            if detected is not None:
                return detected
        if self._has_hyprctl:
            detected = self._detect_hyprland()
            if detected is not None:
                return detected
        return None

    def _detect_wayland_first(self) -> ActiveWindow | None:
        if self._has_hyprctl:
            detected = self._detect_hyprland()
            if detected is not None:
                return detected
        if self._enable_kwin_dbus and self._can_use_kwin_dbus():
            detected = self._detect_kwin_dbus()
            if detected is not None:
                return detected
        if self._has_xdotool and self._has_xprop:
            # Fallback útil cuando la app activa corre sobre XWayland.
            detected = self._detect_x11()
            if detected is not None:
                return detected
        return None

    def _run(self, args: list[str], timeout: float = 1.5) -> str | None:
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

    def _detect_hyprland(self) -> ActiveWindow | None:
        raw = self._run(["hyprctl", "activewindow", "-j"])
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None

        title = (data.get("title") or "").strip()
        app = (data.get("class") or data.get("initialClass") or "Desconocido").strip()

        if not app:
            app = "Desconocido"

        return ActiveWindow(app=app, title=title, source="hyprctl")

    def _detect_kwin_dbus(self) -> ActiveWindow | None:
        raw = self._run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.kde.KWin",
                "--object-path",
                "/KWin",
                "--method",
                "org.kde.KWin.queryWindowInfo",
            ],
            timeout=2.0,
        )
        if not raw:
            return None

        title = self._extract_variant_map_value(raw, "caption")
        app = (
            self._extract_variant_map_value(raw, "resourceClass")
            or self._extract_variant_map_value(raw, "desktopFile")
            or self._extract_variant_map_value(raw, "resourceName")
        )
        if not app:
            app = "Desconocido"

        return ActiveWindow(app=app, title=title, source="kwin_dbus")

    def _detect_x11(self) -> ActiveWindow | None:
        window_id = self._run(["xdotool", "getactivewindow"])
        if not window_id:
            return None

        title = self._extract_quoted(
            self._run(["xprop", "-id", window_id, "WM_NAME"]) or ""
        )
        wm_class_raw = self._run(["xprop", "-id", window_id, "WM_CLASS"]) or ""
        pid_raw = self._run(["xprop", "-id", window_id, "_NET_WM_PID"]) or ""

        app = self._extract_last_quoted(wm_class_raw)
        pid = self._extract_pid(pid_raw)

        if not app and pid is not None:
            try:
                app = psutil.Process(pid).name()
            except (psutil.Error, ProcessLookupError):
                app = ""

        if not app:
            app = "Desconocido"

        return ActiveWindow(app=app, title=title, source="x11")

    def _extract_quoted(self, text: str) -> str:
        # Soporta WM_NAME(STRING) = "Texto"
        match = re.search(r'"(.*)"', text)
        if match:
            return match.group(1).strip()

        # Fallback para formatos sin comillas
        parts = text.split("=", maxsplit=1)
        if len(parts) == 2:
            return parts[1].strip().strip('"').strip("'")
        return ""

    def _extract_last_quoted(self, text: str) -> str:
        matches = re.findall(r'"([^"]+)"', text)
        if not matches:
            return ""
        return matches[-1].strip()

    def _extract_pid(self, text: str) -> int | None:
        match = re.search(r"(\d+)", text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _extract_variant_map_value(self, text: str, key: str) -> str:
        # Parsea fragmentos tipo: 'caption': <'Título'>
        pattern = rf"'{re.escape(key)}'\s*:\s*<'((?:\\'|[^'])*)'>"
        match = re.search(pattern, text)
        if not match:
            return ""
        return match.group(1).replace("\\'", "'").strip()

    def _is_kwin_running(self) -> bool:
        for proc in psutil.process_iter(attrs=["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in {"kwin_wayland", "kwin_x11"}:
                return True
        return False

    def _can_use_kwin_dbus(self) -> bool:
        if not self._has_gdbus or not self._is_kwin_running():
            return False

        now = time.time()
        if self._kwin_probe_cache:
            cached_at, cached_ok = self._kwin_probe_cache
            ttl = 15.0 if cached_ok else 2.0
            if (now - cached_at) < ttl:
                return cached_ok

        raw = self._run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.kde.KWin",
                "--object-path",
                "/KWin",
                "--method",
                "org.kde.KWin.currentDesktop",
            ],
            timeout=2.5,
        )
        ok = raw is not None
        self._kwin_probe_cache = (now, ok)
        return ok
