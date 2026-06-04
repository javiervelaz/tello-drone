"""
tello_director.py
=================
Orquestador principal del sistema Tello Director MVP.

Arquitectura de threads:
  - main thread     : loop de órdenes (consola o web según --web)
  - vision_thread   : captura Tello + YOLO + display OpenCV
  - tracking_thread : envía rc_control al Tello con correcciones de visión
  - telemetria_thread: actualiza batería/altura y corta vuelo si baja < 15%
  - flask_thread    : servidor web (solo con --web)

Uso:
    venv/Scripts/python tello_director.py           consola
    venv/Scripts/python tello_director.py --web     interfaz web
    venv/Scripts/python tello_director.py --sin-vuelo
    venv/Scripts/python tello_director.py --sin-camara
"""

import argparse
import queue
import threading
import time
import cv2
import sys
from datetime import datetime


# ============================================================
# ESTADO COMPARTIDO (thread-safe)
# ============================================================
class EstadoSistema:
    def __init__(self):
        self.lock = threading.Lock()

        # vuelo
        self.en_vuelo      = False
        self.altura_cm     = 0
        self.yaw_acumulado = 0
        self.corriendo     = True
        self.modo          = "tierra"

        # visión
        self.frame_actual    = None
        self.personas        = []
        self.sujeto          = None
        self.tracking_activo = False
        self.correccion      = {"lr": 0, "fb": 0, "ud": 0, "yaw": 0}

        # info
        self.ultima_orden = ""
        self.bateria      = 100
        self.altura_tello = 0

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}")

    def set_modo(self, modo: str):
        with self.lock:
            self.modo = modo
        self.log(f"Modo → {modo.upper()}")


# ============================================================
# THREAD: TRACKING
# ============================================================
def tracking_loop(tello, estado: EstadoSistema):
    while estado.corriendo:
        try:
            if (estado.tracking_activo and
                    estado.en_vuelo and
                    estado.modo == "tracking" and
                    tello is not None):
                with estado.lock:
                    c = estado.correccion.copy()
                tello.send_rc_control(
                    int(c["lr"]), int(c["fb"]),
                    int(c["ud"]), int(c["yaw"]),
                )
        except Exception as e:
            estado.log(f"[tracking] {e}")
        time.sleep(0.1)


