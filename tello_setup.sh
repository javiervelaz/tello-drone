#!/bin/bash
# =============================================================
# tello_setup.sh
# Setup del sistema Tello Director MVP — UN SOLO PASO
#
# Uso:
#   chmod +x tello_setup.sh
#   ./tello_setup.sh
#
# Después del setup, iniciar con:
#   ./tello_start.sh
# =============================================================

set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
info() { echo -e "${CYAN}[>>]${NC} $1"; }
warn() { echo -e "${YELLOW}[!!]${NC} $1"; }
fail() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step() { echo ""; echo -e "${CYAN}━━━  $1  ━━━${NC}"; }

# Directorio donde vive este script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/venv"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   TELLO DIRECTOR — Setup MVP                 ║${NC}"
echo -e "${CYAN}║   Sistema de filmación autónoma DJI Tello    ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

# -------------------------------------------------------------
# PASO 1 — Python
# -------------------------------------------------------------
step "1/5 — Verificando Python 3"

PYTHON=""
for py in python3 python3.11 python3.10 python3.9 python3.8; do
    if command -v "$py" &>/dev/null; then
        VER=$("$py" -c "import sys; print(sys.version_info[:2])")
        if "$py" -c "import sys; exit(0 if sys.version_info >= (3,8) else 1)"; then
            PYTHON="$py"
            ok "Usando $py ($("$py" --version 2>&1))"
            break
        fi
    fi
done

[ -z "$PYTHON" ] && fail "Python 3.8+ no encontrado. Instalá Python 3 primero."

# -------------------------------------------------------------
# PASO 2 — Entorno virtual
# -------------------------------------------------------------
step "2/5 — Creando entorno virtual"

if [ -d "$VENV" ]; then
    ok "Entorno virtual ya existe en $VENV"
    info "Para reinstalar: rm -rf $VENV && ./tello_setup.sh"
else
    info "Creando venv en $VENV ..."
    "$PYTHON" -m venv "$VENV"
    ok "Entorno virtual creado"
fi

PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

# -------------------------------------------------------------
# PASO 3 — Dependencias Python
# -------------------------------------------------------------
step "3/5 — Instalando dependencias"

info "Actualizando pip..."
"$PIP" install --upgrade pip -q

DEPS=(
    "djitellopy2"           # SDK oficial DJI Tello
    "ultralytics"           # YOLOv8
    "opencv-python"         # visión + display
    "numpy"                 # arrays
)

for dep in "${DEPS[@]}"; do
    info "Instalando $dep ..."
    "$PIP" install "$dep" -q && ok "$dep instalado" || warn "$dep falló (ver abajo)"
done

# -------------------------------------------------------------
# PASO 4 — Pre-descargar modelo YOLO
# -------------------------------------------------------------
step "4/5 — Descargando modelo YOLOv8n"

MODEL_PATH="$SCRIPT_DIR/yolov8n.pt"

if [ -f "$MODEL_PATH" ]; then
    ok "yolov8n.pt ya existe ($(du -sh "$MODEL_PATH" | cut -f1))"
else
    info "Descargando yolov8n.pt (~6MB)..."
    "$PY" -c "
from ultralytics import YOLO
import shutil, os
model = YOLO('yolov8n.pt')
src = str(model.ckpt_path)
dst = '$MODEL_PATH'
if src != dst:
    shutil.copy(src, dst)
print('Guardado en: $MODEL_PATH')
" && ok "yolov8n.pt descargado" || warn "No se pudo pre-descargar (se descargará al iniciar)"
fi

# -------------------------------------------------------------
# PASO 5 — Permisos y verificación final
# -------------------------------------------------------------
step "5/5 — Verificación final"

chmod +x "$SCRIPT_DIR/tello_start.sh" 2>/dev/null && ok "tello_start.sh marcado como ejecutable" || true

# Verificar imports críticos
"$PY" -c "
imports = [('cv2','opencv-python'), ('numpy','numpy'),
           ('djitellopy','djitellopy2'), ('ultralytics','ultralytics')]
ok = True
for mod, pkg in imports:
    try:
        __import__(mod)
        print(f'  [OK] {mod}')
    except ImportError:
        print(f'  [!!] {mod} — falta: pip install {pkg}')
        ok = False
exit(0 if ok else 1)
" && ok "Todos los módulos importan correctamente" || warn "Algún módulo falló (revisar arriba)"

# Crear carpetas de salida
mkdir -p "$SCRIPT_DIR/grabaciones" "$SCRIPT_DIR/fotos"
ok "Carpetas grabaciones/ y fotos/ listas"

# -------------------------------------------------------------
# RESUMEN
# -------------------------------------------------------------
echo ""
echo -e "${CYAN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓  Setup completado${NC}"
echo -e "${CYAN}════════════════════════════════════════════════${NC}"
echo ""
echo "  PRÓXIMOS PASOS:"
echo ""
echo -e "  ${YELLOW}1.${NC} Encendé el Tello y conectá tu PC al WiFi TELLO-XXXXXX"
echo -e "  ${YELLOW}2.${NC} Iniciá el sistema:"
echo ""
echo -e "       ${GREEN}./tello_start.sh${NC}"
echo ""
echo "  OPCIONES DE INICIO:"
echo "    ./tello_start.sh                # modo normal"
echo "    ./tello_start.sh --sin-vuelo    # solo visión, sin volar"
echo "    ./tello_start.sh --sin-camara   # solo comandos, sin video"
echo ""
echo "  ÓRDENES DISPONIBLES:"
echo "    despegar | aterrizar | panoramica | cenital"
echo "    norte | sur | este | oeste | centro"
echo "    sube [N] | baja [N]  (ej: sube 1)"
echo "    seguir | dejar seguir | estado | foto | flip"
echo ""
