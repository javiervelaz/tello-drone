"""
tello_commands.py
=================
Parser de órdenes en español + ejecutor de comandos para DJI Tello.

Refactorizado de:
  - interprete_ordenes.py  (parser de reglas)
  - drone_director.py      (EjecutorComandos)
  - parser_llm.py          (ParserFallback)

Sin LLM externo — parser de reglas directo.
Compatible con Tello API (comandos relativos en cm, no NED).
"""

import time
import os
import cv2
import threading
from datetime import datetime


# ============================================================
# MAPA DE PALABRAS CLAVE → COMANDOS
# ============================================================
_KEYWORDS = {
    "despegar":     ["despegar", "despega", "volar", "vola", "arranca",
                     "levanta", "take off", "takeoff"],
    "aterrizar":    ["aterrizar", "aterriza", "tierra", "baja y aterriza",
                     "termina", "parar", "land"],
    "panoramica":   ["panoramica", "panorama", "360", "vuelta completa",
                     "vuelta", "girar", "girate"],
    "cenital":      ["cenital", "desde arriba", "plano cenital",
                     "arriba del todo", "bird", "cenital"],
    "norte":        ["norte", "adelante", "avanza", "forward"],
    "sur":          ["sur", "atras", "atrás", "retrocede", "back"],
    "este":         ["este", "derecha", "right"],
    "oeste":        ["oeste", "izquierda", "left"],
    "centro":       ["centro", "medio", "volver", "regresa", "hover",
                     "quedate", "quédate", "para"],
    "subir":        ["sube", "subir", "asciende", "más alto",
                     "mas alto", "up"],
    "bajar":        ["baja", "bajar", "descende", "más bajo",
                     "mas bajo", "down"],
    "seguir":       ["segui", "seguí", "sigue", "seguir",
                     "tracking", "enfoca", "apunta"],
    "dejar_seguir": ["deja de seguir", "dejar de seguir", "suelta",
                     "libre", "stop tracking", "no sigas"],
    "estado":       ["estado", "info", "posicion", "posición",
                     "bateria", "batería", "donde", "dónde"],
    "foto":         ["foto", "captura", "screenshot", "saca"],
    "escanear":     ["escanea", "escaneá", "reconocer", "mapear",
                     "scan", "recorrer"],
    "flip":         ["flip", "vuelta mortal", "pirueta"],
}


# ============================================================
# PARSER
# ============================================================
class ParserOrdenes:
    """
    Parser de reglas. Igual al ParserFallback de parser_llm.py
    pero sin dependencia de Ollama.
    """

    def parsear(self, texto: str) -> dict:
        t = texto.lower().strip()
        for cmd, kws in _KEYWORDS.items():
            for kw in kws:
                if kw in t:
                    return {
                        "comando": cmd,
                        "texto":   texto,
                        "params":  self._params(t, cmd),
                    }
        return {"comando": "desconocido", "texto": texto, "params": {}}

    def _params(self, texto: str, comando: str) -> dict:
        params = {}
        # buscar número (metros o grados)
        for word in texto.split():
            word = word.replace(",", ".")
            try:
                params["numero"] = float(word)
                break
            except ValueError:
                pass
        # buscar color para modo seguir
        if comando == "seguir":
            for color in ["roja", "rojo", "azul", "verde", "blanca", "blanco",
                          "negro", "negra", "amarilla", "amarillo", "naranja"]:
                if color in texto:
                    params["color"] = color
                    break
        return params