# ============================================================
# THREAD: TELEMETRÍA
# ============================================================
def telemetria_loop(tello, estado: EstadoSistema):
    while estado.corriendo:
        try:
            if tello is not None and estado.en_vuelo:
                bat = tello.get_battery()
                alt = tello.get_height()
                with estado.lock:
                    estado.bateria      = bat
                    estado.altura_tello = alt
                if bat < 15:
                    estado.log(f"⚠ BATERÍA CRÍTICA: {bat}% — aterrizando")
                    tello.land()
                    estado.en_vuelo = False
                    estado.set_modo("tierra")
        except Exception:
            pass
        time.sleep(2.0)


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Tello Director MVP")
    parser.add_argument("--sin-vuelo",  action="store_true")
    parser.add_argument("--sin-camara", action="store_true")
    parser.add_argument("--web",        action="store_true",
                        help="Usar interfaz web en lugar de consola")
    parser.add_argument("--port",       type=int, default=5000,
                        help="Puerto del servidor web (default: 5000)")
    args = parser.parse_args()

    print()
    print("=" * 52)
    print("   TELLO DIRECTOR — MVP Filmación Autónoma")
    modo_ui = "WEB (http://localhost:{})".format(args.port) if args.web else "CONSOLA"
    print(f"   Interfaz: {modo_ui}")
    print("=" * 52)

    estado    = EstadoSistema()
    cmd_queue = queue.Queue()
    tello     = None

    # ----------------------------------------------------------
    # 1. Conectar Tello
    # ----------------------------------------------------------
    if not args.sin_vuelo:
        try:
            from djitellopy import Tello as _Tello
        except ImportError:
            print("\n[ERROR] djitellopy no instalado.")
            sys.exit(1)

        print("\n[1/3] Conectando con Tello...")
        try:
            tello = _Tello()
            tello.connect()
            bat = tello.get_battery()
            estado.bateria = bat
            print(f"      Tello OK ✓  —  Batería: {bat}%")
            if bat < 20:
                print(f"      ⚠ Batería baja ({bat}%)")
        except Exception as e:
            print(f"      ✗ Sin conexión: {e}")
            print("      Iniciando en modo --sin-vuelo...")
            tello = None
            args.sin_vuelo = True
    else:
        print("\n[Modo --sin-vuelo activo]")

    # ----------------------------------------------------------
    # 2. Visión
    # ----------------------------------------------------------
    if not args.sin_camara:
        if tello is not None:
            print("\n[2/3] Iniciando stream de video...")
            try:
                tello.streamon()
                print("      Stream activo ✓")
            except Exception as e:
                print(f"      Stream falló: {e}")

        from tello_vision import SistemaVision
        vision = SistemaVision(tello, estado)
        threading.Thread(target=vision.loop, daemon=True, name="vision").start()
        print("      YOLOv8n activo ✓")
    else:
        print("\n[Visión desactivada]")

    # ----------------------------------------------------------
    # 3. Threads de soporte
    # ----------------------------------------------------------
    if tello is not None:
        threading.Thread(
            target=tracking_loop, args=(tello, estado),
            daemon=True, name="tracking").start()
        threading.Thread(
            target=telemetria_loop, args=(tello, estado),
            daemon=True, name="telemetria").start()

    # ----------------------------------------------------------
    # 4. Módulos de comando
    # ----------------------------------------------------------
    from tello_commands import ParserOrdenes, EjecutorTello
    from tello_recorder import GrabadorTello

    cmd_parser = ParserOrdenes()
    ejecutor   = EjecutorTello(tello, estado)
    grabador   = GrabadorTello(estado)
    grabador.iniciar()

    print("\n[3/3] Sistema listo.")

    # ----------------------------------------------------------
    # 5a. Modo WEB — Flask + loop desde queue
    # ----------------------------------------------------------
    if args.web:
        from tello_web import ServidorWeb
        servidor = ServidorWeb(estado, cmd_queue, port=args.port)
        servidor.iniciar()

        print()
        print("─" * 52)
        print(f"  Abrí en el browser: http://localhost:{args.port}")
        print("  Ctrl+C para terminar")
        print("─" * 52)
        print()

        while estado.corriendo:
            try:
                # esperar comando de la queue con timeout para poder
                # detectar estado.corriendo == False
                try:
                    texto = cmd_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if not texto:
                    continue

                estado.log(f"Web >> {texto}")
                grabador.registrar_orden(texto)
                orden     = cmd_parser.parsear(texto)
                continuar = ejecutor.ejecutar(orden)
                if not continuar:
                    break

            except KeyboardInterrupt:
                print("\n[!] Ctrl+C — terminando...")
                break

    # ----------------------------------------------------------
    # 5b. Modo CONSOLA — input() bloqueante
    # ----------------------------------------------------------
    else:
        print()
        print("─" * 52)
        print("  ÓRDENES: despegar | aterrizar | panoramica | cenital")
        print("           norte | sur | este | oeste | centro")
        print("           sube [N] | baja [N]  (ej: sube 1)")
        print("           seguir | dejar seguir | estado | foto | flip")
        print("  [Q en ventana de video para salir]")
        print("─" * 52)
        print()

        while estado.corriendo:
            try:
                texto = input(">> ").strip()
                if not texto:
                    continue
                grabador.registrar_orden(texto)
                orden     = cmd_parser.parsear(texto)
                continuar = ejecutor.ejecutar(orden)
                if not continuar:
                    break
            except KeyboardInterrupt:
                print("\n[!] Ctrl+C — terminando...")
                break
            except EOFError:
                break

    # ----------------------------------------------------------
    # Cleanup
    # ----------------------------------------------------------
    estado.corriendo = False
    grabador.finalizar()

    if tello is not None:
        if estado.en_vuelo:
            print("[*] Aterrizando por seguridad...")
            try:
                tello.land()
            except Exception:
                pass
        try:
            tello.streamoff()
            tello.end()
        except Exception:
            pass

    cv2.destroyAllWindows()
    print("\n[OK] Sistema terminado.")


if __name__ == "__main__":
    main()
