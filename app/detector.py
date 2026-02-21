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
    pid: int | None = None
    window_id: str | None = None


class WindowDetector:
    """Detecta la ventana activa con varios métodos por orden de prioridad."""

    def __init__(self) -> None:
        self._has_xdotool = shutil.which("xdotool") is not None
        self._has_kdotool = shutil.which("kdotool") is not None
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
        can_kdotool = self._has_kdotool
        can_kwin = self._enable_kwin_dbus and self._can_use_kwin_dbus()
        can_wayland = self._has_hyprctl or can_kdotool or can_kwin
        preferred_backend = "none"
        if session_type == "x11":
            preferred_backend = "x11" if can_x11 else "none"
        elif session_type == "wayland":
            if self._has_hyprctl:
                preferred_backend = "hyprctl"
            elif can_kdotool:
                preferred_backend = "kdotool"
            elif can_kwin:
                preferred_backend = "kwin_dbus"
            elif can_x11:
                preferred_backend = "x11_fallback"
        else:
            if self._has_hyprctl:
                preferred_backend = "hyprctl"
            elif can_kdotool:
                preferred_backend = "kdotool"
            elif can_kwin:
                preferred_backend = "kwin_dbus"
            elif can_x11:
                preferred_backend = "x11"

        return {
            "xdotool": self._has_xdotool,
            "kdotool": self._has_kdotool,
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

    def list_windows(self, limit: int = 300) -> list[ActiveWindow]:
        max_items = max(1, min(limit, 2000))
        if self._has_kdotool:
            return self._list_kdotool_windows(limit=max_items)
        if self._has_xdotool and self._has_xprop:
            return self._list_x11_windows(limit=max_items)
        return []

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
        if self._has_kdotool:
            detected = self._detect_kdotool()
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
        if args and args[0] in {"gdbus", "kdotool"}:
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
        app = (data.get("class") or data.get("initialClass") or "").strip()
        pid = self._coerce_int(data.get("pid"))
        app = self._resolve_app_name(app=app, pid=pid, title=title)

        return ActiveWindow(app=app, title=title, source="hyprctl")

    def _detect_kdotool(self) -> ActiveWindow | None:
        window_id = self._run(["kdotool", "getactivewindow"])
        if not window_id:
            return None

        title = self._run(["kdotool", "getwindowname", window_id]) or ""
        app = self._run(["kdotool", "getwindowclassname", window_id]) or ""
        pid = self._coerce_int(self._run(["kdotool", "getwindowpid", window_id]) or "")
        app = self._resolve_app_name(app=app, pid=pid, title=title)
        return ActiveWindow(app=app, title=title, source="kdotool", pid=pid, window_id=window_id)

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
        pid = self._extract_variant_map_int(raw, "pid")
        app = self._resolve_app_name(app=app, pid=pid, title=title)

        return ActiveWindow(app=app, title=title, source="kwin_dbus", pid=pid)

    def _detect_x11(self) -> ActiveWindow | None:
        window_id = self._run(["xdotool", "getactivewindow"])
        if not window_id:
            return None

        title = self._extract_quoted(self._run(["xprop", "-id", window_id, "WM_NAME"]) or "")
        if not title:
            title = self._extract_quoted(self._run(["xprop", "-id", window_id, "_NET_WM_NAME"]) or "")
        wm_class_raw = self._run(["xprop", "-id", window_id, "WM_CLASS"]) or ""

        app = self._extract_last_quoted(wm_class_raw)
        pid = self._pid_for_window(window_id)
        app = self._resolve_app_name(app=app, pid=pid, title=title)

        return ActiveWindow(app=app, title=title, source="x11", pid=pid, window_id=window_id)

    def _list_kdotool_windows(self, limit: int) -> list[ActiveWindow]:
        raw = self._run(["kdotool", "search", ""], timeout=2.0)
        if not raw:
            return []
        ids = [line.strip() for line in raw.splitlines() if line.strip()]
        unique_ids: list[str] = []
        seen: set[str] = set()
        for wid in ids:
            if wid in seen:
                continue
            seen.add(wid)
            unique_ids.append(wid)

        windows: list[ActiveWindow] = []
        for wid in unique_ids[:limit]:
            title = self._run(["kdotool", "getwindowname", wid]) or ""
            app = self._run(["kdotool", "getwindowclassname", wid]) or ""
            pid = self._coerce_int(self._run(["kdotool", "getwindowpid", wid]) or "")
            app = self._resolve_app_name(app=app, pid=pid, title=title)
            windows.append(ActiveWindow(app=app, title=title, source="kdotool", pid=pid, window_id=wid))

        return windows

    def _list_x11_windows(self, limit: int) -> list[ActiveWindow]:
        raw = self._run(["xdotool", "search", "--all", "--name", ".*"], timeout=2.0)
        if not raw:
            return []
        ids = [line.strip() for line in raw.splitlines() if line.strip()]
        windows: list[ActiveWindow] = []
        seen: set[str] = set()
        for wid in ids:
            if wid in seen:
                continue
            seen.add(wid)
            title = self._extract_quoted(self._run(["xprop", "-id", wid, "WM_NAME"]) or "")
            if not title:
                title = self._extract_quoted(self._run(["xprop", "-id", wid, "_NET_WM_NAME"]) or "")
            app = self._extract_last_quoted(self._run(["xprop", "-id", wid, "WM_CLASS"]) or "")
            pid = self._pid_for_window(wid)
            app = self._resolve_app_name(app=app, pid=pid, title=title)
            windows.append(ActiveWindow(app=app, title=title, source="x11", pid=pid, window_id=wid))
            if len(windows) >= limit:
                break
        return windows

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

    def _extract_variant_map_int(self, text: str, key: str) -> int | None:
        pattern = rf"'{re.escape(key)}'\s*:\s*<(-?\d+)>"
        match = re.search(pattern, text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _pid_for_window(self, window_id: str) -> int | None:
        pid = self._extract_pid(self._run(["xprop", "-id", window_id, "_NET_WM_PID"]) or "")
        if pid is not None:
            return pid
        return self._extract_pid(self._run(["xdotool", "getwindowpid", window_id]) or "")

    def _process_name_from_pid(self, pid: int | None) -> str:
        if pid is None:
            return ""
        try:
            return psutil.Process(pid).name().strip()
        except (psutil.Error, ProcessLookupError):
            return ""

    def _app_from_title(self, title: str) -> str:
        title = title.strip()
        if not title:
            return ""
        for sep in (" — ", " - ", " | ", " • "):
            if sep in title:
                right = title.rsplit(sep, maxsplit=1)[-1].strip()
                if right:
                    return right
        return ""

    def _resolve_app_name(self, app: str, pid: int | None, title: str) -> str:
        app = (app or "").strip()
        if app:
            return self._humanize_app_name(app)

        proc_name = self._process_name_from_pid(pid)
        if proc_name:
            return self._humanize_app_name(proc_name)

        from_title = self._app_from_title(title)
        if from_title:
            return self._humanize_app_name(from_title)

        if pid is not None:
            return f"Proceso-{pid}"
        return "Proceso"

    def _humanize_app_name(self, app: str) -> str:
        value = (app or "").strip()
        if not value:
            return "Proceso"

        lower = value.casefold()
        aliases = {
            "org.kde.konsole": "Konsole",
            "org.kde.dolphin": "Dolphin",
            "org.telegram.desktop": "Telegram",
            "brave-browser": "Brave",
            "brave": "Brave",
            "firefox": "Firefox",
            "code": "VS Code",
            "code-oss": "VS Code",
            "dev.aunetx.deezer": "Deezer",
            "deezer-desktop": "Deezer",
            "antigravity": "Antigravity",
            "obsidian": "Obsidian",
        }
        if lower in aliases:
            return aliases[lower]

        if "." in value and " " not in value:
            tail = value.split(".")[-1]
            if tail:
                value = tail

        value = value.removesuffix(".desktop")
        value = re.sub(r"[-_]+", " ", value).strip()
        if not value:
            return "Proceso"

        if any(ch.isupper() for ch in value):
            return value
        return value.title()

    def _coerce_int(self, value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

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
