"""
tello_vision.py
===============
Sistema de visión para DJI Tello.
Refactorizado de tracking_personas.py y drone_director.SistemaVision.

Cambios respecto al original:
  - Fuente de video: tello.get_frame_read().frame (no webcam/archivo)
  - Corrección de tracking: yaw + up/down vía rc_control (no NED offboard)
  - Loop con time.sleep (threading, no asyncio)
"""

import cv2
import math
import time
import numpy as np

try:
    from ultralytics import YOLO
    _YOLO_OK = True
except ImportError:
    _YOLO_OK = False


# ============================================================
# CONFIGURACIÓN
# ============================================================
CONFIG = {
    "modelo":          "yolov8n.pt",
    "confianza":       0.45,
    "resolucion":      (640, 480),
    "fps_objetivo":    15,

    # zona muerta central — no corregir si el offset es menor
    "zona_muerta_px":  50,

    # ganancias de control → valor rc (-100..100)
    # yaw: rota el drone para centrar al sujeto horizontalmente
    # ud:  sube/baja para centrar verticalmente
    "ganancia_yaw":    0.15,
    "ganancia_ud":     0.12,
    "max_rc":          30,   # limita movimientos bruscos

    # colores HUD
    "color_ok":        (0, 255, 0),      # verde — detecciones
    "color_tracking":  (0, 100, 255),    # naranja — sujeto activo
    "color_info":      (200, 200, 200),  # gris — HUD texto
}


