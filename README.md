# Actividad Web (tipo ActivityWatch)

App local para Linux que monitorea tu ventana activa y muestra estadísticas en una interfaz web en español.

## Qué hace

- Registra sesiones de uso por aplicación/ventana.
- Guarda los datos en SQLite (`data/actividad.db`).
- Expone API local para métricas.
- Muestra dashboard web con:
  - tiempo activo,
  - apps principales,
  - actividad por hora,
  - sesiones recientes.

## Requisitos

- Python 3.10+
- Dependencias Python de `requirements.txt`
- Para detección de ventana activa en X11:
  - `xdotool`
  - `xprop` (paquete `xorg-x11-utils` o similar)
- Opcional en Hyprland/Wayland:
  - `hyprctl`

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

Abre en navegador:

- http://127.0.0.1:18765

## Variables opcionales

- `ACTIVIDAD_DB_PATH` (default: `data/actividad.db`)
- `ACTIVIDAD_INTERVAL_SECONDS` (default: `2`)
- `ACTIVIDAD_ENABLE_KWIN_DBUS` (default: `0`, en KDE Wayland)

Ejemplo:

```bash
ACTIVIDAD_INTERVAL_SECONDS=1 uvicorn app.main:app --host 127.0.0.1 --port 18765
```

## Autostart (systemd --user)

El proyecto incluye `run.sh` para usarlo con un servicio de usuario.

Archivo recomendado de entorno:

```bash
mkdir -p ~/.config/actividad-web
cat > ~/.config/actividad-web/env <<'EOF'
ACTIVIDAD_PORT=18765
ACTIVIDAD_ENABLE_KWIN_DBUS=0
ACTIVIDAD_INTERVAL_SECONDS=2
ACTIVIDAD_DB_PATH=/home/$USER/actividad-web/data/actividad.db
EOF
```

## Notas de compatibilidad

- En X11, con `xdotool` + `xprop`, la detección es completa y estable.
- En Wayland:
  - Hyprland: soporte nativo con `hyprctl`.
  - KDE Plasma Wayland: por defecto usa fallback XWayland para evitar posibles interferencias del cursor.
    Puedes activar backend nativo con `ACTIVIDAD_ENABLE_KWIN_DBUS=1`.
  - Otros compositores: fallback con `xdotool` + `xprop` vía XWayland; ventanas nativas Wayland pueden no detectarse siempre.
- Si faltan utilidades, revisa `GET /api/health` para ver advertencias.
