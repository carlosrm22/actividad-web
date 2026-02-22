# Actividad Web (tipo ActivityWatch)

App local para Linux que monitorea ventana activa, inactividad (AFK) y estadísticas de uso en interfaz web en español.

## Qué hace

- Registra sesiones por aplicación/ventana.
- Detecta inactividad y la guarda como `Inactivo`.
- Detecta suspensión/hibernación y la registra como `Suspensión/Hibernación`.
- Calcula tiempo efectivo (con input reciente) y tiempo pasivo sin input.
- Guarda datos en SQLite (`data/actividad.db`).
- Permite pausar/reanudar tracking.
- Permite categorizar aplicaciones.
- Soporta exclusiones de privacidad por app/título (`contains`, `exact`, `regex`).
- Exporta sesiones en CSV/JSON.
- Backup/restore completo (sesiones + categorías + reglas de privacidad).
- Dashboard web con métricas, ranking, comparativas y gráficas.

## Requisitos

- Python 3.10+
- Dependencias de `requirements.txt`
- Detección de ventana (X11):
  - `xdotool`
  - `xprop` (paquete `xorg-x11-utils` o similar)
- Detección en KDE Wayland (recomendado):
  - `kdotool`
- AFK en X11/XWayland:
  - `xprintidle` (si existe en tu distro) o `xssstate`
- AFK en Wayland/KDE sin backend X11:
  - `loginctl` (logind), usado como fallback automático

### Fedora 43

`xprintidle` no está disponible en repos oficiales. Usa:

```bash
sudo dnf5 -y install xssstate
```

## Instalación rápida

```bash
cd ~/actividad-web
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ejecutar

```bash
cd ~/actividad-web
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 18765
```

Abrir en navegador:

- http://127.0.0.1:18765

## Comando rápido `actividad` (recomendado)

Para reiniciar/diagnosticar rápido cuando se cuelgue, crea este helper en `~/bin`:

```bash
mkdir -p ~/bin
cat > ~/bin/actividad <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
uid="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$uid}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

service="actividad-web.service"
action="${1:-restart}"

case "$action" in
  restart) systemctl --user restart "$service"; systemctl --user status "$service" --no-pager -l ;;
  start)   systemctl --user start "$service";   systemctl --user status "$service" --no-pager -l ;;
  stop)    systemctl --user stop "$service";    systemctl --user status "$service" --no-pager -l || true ;;
  status)  systemctl --user status "$service" --no-pager -l ;;
  logs)    journalctl --user -u "$service" -n 80 --no-pager ;;
  open)    xdg-open "http://127.0.0.1:18765" >/dev/null 2>&1 & ;;
  *) echo "Uso: actividad [restart|start|stop|status|logs|open]"; exit 2 ;;
esac
EOF
chmod +x ~/bin/actividad
hash -r
```

Uso:

```bash
actividad
actividad status
actividad logs
actividad open
```

## Variables opcionales

- `ACTIVIDAD_DB_PATH` (default: `data/actividad.db`)
- `ACTIVIDAD_INTERVAL_SECONDS` (default: `2`)
- `ACTIVIDAD_ENABLE_KWIN_DBUS` (default: `0`)
- `ACTIVIDAD_IDLE_ENABLED` (default: `1`)
- `ACTIVIDAD_IDLE_THRESHOLD_SECONDS` (default: `60`)
- `ACTIVIDAD_EFFECTIVE_IDLE_SECONDS` (default: `8`)
- `ACTIVIDAD_SLEEP_GAP_SECONDS` (default: `90`)

## API principal

- `GET /api/health`
- `GET /api/overview` (`mode`, `group_by`, fechas)
- `GET /api/ranking`
- `GET /api/recent`
- `GET /api/windows`
- `GET /api/categories`
- `PUT /api/categories/{app}`
- `DELETE /api/categories/{app}`
- `GET /api/privacy/rules`
- `POST /api/privacy/rules`
- `PATCH /api/privacy/rules/{id}`
- `DELETE /api/privacy/rules/{id}`
- `GET /api/export/sessions?format=json|csv`
- `GET /api/backup/export`
- `POST /api/backup/restore?replace=0|1`
- `POST /api/control/pause`
- `POST /api/control/resume`
- `POST /api/control/state`

## Tests automáticos

Instalar dependencias de test:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Ejecutar suite completa:

```bash
pytest -q
```

Solo API:

```bash
pytest -q tests/test_api.py
```

Solo UI:

```bash
pytest -q tests/test_ui.py
```

Nota: para tests UI necesitas Chromium para Playwright:

```bash
playwright install chromium
```

## Autostart (systemd --user)

Archivo recomendado de entorno:

```bash
mkdir -p ~/.config/actividad-web
cat > ~/.config/actividad-web/env <<'EOF_ENV'
ACTIVIDAD_PORT=18765
ACTIVIDAD_ENABLE_KWIN_DBUS=0
ACTIVIDAD_INTERVAL_SECONDS=2
ACTIVIDAD_IDLE_ENABLED=1
ACTIVIDAD_IDLE_THRESHOLD_SECONDS=60
ACTIVIDAD_EFFECTIVE_IDLE_SECONDS=8
ACTIVIDAD_SLEEP_GAP_SECONDS=90
ACTIVIDAD_DB_PATH=/home/$USER/actividad-web/data/actividad.db
EOF_ENV
```

## Notas de compatibilidad

- X11: detección estable con `xdotool` + `xprop`.
- Wayland:
  - Hyprland: backend nativo con `hyprctl`.
  - KDE Plasma Wayland: backend nativo recomendado con `kdotool`.
  - Fallback XWayland puede perder apps nativas Wayland.
- Revisa `GET /api/health` para confirmar backend de detección e idle disponible.
