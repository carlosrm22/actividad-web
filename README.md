# Actividad Web (tipo ActivityWatch)

App local para Linux que monitorea ventana activa, inactividad (AFK) y estadísticas de uso en interfaz web en español.

## Qué hace

- Registra sesiones de uso por aplicación/ventana.
- Detecta inactividad y la guarda como `Inactivo`.
- Guarda datos en SQLite (`data/actividad.db`).
- Permite pausar/reanudar tracking.
- Permite categorizar aplicaciones para análisis por contexto.
- Dashboard web con:
  - tiempo activo,
  - tiempo AFK,
  - ranking por app o categoría,
  - comparativa con período anterior,
  - tendencia del período,
  - resumen de últimos 30 días.

## Requisitos

- Python 3.10+
- Dependencias Python de `requirements.txt`
- Para detección de ventana activa en X11:
  - `xdotool`
  - `xprop` (paquete `xorg-x11-utils` o similar)
- Para KDE Plasma Wayland (recomendado):
  - `kdotool`
- Opcional en Hyprland/Wayland:
  - `hyprctl`
- Para detección AFK en X11/XWayland:
  - `xprintidle`

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

## Variables opcionales

- `ACTIVIDAD_DB_PATH` (default: `data/actividad.db`)
- `ACTIVIDAD_INTERVAL_SECONDS` (default: `2`)
- `ACTIVIDAD_ENABLE_KWIN_DBUS` (default: `0`, en KDE Wayland)
- `ACTIVIDAD_IDLE_ENABLED` (default: `1`)
- `ACTIVIDAD_IDLE_THRESHOLD_SECONDS` (default: `60`)

Ejemplo:

```bash
ACTIVIDAD_INTERVAL_SECONDS=1 \
ACTIVIDAD_IDLE_THRESHOLD_SECONDS=90 \
uvicorn app.main:app --host 127.0.0.1 --port 18765
```

## API principal

- `GET /api/health`
- `GET /api/overview` (`mode`, `group_by`, fechas)
- `GET /api/ranking`
- `GET /api/recent`
- `GET /api/windows`
- `GET /api/categories`
- `PUT /api/categories/{app}`
- `DELETE /api/categories/{app}`
- `POST /api/control/pause`
- `POST /api/control/resume`
- `POST /api/control/state`

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
ACTIVIDAD_DB_PATH=/home/$USER/actividad-web/data/actividad.db
EOF_ENV
```

## Notas de compatibilidad

- En X11, con `xdotool` + `xprop`, la detección de ventana activa es estable.
- En Wayland:
  - Hyprland: backend nativo con `hyprctl`.
  - KDE Plasma Wayland: backend nativo recomendado con `kdotool`.
  - Fallback XWayland (`xdotool` + `xprop`) puede perder apps nativas Wayland.
- Si faltan utilidades, revisa `GET /api/health` para advertencias.
