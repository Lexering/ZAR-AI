import threading
from flask import Flask, render_template_string, request, jsonify, send_from_directory
from flask_socketio import SocketIO
import json
import os

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

_jarvis_ref = None

def set_jarvis(jarvis):
    global _jarvis_ref
    _jarvis_ref = jarvis

HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="ZAR">
    <meta name="theme-color" content="#00060a">
    <link rel="apple-touch-icon" href="/icon.png">
    <link rel="manifest" href="/manifest.json">
    <title>ZAR</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        body {
            background: #00060a;
            color: #00ffff;
            font-family: 'Courier New', monospace;
            height: 100dvh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        header {
            text-align: center;
            padding: 16px;
            border-bottom: 1px solid #00ffff33;
            letter-spacing: 6px;
            font-size: 1.1em;
            background: #010d14;
            flex-shrink: 0;
        }
        #status {
            text-align: center;
            font-size: 0.7em;
            padding: 5px;
            color: #00ffff88;
            letter-spacing: 3px;
            background: #010d14;
            flex-shrink: 0;
        }
        #log {
            flex: 1;
            overflow-y: auto;
            padding: 15px;
            font-size: 0.85em;
            line-height: 1.9;
            -webkit-overflow-scrolling: touch;
        }
        #log .you { color: #ffffff; }
        #log .alberto { color: #00ffff; }
        #log .sys { color: #ffff00; font-size: 0.75em; }
        .input-area {
            display: flex;
            gap: 8px;
            padding: 12px;
            border-top: 1px solid #00ffff33;
            background: #010d14;
            flex-shrink: 0;
            padding-bottom: max(12px, env(safe-area-inset-bottom));
        }
        #msg {
            flex: 1;
            background: #0d1117;
            border: 1px solid #00ffff55;
            border-radius: 20px;
            padding: 10px 16px;
            color: #fff;
            font-size: 1em;
            outline: none;
        }
        #msg:focus { border-color: #00ffff; }
        .btn {
            background: transparent;
            border: 1px solid #00ffff;
            color: #00ffff;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            font-size: 1.2em;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        .btn:active { background: #00ffff22; }
        .btn.recording { background: #ff336622; border-color: #ff3366; color: #ff3366; animation: pulse 1s infinite; }
        @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.5 } }
        #install-btn {
            display: none;
            margin: 8px auto;
            background: #00ffff22;
            border: 1px solid #00ffff;
            color: #00ffff;
            padding: 8px 20px;
            border-radius: 20px;
            font-size: 0.8em;
            cursor: pointer;
            letter-spacing: 2px;
        }
    </style>
</head>
<body>
    <header>ZAR</header>
    <div id="status">● CONECTADO</div>
    <button id="install-btn" onclick="installApp()">⬇ INSTALAR APP</button>
    <div id="log"></div>
    <div class="input-area">
        <input id="msg" type="text" placeholder="Escribe un comando..." autocomplete="off" autocorrect="off"/>
        <button class="btn" onclick="send()">➤</button>
        <button class="btn" id="voice-btn" onclick="toggleVoice()">🎤</button>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <script>
        const socket = io();
        const log = document.getElementById('log');
        let isRecording = false;
        let recognition = null;
        let deferredPrompt = null;

        // PWA install
        window.addEventListener('beforeinstallprompt', e => {
            e.preventDefault();
            deferredPrompt = e;
            document.getElementById('install-btn').style.display = 'block';
        });

        function installApp() {
            if (deferredPrompt) {
                deferredPrompt.prompt();
                deferredPrompt.userChoice.then(() => {
                    document.getElementById('install-btn').style.display = 'none';
                });
            }
        }

        // Service Worker
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js');
        }

        socket.on('connect', () => {
            document.getElementById('status').textContent = '● CONECTADO';
            document.getElementById('status').style.color = '#00ff8888';
        });

        socket.on('disconnect', () => {
            document.getElementById('status').textContent = '○ DESCONECTADO';
            document.getElementById('status').style.color = '#ff336688';
        });

        function addLog(text, cls) {
            const d = document.createElement('div');
            d.className = cls;
            d.textContent = text;
            log.appendChild(d);
            log.scrollTop = log.scrollHeight;
        }

        socket.on('log', data => {
            const cls = data.startsWith('You:') ? 'you' : data.startsWith('ZAR:') ? 'alberto' : 'sys';
            addLog(data, cls);
        });

        function send() {
    const input = document.getElementById('msg');
    const text = input.value.trim();
    if (!text) return;
    addLog('You: ' + text, 'you');
    fetch('/command', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text})
    }).then(r => r.json()).then(d => {
        if (!d.ok) addLog('SYS: ' + (d.error || 'Error'), 'sys');
    });
    input.value = '';
}

        document.getElementById('msg').addEventListener('keydown', e => {
            if (e.key === 'Enter') send();
        });

        function toggleVoice() {
            if (isRecording) {
                stopVoice();
            } else {
                startVoice();
            }
        }

        function startVoice() {
            if (!('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {
                alert('Usa Safari en iPhone para reconocimiento de voz.');
                return;
            }
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            recognition = new SR();
            recognition.lang = 'es-ES';
            recognition.continuous = false;
            recognition.interimResults = false;
            recognition.onresult = e => {
                const text = e.results[0][0].transcript;
                addLog('You: ' + text, 'you');
                fetch('/command', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text})
                });
                stopVoice();
            };
            recognition.onerror = () => stopVoice();
            recognition.onend = () => stopVoice();
            recognition.start();
            isRecording = true;
            document.getElementById('voice-btn').classList.add('recording');
            document.getElementById('status').textContent = '🎤 ESCUCHANDO...';
        }

        function stopVoice() {
            if (recognition) recognition.stop();
            isRecording = false;
            document.getElementById('voice-btn').classList.remove('recording');
            document.getElementById('status').textContent = '● CONECTADO';
        }
    </script>
</body>
</html>
"""

MANIFEST = {
    "name": "ZAR",
    "short_name": "ZAR",
    "description": "Tu asistente personal con IA",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#00060a",
    "theme_color": "#00060a",
    "orientation": "portrait",
    "icons": [
        {"src": "/icon.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon.png", "sizes": "512x512", "type": "image/png"}
    ]
}

SW = """
const CACHE = 'alberto-v1';
const ASSETS = ['/'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS))));
self.addEventListener('fetch', e => e.respondWith(fetch(e.request).catch(() => caches.match(e.request))));
"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/manifest.json")
def manifest():
    return jsonify(MANIFEST)

@app.route("/sw.js")
def service_worker():
    from flask import Response
    return Response(SW, mimetype="application/javascript")

@app.route("/icon.png")
def icon():
    icon_path = os.path.join(os.path.dirname(__file__), "face.png")
    if os.path.exists(icon_path):
        return send_from_directory(os.path.dirname(__file__), "face.png")
    from flask import Response
    return Response(b"", mimetype="image/png")

@app.route("/command", methods=["POST"])
def command():
    data = request.get_json()
    text = data.get("text", "").strip()
    if text and _jarvis_ref:
        _jarvis_ref._on_text_command(text)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "ZAR no está conectado aún. Espera unos segundos."})

def emit_log(msg: str):
    socketio.emit("log", msg)

def start_server():
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)

_server_started = False


def run_in_background(jarvis):
    global _server_started
    set_jarvis(jarvis)
    if _server_started:          # reconnections reuse the same server
        return
    _server_started = True
    threading.Thread(target=start_server, daemon=True).start()