# ============================================================
# EJECUTOR — TELLO
# ============================================================
class EjecutorTello:
    """
    Convierte órdenes parseadas en comandos concretos del DJI Tello.

    Diferencias con EjecutorComandos de drone_director.py:
      - Sin coordenadas NED absolutas → movimientos relativos en cm
      - Sin offboard/MAVSDK → API djitellopy directa
      - Sin A* → movimientos simples sin mapa de obstáculos
    """

    # Constantes de movimiento (ajustables)
    PASO_CARDINAL_CM = 80      # cm por movimiento norte/sur/este/oeste
    ALTURA_CRUCERO_CM = 100    # altura de crucero tras despegue
    ALTURA_CENITAL_CM = 200    # altura para toma cenital (Tello max ~300cm)
    GRADOS_PANORAMICA = 30     # grados por paso en panorámica (12 pasos = 360°)
    PAUSA_PANORAMICA  = 0.8    # segundos entre cada paso de panorámica

    def __init__(self, tello, estado):
        self.tello   = tello
        self.estado  = estado
        self._sim    = (tello is None)  # True = modo sin vuelo

    # ----------------------------------------------------------
    # Dispatcher principal
    # ----------------------------------------------------------
    def ejecutar(self, orden: dict) -> bool:
        """
        Ejecuta la orden y retorna:
          True  = seguir en el loop
          False = salir del loop (aterrizar)
        """
        cmd    = orden["comando"]
        params = orden["params"]
        self.estado.ultima_orden = orden["texto"]

        acciones = {
            "despegar":     self._despegar,
            "aterrizar":    self._aterrizar,
            "panoramica":   self._panoramica,
            "cenital":      self._cenital,
            "norte":        lambda p: self._cardinal("norte",  p),
            "sur":          lambda p: self._cardinal("sur",    p),
            "este":         lambda p: self._cardinal("este",   p),
            "oeste":        lambda p: self._cardinal("oeste",  p),
            "centro":       self._centro,
            "subir":        self._subir,
            "bajar":        self._bajar,
            "seguir":       self._seguir,
            "dejar_seguir": self._dejar_seguir,
            "estado":       self._estado,
            "foto":         self._foto,
            "escanear":     self._escanear,
            "flip":         self._flip,
            "desconocido":  self._desconocido,
        }

        fn = acciones.get(cmd, self._desconocido)
        return fn(params)

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------
    def _cmd(self, fn_nombre: str, *args) -> bool:
        """Ejecuta un método del Tello con manejo de errores."""
        if self._sim:
            self.estado.log(f"  [sim] tello.{fn_nombre}({', '.join(str(a) for a in args)})")
            time.sleep(0.3)
            return True
        try:
            getattr(self.tello, fn_nombre)(*args)
            return True
        except Exception as e:
            self.estado.log(f"  [!] {fn_nombre}: {e}")
            return False

    def _necesita_vuelo(self) -> bool:
        if not self.estado.en_vuelo and not self._sim:
            self.estado.log("Primero despegá.")
            return False
        return True

    def _cm_desde_metros(self, params: dict, default_m: float) -> int:
        metros = params.get("numero", default_m)
        cm = int(float(metros) * 100)
        return max(20, min(cm, 300))   # límites físicos del Tello

    # ----------------------------------------------------------
    # Comandos
    # ----------------------------------------------------------
    def _despegar(self, params: dict) -> bool:
        if self.estado.en_vuelo:
            self.estado.log("Ya está en vuelo.")
            return True
        self.estado.log("Despegando...")
        self.estado.set_modo("vuelo")
        if self._cmd("takeoff"):
            self.estado.en_vuelo  = True
            self.estado.altura_cm = self.ALTURA_CRUCERO_CM
            time.sleep(1.5)
            self.estado.log(f"En vuelo ✓  (~{self.ALTURA_CRUCERO_CM}cm)")
        return True

    def _aterrizar(self, params: dict) -> bool:
        self.estado.log("Aterrizando...")
        with self.estado.lock:
            self.estado.tracking_activo = False
            self.estado.sujeto = None
        self.estado.set_modo("tierra")
        self._cmd("land")
        self.estado.en_vuelo = False
        self.estado.log("En tierra ✓")
        return False   # señal para salir del loop principal

    def _panoramica(self, params: dict) -> bool:
        if not self._necesita_vuelo():
            return True
        self.estado.set_modo("escaneando")
        pasos = 360 // self.GRADOS_PANORAMICA
        self.estado.log(f"Panorámica 360° — {pasos} pasos de {self.GRADOS_PANORAMICA}°...")
        for i in range(pasos):
            self._cmd("rotate_clockwise", self.GRADOS_PANORAMICA)
            with self.estado.lock:
                self.estado.yaw_acumulado = (
                    self.estado.yaw_acumulado + self.GRADOS_PANORAMICA) % 360
            time.sleep(self.PAUSA_PANORAMICA)
            self.estado.log(f"  Paso {i+1}/{pasos} — yaw: {self.estado.yaw_acumulado}°")
        self.estado.set_modo("vuelo")
        self.estado.log("Panorámica completada.")
        return True

    def _cenital(self, params: dict) -> bool:
        if not self._necesita_vuelo():
            return True
        diff_cm = self.ALTURA_CENITAL_CM - self.estado.altura_cm
        if diff_cm > 20:
            self.estado.log(f"Subiendo a posición cenital ({self.ALTURA_CENITAL_CM}cm)...")
            self._cmd("move_up", min(int(diff_cm), 300))
            self.estado.altura_cm = self.ALTURA_CENITAL_CM
            time.sleep(2.0)
        self.estado.set_modo("vuelo")
        self.estado.log("Toma cenital activa.")
        return True

    def _cardinal(self, direccion: str, params: dict) -> bool:
        if not self._necesita_vuelo():
            return True
        cm = self._cm_desde_metros(params, default_m=0.8)
        mapa = {
            "norte": "move_forward",
            "sur":   "move_back",
            "este":  "move_right",
            "oeste": "move_left",
        }
        self.estado.log(f"Moviendo {direccion} {cm}cm...")
        self._cmd(mapa[direccion], cm)
        return True

    def _centro(self, params: dict) -> bool:
        # hover: detener tracking y mantener posición
        with self.estado.lock:
            self.estado.tracking_activo = False
            self.estado.sujeto = None
        self.estado.set_modo("vuelo")
        self.estado.log("Hover — manteniendo posición.")
        return True

    def _subir(self, params: dict) -> bool:
        if not self._necesita_vuelo():
            return True
        cm = self._cm_desde_metros(params, default_m=0.5)
        self.estado.log(f"Subiendo {cm}cm...")
        self._cmd("move_up", cm)
        self.estado.altura_cm += cm
        return True

    def _bajar(self, params: dict) -> bool:
        if not self._necesita_vuelo():
            return True
        cm = self._cm_desde_metros(params, default_m=0.5)
        self.estado.log(f"Bajando {cm}cm...")
        self._cmd("move_down", cm)
        self.estado.altura_cm = max(30, self.estado.altura_cm - cm)
        return True

    def _seguir(self, params: dict) -> bool:
        color = params.get("color", "")
        desc  = f" — buscando persona {color}" if color else ""
        self.estado.set_modo("tracking")
        with self.estado.lock:
            self.estado.tracking_activo = True
            self.estado.sujeto          = None
        self.estado.log(f"Tracking activado{desc}.")
        return True

    def _dejar_seguir(self, params: dict) -> bool:
        with self.estado.lock:
            self.estado.tracking_activo = False
            self.estado.sujeto          = None
        self.estado.set_modo("vuelo")
        self.estado.log("Tracking desactivado.")
        return True

    def _estado(self, params: dict) -> bool:
        bat    = self.estado.bateria
        alt    = self.estado.altura_tello or self.estado.altura_cm
        modo   = self.estado.modo
        pers   = len(self.estado.personas)
        track  = "ON" if self.estado.tracking_activo else "OFF"
        vuelo  = "sí" if self.estado.en_vuelo else "no"
        self.estado.log(
            f"En vuelo: {vuelo} | Modo: {modo} | Batería: {bat}% | "
            f"Altura: {alt}cm | Personas: {pers} | Tracking: {track}"
        )
        return True

    def _foto(self, params: dict) -> bool:
        frame = self.estado.frame_actual
        if frame is None:
            self.estado.log("Sin frame disponible (¿stream activo?).")
            return True
        os.makedirs("fotos", exist_ok=True)
        nombre = f"fotos/foto_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(nombre, frame)
        self.estado.log(f"Foto guardada: {nombre}")
        return True

    def _escanear(self, params: dict) -> bool:
        if not self._necesita_vuelo():
            return True
        from tello_scan import EscaneoTello
        scanner = EscaneoTello(self.tello, self.estado)
        # corre en thread para no bloquear el loop de órdenes
        t = threading.Thread(target=scanner.ejecutar, daemon=True)
        t.start()
        self.estado.log("Escaneo iniciado en background. Esperá la confirmación.")
        return True

    def _flip(self, params: dict) -> bool:
        if not self._necesita_vuelo():
            return True
        bat = self.estado.bateria
        if bat < 50:
            self.estado.log(f"Batería insuficiente para flip ({bat}% < 50%).")
            return True
        self.estado.log("Flip adelante!")
        self._cmd("flip_forward")
        return True

    def _desconocido(self, params: dict = None) -> bool:
        self.estado.log(f"No entendí: '{self.estado.ultima_orden}'")
        self.estado.log("Comandos: despegar | aterrizar | panoramica | cenital | "
                        "norte/sur/este/oeste | centro | sube/baja N | "
                        "seguir | dejar seguir | estado | foto | flip | escanear")
        return True
