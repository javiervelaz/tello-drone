"""
tello_scan.py
=============
Escaneo espiral del área usando movimientos relativos del DJI Tello.
Refactorizado de escaneo_area.py.

Diferencias respecto al original:
  - Sin GPS ni coordenadas NED absolutas
  - Movimientos relativos: move_forward + rotate_clockwise
  - Posición estimada por dead reckoning (solo para el log)
  - Espiral simplificada: avanzar → girar → repetir por anillos
"""

import time
import json
import math
from datetime import datetime


class EscaneoTello:
    """
    Escaneo en anillos concéntricos usando comandos relativos.

    Estrategia:
      1. Para cada anillo (1..ANILLOS):
         - Avanzar al radio del anillo
         - Recorrer el perímetro con 8 puntos (45° cada uno)
      2. Retornar al origen estimado
    """

    # Parámetros del escaneo
    ANILLOS          = 3      # cuántos anillos recorrer
    PASO_BASE_CM     = 70     # radio del primer anillo (cm)
    GRADOS_POR_PUNTO = 45     # 8 puntos por anillo (360 / 45 = 8)
    PAUSA_PASO       = 1.2    # segundos entre movimientos (da tiempo a estabilizar)
    PAUSA_PUNTO      = 0.8    # segundos de pausa en cada waypoint para filmar
    ALTURA_CM        = 100    # altura durante el escaneo

    def __init__(self, tello, estado):
        self.tello    = tello
        self.estado   = estado
        self._sim     = (tello is None)
        self.waypoints = []
        # dead reckoning (estimación de posición)
        self._norte_cm = 0.0
        self._este_cm  = 0.0
        self._yaw      = 0     # grados acumulados

    # ----------------------------------------------------------
    # Wrappers de movimiento con log
    # ----------------------------------------------------------
    def _move(self, fn: str, val: int):
        """Ejecuta un movimiento con manejo de errores y modo sim."""
        if self._sim:
            self.estado.log(f"  [sim] tello.{fn}({val})")
            time.sleep(0.2)
            return
        try:
            getattr(self.tello, fn)(val)
        except Exception as e:
            self.estado.log(f"  [!] Error en {fn}({val}): {e}")

    def _forward(self, cm: int):
        cm = max(20, min(cm, 300))
        self._move("move_forward", cm)
        # actualizar dead reckoning
        rad = math.radians(self._yaw)
        self._norte_cm += cm * math.cos(rad)
        self._este_cm  += cm * math.sin(rad)
        time.sleep(self.PAUSA_PASO)

    def _rotate_cw(self, deg: int):
        deg = max(1, min(deg, 360))
        self._move("rotate_clockwise", deg)
        self._yaw = (self._yaw + deg) % 360
        time.sleep(self.PAUSA_PASO)

    def _rotate_ccw(self, deg: int):
        deg = max(1, min(deg, 360))
        self._move("rotate_counter_clockwise", deg)
        self._yaw = (self._yaw - deg) % 360
        time.sleep(self.PAUSA_PASO)

    # ----------------------------------------------------------
    # Registro de waypoint
    # ----------------------------------------------------------
    def _registrar(self):
        wp = {
            "idx":      len(self.waypoints),
            "norte_cm": round(self._norte_cm, 1),
            "este_cm":  round(self._este_cm, 1),
            "yaw":      self._yaw,
            "ts":       datetime.now().strftime("%H:%M:%S"),
        }
        self.waypoints.append(wp)
        return wp

    # ----------------------------------------------------------
    # Flujo principal
    # ----------------------------------------------------------
    def ejecutar(self):
        self.estado.set_modo("escaneando")
        puntos_anillo = 360 // self.GRADOS_POR_PUNTO
        total = self.ANILLOS * puntos_anillo
        self.estado.log(
            f"Escaneo: {self.ANILLOS} anillos × {puntos_anillo} puntos = {total} waypoints"
        )

        # subir a altura de escaneo si es necesario
        alt_actual = self.estado.altura_tello or self.estado.altura_cm
        if alt_actual < self.ALTURA_CM - 20:
            diff = self.ALTURA_CM - alt_actual
            self.estado.log(f"Subiendo {diff}cm para escaneo...")
            self._move("move_up", min(int(diff), 300))
            self.estado.altura_cm = self.ALTURA_CM
            time.sleep(2.0)

        posicion_inicial = (self._norte_cm, self._este_cm, self._yaw)

        for anillo in range(1, self.ANILLOS + 1):
            radio_cm = self.PASO_BASE_CM * anillo
            self.estado.log(
                f"Anillo {anillo}/{self.ANILLOS} — radio ~{radio_cm}cm"
            )

            # avanzar al primer punto del anillo
            avance = radio_cm if anillo == 1 else self.PASO_BASE_CM
            self._forward(min(avance, 200))
            wp = self._registrar()
            self.estado.log(
                f"  Punto 1/{puntos_anillo} — "
                f"N:{wp['norte_cm']:.0f} E:{wp['este_cm']:.0f} yaw:{wp['yaw']}°"
            )
            time.sleep(self.PAUSA_PUNTO)

            # recorrer el anillo
            arco_cm = max(20, int(
                2 * math.pi * radio_cm / puntos_anillo
            ))

            for punto in range(2, puntos_anillo + 1):
                self._rotate_cw(self.GRADOS_POR_PUNTO)
                self._forward(min(arco_cm, 200))
                wp = self._registrar()
                self.estado.log(
                    f"  Punto {punto}/{puntos_anillo} — "
                    f"N:{wp['norte_cm']:.0f} E:{wp['este_cm']:.0f} yaw:{wp['yaw']}°"
                )
                time.sleep(self.PAUSA_PUNTO)

        # Retornar al origen estimado
        self._retornar(posicion_inicial)

        self._guardar_mapa()
        self.estado.set_modo("vuelo")
        self.estado.log(
            f"Escaneo completado — {len(self.waypoints)} waypoints registrados."
        )

    def _retornar(self, pos_inicial: tuple):
        """
        Retorno aproximado al origen por dead reckoning.
        Calcula ángulo y distancia hacia el punto de inicio estimado.
        """
        norte_i, este_i, yaw_i = pos_inicial
        dx = norte_i - self._norte_cm
        dy = este_i  - self._este_cm
        dist = math.sqrt(dx**2 + dy**2)

        if dist < 30:
            self.estado.log("Ya cerca del origen — sin retorno necesario.")
            return

        angulo_objetivo = math.degrees(math.atan2(dy, dx)) % 360
        diff_yaw = (angulo_objetivo - self._yaw + 360) % 360

        self.estado.log(
            f"Retornando al origen (dist estimada: {dist:.0f}cm, "
            f"rotación: {diff_yaw:.0f}°)..."
        )

        # rotar hacia el origen
        if diff_yaw <= 180:
            self._rotate_cw(int(diff_yaw))
        else:
            self._rotate_ccw(int(360 - diff_yaw))

        # avanzar en tramos de 200cm máximo
        restante = dist
        while restante > 25:
            tramo = min(restante, 200)
            self._forward(int(tramo))
            restante -= tramo

        # realinear al yaw original
        diff_final = (yaw_i - self._yaw + 360) % 360
        if diff_final > 5:
            if diff_final <= 180:
                self._rotate_cw(int(diff_final))
            else:
                self._rotate_ccw(int(360 - diff_final))

        self.estado.log("Retorno completado.")

    def _guardar_mapa(self):
        mapa = {
            "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dispositivo":    "dji_tello",
            "anillos":        self.ANILLOS,
            "paso_base_cm":   self.PASO_BASE_CM,
            "altura_cm":      self.ALTURA_CM,
            "puntos_anillo":  360 // self.GRADOS_POR_PUNTO,
            "total_waypoints": len(self.waypoints),
            "waypoints":      self.waypoints,
        }
        nombre = f"mapa_tello_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(nombre, "w") as f:
            json.dump(mapa, f, indent=2)
        self.estado.log(f"Mapa guardado: {nombre}")
        return nombre