# ============================================================
# SISTEMA DE VISIÓN
# ============================================================
class SistemaVision:
    """
    Corre en su propio thread (daemon).
    Escribe en estado.frame_actual, estado.personas, estado.sujeto,
    estado.correccion — todos bajo estado.lock.
    """

    def __init__(self, tello, estado):
        self.tello   = tello
        self.estado  = estado
        self.cfg     = CONFIG
        self.modelo  = None
        self._frames_sin_sujeto = 0
        self._MAX_PERDIDA       = 20   # frames antes de soltar el sujeto

    # ----------------------------------------------------------
    # Init del modelo
    # ----------------------------------------------------------
    def _cargar_modelo(self) -> bool:
        if not _YOLO_OK:
            self.estado.log("[Vision] ultralytics no instalado — visión desactivada")
            return False
        self.estado.log("[Vision] Cargando YOLOv8n...")
        self.modelo = YOLO(self.cfg["modelo"])
        self.estado.log("[Vision] Modelo listo ✓")
        return True

    # ----------------------------------------------------------
    # Captura de frame
    # ----------------------------------------------------------
    def _get_frame(self):
        if self.tello is None:
            return None
        try:
            frame = self.tello.get_frame_read().frame
            if frame is not None and frame.size > 0:
                return cv2.resize(frame, self.cfg["resolucion"])
        except Exception:
            pass
        return None

    # ----------------------------------------------------------
    # Detección YOLO
    # ----------------------------------------------------------
    def _detectar(self, frame: np.ndarray) -> list:
        if self.modelo is None:
            return []
        resultados = self.modelo(
            frame,
            conf=self.cfg["confianza"],
            classes=[0],         # solo "person" en COCO
            verbose=False,
        )
        personas = []
        for r in resultados:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                conf = float(box.conf[0])
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                personas.append({
                    "bbox":      (x1, y1, x2, y2),
                    "centro":    (cx, cy),
                    "confianza": conf,
                    "area":      (x2 - x1) * (y2 - y1),
                })
        # mayor área primero = persona más cercana
        personas.sort(key=lambda p: p["area"], reverse=True)
        return personas

    # ----------------------------------------------------------
    # Tracking de sujeto (persistencia entre frames)
    # ----------------------------------------------------------
    def _actualizar_sujeto(self, personas: list):
        if not personas:
            self._frames_sin_sujeto += 1
            if self._frames_sin_sujeto > self._MAX_PERDIDA:
                self.estado.sujeto = None
            return
        self._frames_sin_sujeto = 0
        prev = self.estado.sujeto
        if prev is None:
            self.estado.sujeto = personas[0]
        else:
            # seguir la detección más cercana al sujeto anterior
            cx_p, cy_p = prev["centro"]
            self.estado.sujeto = min(
                personas,
                key=lambda p: math.hypot(
                    p["centro"][0] - cx_p,
                    p["centro"][1] - cy_p,
                )
            )

    # ----------------------------------------------------------
    # Cálculo de corrección rc_control
    # Mapping:
    #   offset_x (horizontal) → yaw  (rotar drone)
    #   offset_y (vertical)   → ud   (subir/bajar)
    # ----------------------------------------------------------
    def _calcular_correccion(self, sujeto) -> dict:
        nulo = {"lr": 0, "fb": 0, "ud": 0, "yaw": 0}
        if sujeto is None:
            return nulo

        w, h   = self.cfg["resolucion"]
        cx_f   = w // 2
        cy_f   = h // 2
        cx, cy = sujeto["centro"]
        off_x  = cx - cx_f   # positivo = sujeto a la derecha
        off_y  = cy - cy_f   # positivo = sujeto abajo

        zona   = self.cfg["zona_muerta_px"]
        max_rc = self.cfg["max_rc"]

        yaw = 0
        if abs(off_x) > zona:
            yaw = int(off_x * self.cfg["ganancia_yaw"])
            yaw = max(-max_rc, min(max_rc, yaw))

        ud = 0
        if abs(off_y) > zona:
            # invertido: sujeto abajo (off_y > 0) → drone baja (ud negativo)
            ud = int(-off_y * self.cfg["ganancia_ud"])
            ud = max(-max_rc, min(max_rc, ud))

        return {"lr": 0, "fb": 0, "ud": ud, "yaw": yaw}

    # ----------------------------------------------------------
    # Dibujo del HUD
    # ----------------------------------------------------------
    def _dibujar(self, frame: np.ndarray, personas: list,
                 sujeto, correccion: dict) -> np.ndarray:
        h, w = frame.shape[:2]
        cx_f = w // 2
        cy_f = h // 2
        zona = self.cfg["zona_muerta_px"]
        modo = self.estado.modo.upper()

        # retícula central
        cv2.line(frame, (cx_f - 25, cy_f), (cx_f + 25, cy_f), (255, 255, 255), 1)
        cv2.line(frame, (cx_f, cy_f - 25), (cx_f, cy_f + 25), (255, 255, 255), 1)
        cv2.rectangle(frame,
                      (cx_f - zona, cy_f - zona),
                      (cx_f + zona, cy_f + zona),
                      (60, 60, 60), 1)

        # todas las detecciones (verde, fino)
        for p in personas:
            x1, y1, x2, y2 = p["bbox"]
            cv2.rectangle(frame, (x1, y1), (x2, y2),
                          self.cfg["color_ok"], 1)
            cv2.putText(frame, f"{p['confianza']:.2f}",
                        (x1, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                        self.cfg["color_ok"], 1)

        # sujeto seguido (naranja, grueso)
        if sujeto:
            x1, y1, x2, y2 = sujeto["bbox"]
            col = self.cfg["color_tracking"]
            cv2.rectangle(frame, (x1, y1), (x2, y2), col, 2)
            cv2.putText(frame, "TRACKING",
                        (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
            # línea desde centro al sujeto
            cv2.line(frame, (cx_f, cy_f), sujeto["centro"], col, 1)
            # info de corrección
            if correccion["yaw"] != 0 or correccion["ud"] != 0:
                cv2.putText(frame,
                            f"YAW:{correccion['yaw']:+d}  UD:{correccion['ud']:+d}",
                            (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
            else:
                cv2.putText(frame, "CENTRADO",
                            (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            self.cfg["color_ok"], 2)
        elif self.estado.tracking_activo:
            cv2.putText(frame, "Buscando persona...",
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)

        # HUD inferior: modo + batería + última orden
        bat    = self.estado.bateria
        alt    = self.estado.altura_tello or self.estado.altura_cm
        col_hud = self.cfg["color_info"]
        cv2.putText(frame,
                    f"MODO:{modo}  BAT:{bat}%  ALT:{alt}cm  P:{len(personas)}",
                    (10, h - 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, col_hud, 1)
        cv2.putText(frame,
                    f">> {self.estado.ultima_orden[:55]}",
                    (10, h - 7),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (150, 150, 255), 1)

        # indicador REC parpadeante
        elapsed = time.time()
        if int(elapsed * 2) % 2 == 0:
            cv2.circle(frame, (w - 20, 18), 6, (0, 0, 255), -1)
        cv2.putText(frame, "REC",
                    (w - 50, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

        # instrucciones
        cv2.putText(frame, "Q:salir  R:reset sujeto",
                    (w - 180, h - 7),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (100, 100, 100), 1)

        return frame

    # ----------------------------------------------------------
    # Loop principal (se ejecuta en su propio thread)
    # ----------------------------------------------------------
    def loop(self):
        if not self._cargar_modelo():
            return

        interval        = 1.0 / self.cfg["fps_objetivo"]
        ultimo_t        = 0.0
        frames_sin_feed = 0

        while self.estado.corriendo:
            now = time.time()
            if now - ultimo_t < interval:
                time.sleep(0.01)
                continue
            ultimo_t = now

            # capturar frame del Tello
            frame = self._get_frame()
            if frame is None:
                frames_sin_feed += 1
                if frames_sin_feed == 30:
                    self.estado.log("[Vision] Sin feed de video (normal si --sin-vuelo)")
                time.sleep(0.05)
                continue
            frames_sin_feed = 0

            # detección
            personas = self._detectar(frame)

            # actualizar estado compartido
            with self.estado.lock:
                self.estado.personas = personas
                if self.estado.tracking_activo:
                    self._actualizar_sujeto(personas)
                else:
                    self.estado.sujeto = None
                correccion = self._calcular_correccion(
                    self.estado.sujeto if self.estado.tracking_activo else None
                )
                self.estado.correccion  = correccion
                frame = self._dibujar(frame, personas,
                                      self.estado.sujeto, correccion)
                self.estado.frame_actual = frame.copy()

            # mostrar
            cv2.imshow("Tello Director", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord('q'):
                self.estado.log("[Vision] Q presionado — terminando...")
                self.estado.corriendo = False
                break
            elif key == ord('r'):
                with self.estado.lock:
                    self.estado.sujeto = None
                self._frames_sin_sujeto = 0
                self.estado.log("[Vision] Sujeto reseteado")

        cv2.destroyAllWindows()
