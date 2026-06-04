"""
tello_recorder.py
=================
Módulo de grabación adaptado para DJI Tello.
Refactorizado de grabacion.py.

Graba en background:
  - VIDEO  → grabaciones/tello_YYYYMMDD_HHMMSS.mp4
  - LOG    → grabaciones/tello_YYYYMMDD_HHMMSS.json

El grabador toma frames de estado.frame_actual (ya con HUD de visión),
así el video incluye las cajas de detección y la retícula.
"""

import cv2
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path


# ============================================================
# CONFIGURACIÓN
# ============================================================
CONFIG = {
    "fps":        15,
    "resolucion": (640, 480),
    "codec":      "mp4v",       # H.264 si disponible; mp4v como fallback
    "directorio": "grabaciones",
}


# ============================================================
# GRABADOR
# ============================================================
class GrabadorTello:
    """
    Graba frames del estado en background sin bloquear el loop principal.
    """

    def __init__(self, estado, config: dict = None):
        self.estado  = estado
        self.cfg     = config or CONFIG
        self.activo  = False
        self.writer  = None
        self.sesion_id  = ""
        self.ruta_video = ""
        self.ruta_log   = ""
        self.frames     = 0
        self.t_inicio   = 0.0
        self._lock      = threading.Lock()
        self._log       = {"eventos": [], "telemetria": []}
        self._t_grab    = None

    # ----------------------------------------------------------
    # Iniciar sesión
    # ----------------------------------------------------------
    def iniciar(self):
        self.sesion_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.t_inicio  = time.time()
        Path(self.cfg["directorio"]).mkdir(exist_ok=True)

        self.ruta_video = os.path.join(
            self.cfg["directorio"],
            f"tello_{self.sesion_id}.mp4",
        )
        self.ruta_log = os.path.join(
            self.cfg["directorio"],
            f"tello_{self.sesion_id}.json",
        )

        # intentar codec H.264, fallback mp4v
        #for codec in ["avc1", "mp4v", "XVID"]:
        for codec in ["mp4v", "XVID", "avc1"]:
            fourcc = cv2.VideoWriter_fourcc(*codec)
            writer = cv2.VideoWriter(
                self.ruta_video, fourcc,
                self.cfg["fps"], self.cfg["resolucion"],
            )
            if writer.isOpened():
                self.writer = writer
                break
            writer.release()

        if self.writer is None or not self.writer.isOpened():
            print("[Grabacion] ⚠ No se pudo abrir VideoWriter — grabación desactivada")
            return

        self._log = {
            "sesion_id":  self.sesion_id,
            "inicio":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dispositivo": "dji_tello",
            "fps_config": self.cfg["fps"],
            "eventos":    [],
            "telemetria": [],
        }
        self.activo = True
        self.frames = 0

        # thread de grabación (daemon — se cierra con el proceso)
        self._t_grab = threading.Thread(
            target=self._grab_loop, daemon=True, name="grabador")
        self._t_grab.start()

        print(f"[Grabacion] Sesión: {self.sesion_id}")
        print(f"[Grabacion] Video:  {self.ruta_video}")
        print(f"[Grabacion] Log:    {self.ruta_log}")

    # ----------------------------------------------------------
    # Loop de grabación (corre en thread separado)
    # ----------------------------------------------------------
    def _grab_loop(self):
        interval   = 1.0 / self.cfg["fps"]
        w_t, h_t   = self.cfg["resolucion"]
        telem_cada = int(self.cfg["fps"] * 2)  # telemetría cada ~2 segundos

        while self.activo:
            t0 = time.time()

            frame = self.estado.frame_actual
            if frame is not None and self.writer is not None:
                # asegurar resolución correcta
                if frame.shape[1] != w_t or frame.shape[0] != h_t:
                    frame = cv2.resize(frame, self.cfg["resolucion"])

                with self._lock:
                    self.writer.write(frame)
                    self.frames += 1

                # telemetría periódica
                if self.frames % telem_cada == 0:
                    self._log["telemetria"].append({
                        "ts":       datetime.now().strftime("%H:%M:%S"),
                        "bateria":  self.estado.bateria,
                        "alt_cm":   self.estado.altura_tello or self.estado.altura_cm,
                        "modo":     self.estado.modo,
                        "personas": len(self.estado.personas),
                        "tracking": self.estado.tracking_activo,
                    })

            elapsed = time.time() - t0
            sleep_t = interval - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

    # ----------------------------------------------------------
    # Registro de eventos
    # ----------------------------------------------------------
    def registrar_orden(self, texto: str):
        self._log["eventos"].append({
            "ts":    datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "tipo":  "orden",
            "texto": texto,
        })

    def registrar_evento(self, tipo: str, detalle: str):
        self._log["eventos"].append({
            "ts":     datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "tipo":   tipo,
            "detalle": detalle,
        })

    # ----------------------------------------------------------
    # Finalizar sesión
    # ----------------------------------------------------------
    def finalizar(self):
        if not self.activo:
            return None
        self.activo = False
        time.sleep(0.4)   # dar tiempo al loop a cerrar el último frame

        elapsed = time.time() - self.t_inicio

        with self._lock:
            if self.writer is not None:
                self.writer.release()
                self.writer = None

        self._log["fin"]          = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log["duracion_seg"] = round(elapsed, 1)
        self._log["total_frames"] = self.frames
        self._log["fps_real"]     = round(self.frames / elapsed, 1) if elapsed > 0 else 0

        with open(self.ruta_log, "w", encoding="utf-8") as f:
            json.dump(self._log, f, indent=2, ensure_ascii=False)

        mins = int(elapsed // 60)
        segs = int(elapsed % 60)
        fps_real = self._log["fps_real"]

        print(f"\n[Grabacion] Sesión finalizada.")
        print(f"[Grabacion] Duración:    {mins:02d}:{segs:02d}  ({self.frames} frames, {fps_real} fps)")
        print(f"[Grabacion] Video:       {self.ruta_video}")
        print(f"[Grabacion] Log:         {self.ruta_log}")
        print(f"[Grabacion] Eventos:     {len(self._log['eventos'])}")
        print(f"[Grabacion] Telemetría:  {len(self._log['telemetria'])} registros")

        return {
            "video":       self.ruta_video,
            "log":         self.ruta_log,
            "duracion_seg": round(elapsed, 1),
            "total_frames": self.frames,
        }
