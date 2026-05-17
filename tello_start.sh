#!/bin/bash
# =============================================================
# tello_start.sh
# Arranca el Tello Director MVP directamente.
# NO necesitás activar entorno virtual ni usar 'source'.
#
# Uso:
#   ./tello_start.sh                  modo normal
#   ./tello_start.sh --sin-vuelo      solo visión, Tello apagado
#   ./tello_start.sh --sin-camara     solo comandos, sin video
# =============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"

# Verificar que el setup fue ejecutado
if [ ! -f "$VENV_PYTHON" ]; then
    echo ""
    echo "  [!] Setup no encontrado. Ejecutá primero:"
    echo ""
    echo "      chmod +x tello_setup.sh && ./tello_setup.sh"
    echo ""
    exit 1
fi

# Verificar tello_director.py
if [ ! -f "$SCRIPT_DIR/tello_director.py" ]; then
    echo ""
    echo "  [!] tello_director.py no encontrado en $SCRIPT_DIR"
    echo ""
    exit 1
fi

# Mostrar recordatorio de WiFi si no hay --sin-vuelo
if [[ ! " $* " =~ " --sin-vuelo " ]]; then
    echo ""
    echo "  ┌─────────────────────────────────────────────────┐"
    echo "  │  Asegurate de estar conectado al WiFi del Tello │"
    echo "  │  Red: TELLO-XXXXXX  (sin contraseña)            │"
    echo "  └─────────────────────────────────────────────────┘"
    echo ""
fi

# Lanzar — exec reemplaza este proceso con Python (sin subshell extra)
exec "$VENV_PYTHON" "$SCRIPT_DIR/tello_director.py" "$@"
