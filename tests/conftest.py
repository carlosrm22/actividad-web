from __future__ import annotations

import importlib
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

import pytest
import uvicorn
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, timeout_seconds: float = 8.0) -> None:
    deadline = time.time() + timeout_seconds
    health_url = f"{base_url}/api/health"
    while time.time() < deadline:
        try:
            with urlopen(health_url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"El servidor no arrancó a tiempo: {health_url}")


def seed_database(db) -> None:
    now = int(time.time())
    db.insert_session(now - 900, now - 780, "Firefox", "Docs", "x11")
    db.insert_session(now - 780, now - 660, "VS Code", "Proyecto", "x11")
    db.insert_session(now - 660, now - 600, "Inactivo", "", "idle")
    db.insert_session(now - 600, now - 480, "Deezer", "Mix Diario", "x11")
    db.insert_session(now - 480, now - 420, "Konsole", "bash", "x11")
    db.set_app_category("Firefox", "Navegación")
    db.set_app_category("VS Code", "Desarrollo")


@pytest.fixture
def app_instance(tmp_path, monkeypatch):
    db_path = tmp_path / "actividad-test.db"
    monkeypatch.setenv("ACTIVIDAD_DB_PATH", str(db_path))
    monkeypatch.setenv("ACTIVIDAD_IDLE_ENABLED", "0")
    monkeypatch.setenv("ACTIVIDAD_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("ACTIVIDAD_ENABLE_KWIN_DBUS", "0")

    import app.main as main_mod

    importlib.reload(main_mod)
    app = main_mod.create_app()
    return app


@pytest.fixture
def client_app(app_instance):
    with TestClient(app_instance) as client:
        app_instance.state.tracker.set_paused(True)
        seed_database(app_instance.state.db)
        yield client, app_instance


@pytest.fixture
def live_server(app_instance):
    port = _find_free_port()
    config = uvicorn.Config(app_instance, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    _wait_for_health(base_url)
    app_instance.state.tracker.set_paused(True)
    seed_database(app_instance.state.db)

    yield base_url, app_instance

    server.should_exit = True
    thread.join(timeout=5)
    if thread.is_alive():
        raise RuntimeError("No se pudo detener el servidor de pruebas")


@pytest.fixture
def today_iso():
    return datetime.now().date().isoformat()
