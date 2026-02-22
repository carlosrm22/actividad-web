#!/usr/bin/env bash
set -euo pipefail
cd /home/carlos/actividad-web

uid="$(id -u)"
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$uid}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

# Defaults (can be overridden by systemd EnvironmentFile)
export ACTIVIDAD_PORT="${ACTIVIDAD_PORT:-18765}"
export ACTIVIDAD_ENABLE_KWIN_DBUS="${ACTIVIDAD_ENABLE_KWIN_DBUS:-0}"
export ACTIVIDAD_IDLE_ENABLED="${ACTIVIDAD_IDLE_ENABLED:-1}"
export ACTIVIDAD_IDLE_THRESHOLD_SECONDS="${ACTIVIDAD_IDLE_THRESHOLD_SECONDS:-60}"
export ACTIVIDAD_EFFECTIVE_IDLE_SECONDS="${ACTIVIDAD_EFFECTIVE_IDLE_SECONDS:-8}"
export ACTIVIDAD_SLEEP_GAP_SECONDS="${ACTIVIDAD_SLEEP_GAP_SECONDS:-90}"

exec /home/carlos/actividad-web/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$ACTIVIDAD_PORT"
