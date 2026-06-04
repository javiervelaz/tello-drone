"""
tello_web.py
============
Interfaz web para Tello Director MVP.
Reemplaza el input() de consola con botones en el browser.

Uso:
    venv/Scripts/python tello_director.py --web

Abre en el browser:
    http://localhost:5000
"""

import threading
import time
import queue
import cv2

from flask import Flask, Response, jsonify, request, render_template_string

# ============================================================
# HTML — interfaz completa embebida
# ============================================================
HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tello Director</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f0f; color: #e0e0e0; font-family: -apple-system, sans-serif; }

  /* Layout */
  .app { display: grid; grid-template-columns: 1fr 320px; height: 100vh; gap: 0; }
  .left  { display: flex; flex-direction: column; padding: 12px; gap: 10px; }
  .right { background: #1a1a1a; border-left: 1px solid #2a2a2a;
           display: flex; flex-direction: column; padding: 14px; gap: 12px;
           overflow-y: auto; }

  /* Video */
  .video-wrap { flex: 1; background: #000; border-radius: 8px; overflow: hidden;
                display: flex; align-items: center; justify-content: center; }
  .video-wrap img { width: 100%; height: 100%; object-fit: contain; }
  .no-video { color: #555; font-size: 14px; text-align: center; }

  /* Status bar */
  .status-bar { display: flex; gap: 8px; flex-wrap: wrap; }
  .stat { background: #1e1e1e; border: 1px solid #2a2a2a; border-radius: 6px;
          padding: 6px 12px; font-size: 12px; display: flex; align-items: center; gap: 6px; }
  .stat .val { font-weight: 600; font-size: 14px; }
  .stat.warn .val { color: #f59e0b; }
  .stat.ok   .val { color: #22c55e; }
  .stat.info .val { color: #60a5fa; }
  .stat.track .val { color: #f97316; }

  /* Modo badge */
  .modo-badge { display: inline-block; padding: 2px 10px; border-radius: 99px;
                font-size: 11px; font-weight: 600; text-transform: uppercase;
                letter-spacing: .05em; }
  .modo-tierra    { background: #27272a; color: #71717a; }
  .modo-vuelo     { background: #14532d; color: #86efac; }
  .modo-tracking  { background: #431407; color: #fdba74; }
  .modo-escaneando{ background: #1e3a5f; color: #93c5fd; }

  /* Última orden */
  .ultima-orden { background: #1e1e1e; border: 1px solid #2a2a2a; border-radius: 6px;
                  padding: 8px 12px; font-size: 12px; color: #888; min-height: 32px; }
  .ultima-orden span { color: #a78bfa; }

  /* Secciones del panel derecho */
  .section-title { font-size: 10px; font-weight: 600; text-transform: uppercase;
                   letter-spacing: .08em; color: #555; margin-bottom: 4px; }

  /* Botones */
  .btn-grid { display: grid; gap: 6px; }
  .btn-grid-2 { grid-template-columns: 1fr 1fr; }
  .btn-grid-3 { grid-template-columns: 1fr 1fr 1fr; }
  .btn-grid-4 { grid-template-columns: 1fr 1fr 1fr 1fr; }

  .btn { padding: 9px 6px; border: 1px solid #2a2a2a; border-radius: 6px;
         background: #1e1e1e; color: #e0e0e0; font-size: 12px; font-weight: 500;
         cursor: pointer; transition: all .12s; text-align: center; white-space: nowrap; }
  .btn:hover  { background: #2a2a2a; border-color: #444; }
  .btn:active { transform: scale(.97); }

  /* Colores de botones */
  .btn-green  { border-color: #166534; color: #86efac; }
  .btn-green:hover { background: #14532d; }
  .btn-red    { border-color: #7f1d1d; color: #fca5a5; }
  .btn-red:hover { background: #450a0a; }
  .btn-blue   { border-color: #1e3a5f; color: #93c5fd; }
  .btn-blue:hover { background: #172554; }
  .btn-orange { border-color: #431407; color: #fdba74; }
  .btn-orange:hover { background: #2c0a00; }
  .btn-purple { border-color: #3b0764; color: #d8b4fe; }
  .btn-purple:hover { background: #2e1065; }

  /* Dirección pad */
  .dpad { display: grid; grid-template-columns: 1fr 1fr 1fr;
          grid-template-rows: 1fr 1fr 1fr; gap: 5px; }
  .dpad .btn { padding: 12px 0; font-size: 16px; }
  .dpad .center { grid-column: 2; grid-row: 2; }
  .dpad .norte  { grid-column: 2; grid-row: 1; }
  .dpad .sur    { grid-column: 2; grid-row: 3; }
  .dpad .oeste  { grid-column: 1; grid-row: 2; }
  .dpad .este   { grid-column: 3; grid-row: 2; }

  /* Input de texto */
  .cmd-input-wrap { display: flex; gap: 6px; }
  .cmd-input { flex: 1; background: #1e1e1e; border: 1px solid #2a2a2a; border-radius: 6px;
               color: #e0e0e0; padding: 8px 10px; font-size: 13px; outline: none; }
  .cmd-input:focus { border-color: #555; }
  .cmd-send { padding: 8px 14px; background: #2a2a2a; border: 1px solid #444;
              border-radius: 6px; color: #e0e0e0; cursor: pointer; font-size: 13px; }
  .cmd-send:hover { background: #333; }

  /* Feedback flash */
  .flash { position: fixed; top: 12px; left: 50%; transform: translateX(-50%);
           background: #22c55e; color: #fff; padding: 8px 20px; border-radius: 6px;
           font-size: 13px; font-weight: 500; opacity: 0;
           transition: opacity .2s; pointer-events: none; z-index: 100; }
  .flash.show { opacity: 1; }

  /* Responsive — pantalla angosta */
  @media (max-width: 700px) {
    .app { grid-template-columns: 1fr; grid-template-rows: auto 1fr; height: auto; }
    .right { border-left: none; border-top: 1px solid #2a2a2a; }
  }
</style>
</head>
<body>
<div class="app">

  <!-- IZQUIERDA: video + status -->
  <div class="left">
    <div class="video-wrap">
      <img id="video-feed" src="/video"
           onerror="this.style.display='none'; document.getElementById('no-video').style.display='block'">
      <p id="no-video" class="no-video" style="display:none">Sin feed de video</p>
    </div>

    <div class="status-bar" id="status-bar">
      <div class="stat" id="s-bat">
        <span>🔋</span>
        <span class="val" id="v-bat">--%</span>
      </div>
      <div class="stat" id="s-alt">
        <span>📏</span>
        <span class="val" id="v-alt">--cm</span>
      </div>
      <div class="stat">
        <span>👤</span>
        <span class="val" id="v-per">0</span>
      </div>
      <div class="stat">
        <span>Modo:</span>
        <span class="val"><span class="modo-badge modo-tierra" id="v-modo">tierra</span></span>
      </div>
      <div class="stat" id="s-track" style="display:none">
        <span class="val" style="color:#f97316">● TRACKING</span>
      </div>
    </div>

    <div class="ultima-orden">
      Última orden: <span id="v-orden">—</span>
    </div>
  </div>

  <!-- DERECHA: controles -->
  <div class="right">

    <!-- Vuelo principal -->
    <div>
      <div class="section-title">Vuelo</div>
      <div class="btn-grid btn-grid-2">
        <button class="btn btn-green" onclick="cmd('despegar')">🛫 Despegar</button>
        <button class="btn btn-red"   onclick="cmd('aterrizar')">🛬 Aterrizar</button>
      </div>
    </div>

    <!-- Dirección -->
    <div>
      <div class="section-title">Dirección</div>
      <div class="dpad">
        <button class="btn norte"  onclick="cmd('norte')">▲</button>
        <button class="btn sur"    onclick="cmd('sur')">▼</button>
        <button class="btn oeste"  onclick="cmd('oeste')">◀</button>
        <button class="btn este"   onclick="cmd('este')">▶</button>
        <button class="btn center btn-blue" onclick="cmd('centro')">⏹</button>
      </div>
    </div>

    <!-- Altura -->
    <div>
      <div class="section-title">Altura</div>
      <div class="btn-grid btn-grid-3">
        <button class="btn" onclick="cmd('sube 1')">▲ +1m</button>
        <button class="btn btn-blue" onclick="cmd('cenital')">⬆ Cenital</button>
        <button class="btn" onclick="cmd('baja 1')">▼ -1m</button>
      </div>
    </div>

    <!-- Cámara / filmación -->
    <div>
      <div class="section-title">Filmación</div>
      <div class="btn-grid btn-grid-2">
        <button class="btn btn-purple" onclick="cmd('panoramica')">🔄 Panorámica</button>
        <button class="btn btn-orange" onclick="cmd('seguir')">👁 Seguir</button>
        <button class="btn" onclick="cmd('dejar seguir')">✖ Dejar seguir</button>
        <button class="btn" onclick="cmd('foto')">📷 Foto</button>
      </div>
    </div>

    <!-- Extra -->
    <div>
      <div class="section-title">Extra</div>
      <div class="btn-grid btn-grid-2">
        <button class="btn" onclick="cmd('flip')">🤸 Flip</button>
        <button class="btn" onclick="cmd('estado')">ℹ Estado</button>
        <button class="btn" onclick="cmd('escanear')">🗺 Escanear</button>
      </div>
    </div>

    <!-- Comando libre -->
    <div>
      <div class="section-title">Comando libre</div>
      <div class="cmd-input-wrap">
        <input class="cmd-input" id="cmd-text" type="text"
               placeholder="ej: sube 2, seguir rojo..."
               onkeydown="if(event.key==='Enter') enviarTexto()">
        <button class="cmd-send" onclick="enviarTexto()">▶</button>
      </div>
    </div>

  </div>
</div>

<div class="flash" id="flash"></div>

<script>
  function cmd(texto) {
    fetch('/comando', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({texto})
    });
    flash(texto);
  }

  function enviarTexto() {
    const input = document.getElementById('cmd-text');
    const texto = input.value.trim();
    if (!texto) return;
    cmd(texto);
    input.value = '';
  }

  function flash(texto) {
    const el = document.getElementById('flash');
    el.textContent = texto;
    el.classList.add('show');
    setTimeout(() => el.classList.remove('show'), 800);
  }

  // Polling de estado cada segundo
  function actualizarStatus() {
    fetch('/status')
      .then(r => r.json())
      .then(d => {
        // batería
        const bat = d.bateria;
        document.getElementById('v-bat').textContent = bat + '%';
        const sBat = document.getElementById('s-bat');
        sBat.className = 'stat ' + (bat < 20 ? 'warn' : bat > 50 ? 'ok' : '');

        // altura
        document.getElementById('v-alt').textContent = d.altura + 'cm';

        // personas
        document.getElementById('v-per').textContent = d.personas;

        // modo
        const modoEl = document.getElementById('v-modo');
        modoEl.textContent = d.modo;
        modoEl.className = 'modo-badge modo-' + d.modo;

        // tracking
        document.getElementById('s-track').style.display =
          d.tracking ? 'flex' : 'none';

        // última orden
        document.getElementById('v-orden').textContent =
          d.ultima_orden || '—';
      })
      .catch(() => {});
  }

  setInterval(actualizarStatus, 1000);
  actualizarStatus();
</script>
</body>
</html>
"""


# ============================================================
# SERVIDOR FLASK
# ============================================================
class ServidorWeb:
    def __init__(self, estado, cmd_queue, port=5000):
        self.estado    = estado
        self.cmd_queue = cmd_queue
        self.port      = port
        self.app       = Flask(__name__)
        self._registrar_rutas()

    def _registrar_rutas(self):
        estado    = self.estado
        cmd_queue = self.cmd_queue
        app       = self.app

        @app.route("/")
        def index():
            return render_template_string(HTML)

        @app.route("/video")
        def video():
            def generar():
                while True:
                    frame = estado.frame_actual
                    if frame is not None:
                        ok, jpg = cv2.imencode(
                            ".jpg", frame,
                            [cv2.IMWRITE_JPEG_QUALITY, 70]
                        )
                        if ok:
                            yield (
                                b"--frame\r\n"
                                b"Content-Type: image/jpeg\r\n\r\n"
                                + jpg.tobytes()
                                + b"\r\n"
                            )
                    time.sleep(1 / 15)
            return Response(
                generar(),
                mimetype="multipart/x-mixed-replace; boundary=frame"
            )

        @app.route("/status")
        def status():
            return jsonify({
                "bateria":      estado.bateria,
                "altura":       estado.altura_tello or estado.altura_cm,
                "modo":         estado.modo,
                "personas":     len(estado.personas),
                "tracking":     estado.tracking_activo,
                "en_vuelo":     estado.en_vuelo,
                "ultima_orden": estado.ultima_orden,
            })

        @app.route("/comando", methods=["POST"])
        def comando():
            data  = request.get_json(silent=True) or {}
            texto = data.get("texto", "").strip()
            if texto:
                cmd_queue.put(texto)
            return jsonify({"ok": True})

    def iniciar(self):
        """Arranca Flask en un thread daemon."""
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)   # silenciar logs de cada request

        t = threading.Thread(
            target=lambda: self.app.run(
                host="0.0.0.0",
                port=self.port,
                debug=False,
                use_reloader=False,
            ),
            daemon=True,
            name="flask",
        )
        t.start()
        print(f"[Web] Interfaz lista → http://localhost:{self.port}")
