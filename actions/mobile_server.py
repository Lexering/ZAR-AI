"""
mobile_server.py — Panel de ZAR en el móvil

Levanta una web en tu red local para usar a ZAR desde el móvil (misma wifi), como en el PC:
  · Le ESCRIBES o le HABLAS (grabas voz y la entiende).
  · Te RESPONDE con voz en el móvil.
  · Le ENVÍAS archivos (fotos, PDF, documentos) y los recibe.
  · Botones rápidos: estado, agenda, noticias, pánico, vigilar…

Se abre en el móvil en  http://IP-DE-TU-PC:5057  (ZAR te dice la dirección al arrancar).
Protegido con un PIN (config/mobile.json). Solo en tu red local salvo que montes un túnel.
"""

from __future__ import annotations

import json
import random
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
try:
    from actions.owner_profile import OWNER
except Exception:
    OWNER = "the user"


PORT = 5057


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG = _base_dir() / "config" / "mobile.json"


def _cfg() -> dict:
    try:
        c = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        c = {}
    if not c.get("pin"):
        c["pin"] = f"{random.randint(1000, 9999)}"
        c.setdefault("port", PORT)
        c.setdefault("enabled", True)
        c.setdefault("https", True)
        c.setdefault("voice", "Charon")
        c.setdefault("jarvis_voice", True)
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps(c, indent=2), encoding="utf-8")
        except Exception:
            pass
    return c


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


CERT = _base_dir() / "config" / "mobile_cert.pem"
KEY = _base_dir() / "config" / "mobile_key.pem"


def _ensure_cert(ip: str) -> bool:
    """Crea (una vez) un certificado propio para HTTPS en la red local. Necesita cryptography."""
    try:
        if CERT.exists() and KEY.exists():
            return True
        import datetime, ipaddress
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ZAR")])
        san = [x509.DNSName("localhost")]
        for addr in {ip, "127.0.0.1"}:
            try:
                san.append(x509.IPAddress(ipaddress.ip_address(addr)))
            except Exception:
                pass
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
                .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName(san), critical=False)
                .sign(key, hashes.SHA256()))
        CERT.parent.mkdir(parents=True, exist_ok=True)
        KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return True
    except Exception as e:
        print(f"[ZAR] MÓVIL: no pude crear el certificado HTTPS ({e})")
        return False


def _inbox() -> Path:
    from actions.documents import known_folder
    p = known_folder("desktop") / "Para ZAR"
    p.mkdir(parents=True, exist_ok=True)
    return p


QUICK = [
    ("Estado", "dime el estado del PC"),
    ("Parte", "dame el parte del día"),
    ("Noticias", "dame las noticias de hoy"),
    ("Agenda", "qué tengo hoy en la agenda"),
    ("Correos", "tengo correos nuevos"),
    ("Seguridad", "enséñame el panel de seguridad"),
    ("Captura", "haz una captura de pantalla y mándamela por Telegram"),
    ("Vigilar", "activa el modo vigilancia"),
    ("Pánico", "activa el modo pánico"),
]

PAGE = r"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>ZAR</title>
<style>
:root{--bg:#0a0e14;--panel:#111826;--line:#1d2a3a;--cyan:#0ec8e6;--text:#dff7ff;--muted:#8aa0b2}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--text);font-family:"Bahnschrift","Segoe UI",system-ui,sans-serif;
 display:flex;flex-direction:column;height:100vh;height:100dvh}
header{padding:14px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
.dot{width:12px;height:12px;border-radius:50%;background:var(--muted);box-shadow:0 0 10px currentColor}
.dot.on{color:var(--cyan);background:var(--cyan)} .dot.speak{color:#39d98a;background:#39d98a}
.dot.guard{color:#ff5470;background:#ff5470}
h1{font-size:17px;margin:0;letter-spacing:2px;font-weight:600}
#sub{font-size:12px;color:var(--muted);margin-left:auto}
#spk{font-size:19px;background:none;border:none;color:var(--muted);padding:4px 6px}
#spk.on{color:var(--cyan)}
#feed{flex:1;overflow-y:auto;padding:14px 16px;display:flex;flex-direction:column;gap:8px}
.msg{padding:9px 12px;border-radius:12px;max-width:85%;font-size:15px;line-height:1.35;word-wrap:break-word}
.user{align-self:flex-end;background:#15324a;border:1px solid #1d4765}
.ai{align-self:flex-start;background:var(--panel);border:1px solid var(--line)}
.sys{align-self:center;color:var(--muted);font-size:12px;background:transparent;text-align:center}
.err{align-self:flex-start;background:#3a1620;border:1px solid #5a2030;color:#ffb3c0}
.quick{display:flex;gap:8px;overflow-x:auto;padding:10px 16px;border-top:1px solid var(--line)}
.chip{white-space:nowrap;padding:8px 14px;border-radius:20px;background:var(--panel);border:1px solid var(--line);
 color:var(--text);font-size:14px}
.chip:active{background:#15324a}
form{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--line);background:var(--bg);align-items:center}
input#box{flex:1;background:var(--panel);border:1px solid var(--line);color:var(--text);border-radius:22px;
 padding:12px 16px;font-size:16px;outline:none}
.iconbtn{background:var(--panel);border:1px solid var(--line);color:var(--text);border-radius:50%;
 width:46px;height:46px;font-size:20px;flex:none;display:flex;align-items:center;justify-content:center}
#mic.rec{background:#ff5470;border-color:#ff5470;color:#fff;animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
button#go{background:var(--cyan);border:none;color:#04222a;font-weight:700;border-radius:50%;width:46px;height:46px;font-size:18px;flex:none}
#gate{position:fixed;inset:0;background:var(--bg);display:flex;flex-direction:column;align-items:center;
 justify-content:center;gap:16px;z-index:9}
#gate input{width:160px;text-align:center;letter-spacing:8px;font-size:22px;background:var(--panel);
 border:1px solid var(--line);color:var(--text);border-radius:12px;padding:12px}
#gate button{background:var(--cyan);border:none;color:#04222a;font-weight:700;border-radius:22px;padding:12px 28px;font-size:16px}
.hide{display:none!important}
</style></head><body>
<div id="gate">
  <h1 style="letter-spacing:4px">ZAR</h1>
  <div style="color:var(--muted);font-size:13px">Introduce el PIN</div>
  <input id="pin" type="tel" inputmode="numeric" maxlength="6" placeholder="----">
  <button onclick="savePin()">Entrar</button>
  <div id="gerr" style="color:#ffb3c0;font-size:13px;height:16px"></div>
</div>
<header>
  <div class="dot" id="dot"></div>
  <h1>ZAR</h1>
  <div id="sub">conectando…</div>
  <button id="spk" class="on" onclick="toggleSpk()" title="Voz">&#128266;</button>
</header>
<div id="feed"></div>
<div class="quick" id="quick"></div>
<form onsubmit="return send()">
  <button type="button" class="iconbtn" onclick="document.getElementById('file').click()">&#128206;</button>
  <input type="file" id="file" class="hide" onchange="upload()">
  <input id="box" placeholder="Escribe o pulsa el micro…" autocomplete="off">
  <button type="button" class="iconbtn" id="mic" onclick="toggleMic()">&#127908;</button>
  <button type="submit" id="go">&#9654;</button>
</form>
<script>
let PIN = localStorage.getItem("alberto_pin") || "";
let SPK = localStorage.getItem("alberto_spk") !== "0";
let lastT = 0, rec = null, chunks = [], spkUnlocked = false;
const feed = document.getElementById("feed");
const QUICK = __QUICK__;
document.getElementById("spk").className = SPK ? "on" : "";

function unlockSpeech(){ if(spkUnlocked) return; spkUnlocked=true;
  try{ if(window.speechSynthesis){ const u=new SpeechSynthesisUtterance(" "); u.volume=0; speechSynthesis.speak(u);} }catch(e){}
  try{ const a=new Audio(); a.play().catch(()=>{}); }catch(e){} }
function savePin(){ unlockSpeech(); PIN = document.getElementById("pin").value.trim();
  localStorage.setItem("alberto_pin", PIN); check(); }
function check(){ fetch("/api/state?pin="+encodeURIComponent(PIN)).then(r=>{
  if(r.status===403){ document.getElementById("gerr").textContent="PIN incorrecto"; return; }
  document.getElementById("gate").classList.add("hide"); buildQuick(); poll();
}).catch(()=>{}); }
function toggleSpk(){ SPK=!SPK; localStorage.setItem("alberto_spk",SPK?"1":"0");
  document.getElementById("spk").className=SPK?"on":""; if(!SPK&&window.speechSynthesis) speechSynthesis.cancel(); }
function buildQuick(){ const q=document.getElementById("quick");
  QUICK.forEach(([label,cmd])=>{ const b=document.createElement("button"); b.className="chip";
    b.textContent=label; b.onclick=()=>run(cmd); q.appendChild(b); }); }
function add(who,text){ const d=document.createElement("div"); d.className="msg "+who; d.textContent=text;
  feed.appendChild(d); feed.scrollTop=feed.scrollHeight; return d; }
let audioEl=null;
function speakBrowser(text){ if(!window.speechSynthesis) return;
  try{ const u=new SpeechSynthesisUtterance(text); u.lang="es-ES"; u.rate=0.98; u.pitch=0.9;
    const v=speechSynthesis.getVoices().find(x=>/es(-|_)/i.test(x.lang)); if(v)u.voice=v;
    speechSynthesis.speak(u); }catch(e){} }
function speak(text){ if(!SPK) return;
  // Primero la voz de JARVIS (Gemini, desde el servidor); si no, la del navegador.
  fetch("/api/tts?pin="+encodeURIComponent(PIN)+"&text="+encodeURIComponent(text)).then(r=>{
    if(r.status===200) return r.blob(); throw 0;
  }).then(b=>{ try{ if(audioEl){audioEl.pause();} audioEl=new Audio(URL.createObjectURL(b));
    audioEl.play().catch(()=>speakBrowser(text)); }catch(e){ speakBrowser(text); } })
  .catch(()=>speakBrowser(text)); }
function run(text){ unlockSpeech(); add("user",text);
  fetch("/api/cmd?pin="+encodeURIComponent(PIN),{method:"POST",body:text}).catch(()=>add("err","Sin conexión")); }
function send(){ const b=document.getElementById("box"); const t=b.value.trim(); if(t){ run(t); b.value=""; } return false; }

function toggleMic(){
  unlockSpeech();
  const m=document.getElementById("mic");
  if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder==="undefined"){
    if(!window.__micWarned){ window.__micWarned=true;
      add("sys","Para hablarle por voz hay que abrir el panel con https. Escribir y oírle sí funciona así."); }
    return; }
  if(rec && rec.state==="recording"){ rec.stop(); return; }
  navigator.mediaDevices.getUserMedia({audio:true}).then(stream=>{
    chunks=[]; rec=new MediaRecorder(stream);
    rec.ondataavailable=e=>{ if(e.data.size)chunks.push(e.data); };
    rec.onstop=()=>{ m.className="iconbtn"; stream.getTracks().forEach(t=>t.stop());
      const blob=new Blob(chunks,{type:rec.mimeType||"audio/webm"});
      add("sys","Transcribiendo…");
      fetch("/api/audio?pin="+encodeURIComponent(PIN),{method:"POST",
        headers:{"Content-Type":blob.type},body:blob}).then(r=>r.text()).then(t=>{
        const last=feed.querySelector(".sys:last-child"); if(last)last.remove();
        if(t && t.trim()) add("user","🎙 "+t.trim()); else add("sys","No te he entendido");
      }).catch(()=>{ const l=feed.querySelector(".sys:last-child"); if(l)l.remove(); add("err","No pude enviar el audio"); });
    };
    rec.start(); m.className="iconbtn rec";
  }).catch(()=>add("err","No puedo usar el micrófono (da permiso al navegador)"));
}

function upload(){ const f=document.getElementById("file").files[0]; if(!f) return;
  add("user","📎 "+f.name); add("sys","Enviando archivo…");
  fetch("/api/upload?pin="+encodeURIComponent(PIN)+"&name="+encodeURIComponent(f.name),
    {method:"POST",headers:{"Content-Type":"application/octet-stream"},body:f}).then(r=>{
    const l=feed.querySelector(".sys:last-child"); if(l)l.remove();
    add("sys", r.ok?"Archivo recibido por ZAR":"No pude enviar el archivo");
  }).catch(()=>{ const l=feed.querySelector(".sys:last-child"); if(l)l.remove(); add("err","Fallo al enviar"); });
  document.getElementById("file").value="";
}

function poll(){ fetch("/api/state?pin="+encodeURIComponent(PIN)).then(r=>r.json()).then(d=>{
  const st=d.state||"OFFLINE"; const sub=document.getElementById("sub");
  const names={LISTENING:"escuchando",SPEAKING:"hablando",THINKING:"pensando",OFFLINE:"desconectado",
    STANDBY:'en reposo (di "ZAR")',MUTED:"micro apagado",GUARD:"vigilando",DICTATING:"dictando"};
  sub.textContent=(d.speaker?d.speaker+" · ":"")+(names[st]||st.toLowerCase());
  const dot=document.getElementById("dot"); dot.className="dot"+(d.guard?" guard":st==="SPEAKING"?" speak":st==="OFFLINE"?"":" on");
  (d.log||[]).forEach(e=>{ if(e.t>lastT){ lastT=e.t;
    const who=e.who==="user"?"user":e.who==="ai"?"ai":e.who==="err"?"err":"sys";
    if(who==="user" && !firstLoad) return;                 // lo que dices ya lo pintamos nosotros
    const el=add(who,(e.who==="user"&&e.speaker?e.speaker+": ":"")+e.text);
    if(who==="ai" && !firstLoad) speak(e.text);
  }}); firstLoad=false;
}).catch(()=>{}); setTimeout(poll,2000); }
let firstLoad=true;
if(window.speechSynthesis){ speechSynthesis.getVoices();
  speechSynthesis.onvoiceschanged=()=>speechSynthesis.getVoices(); }
try{ if(location.protocol!=="https:" && (!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia)){
  const mb=document.getElementById("mic"); if(mb) mb.style.opacity="0.4"; } }catch(e){}
if(PIN) check();
</script></body></html>"""


_on_text = None
_log = print


def _transcribe(data: bytes, mime: str) -> str:
    try:
        from google.genai import types
        from actions import documents
        return documents.generate([types.Part.from_bytes(data=data, mime_type=mime or "audio/webm"),
                                   "Transcribe este audio en español. Devuelve solo el texto."]).strip()
    except Exception as e:
        _log(f"MÓVIL: no pude transcribir ({e})")
        return ""


TTS_MODELS = ["gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts", "gemini-2.5-flash-tts"]


def _wav(pcm: bytes, rate: int = 24000) -> bytes:
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return buf.getvalue()


def _tts(text: str) -> bytes:
    """Genera voz con la MISMA voz de ZAR (Gemini). Devuelve WAV, o b'' si no se puede."""
    import base64
    try:
        from google.genai import types
        from actions import documents
        client = documents._gemini()
        voice = _cfg().get("voice", "Charon")
        prompt = ("Di esto en español con voz grave, serena y pausada, estilo JARVIS, sin prisa: " + text)
        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))))
        for model in TTS_MODELS:
            try:
                r = client.models.generate_content(model=model, contents=prompt, config=cfg)
                data = r.candidates[0].content.parts[0].inline_data.data
                pcm = base64.b64decode(data) if isinstance(data, str) else bytes(data)
                if pcm:
                    return _wav(pcm)
            except Exception:
                continue
    except Exception as e:
        _log(f"MÓVIL: voz JARVIS no disponible ({e})")
    return b""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _pin_ok(self) -> bool:
        q = parse_qs(urlparse(self.path).query)
        return (q.get("pin") or [""])[0] == _cfg()["pin"]

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def do_GET(self):
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            html = PAGE.replace("__QUICK__", json.dumps(QUICK, ensure_ascii=False))
            body = html.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if route == "/api/tts":
            if not self._pin_ok():
                self.send_response(403); self.end_headers(); return
            q = parse_qs(urlparse(self.path).query)
            text = (q.get("text") or [""])[0]
            audio = _tts(text) if (text and _cfg().get("jarvis_voice", True)) else b""
            if audio:
                self.send_response(200); self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(audio))); self.end_headers()
                self.wfile.write(audio)
            else:
                self.send_response(204); self.end_headers()
            return
        if route == "/api/state":
            if not self._pin_ok():
                self.send_response(403); self.end_headers(); return
            try:
                from actions import hud_bridge
                data = hud_bridge.snapshot()
            except Exception:
                data = {"state": "OFFLINE", "log": []}
            body = json.dumps(data).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        route = urlparse(self.path).path
        if not self._pin_ok():
            self.send_response(403); self.end_headers(); return

        if route == "/api/cmd":
            text = self._read_body().decode("utf-8", "ignore").strip()
            self.send_response(200); self.end_headers()
            if text and _on_text:
                _log(f"MÓVIL: «{text}»")
                try:
                    _on_text(f"[Mensaje desde el panel del móvil de {OWNER}. Respóndele breve] {text}")
                except Exception as e:
                    _log(f"MÓVIL: error ({e})")
            return

        if route == "/api/audio":
            data = self._read_body()
            mime = self.headers.get("Content-Type", "audio/webm").split(";")[0]
            text = _transcribe(data, mime) if data else ""
            body = text.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
            if text and _on_text:
                _log(f"MÓVIL (voz): «{text}»")
                try:
                    _on_text(f"[Mensaje de voz desde el móvil de {OWNER}. Respóndele breve] {text}")
                except Exception:
                    pass
            return

        if route == "/api/upload":
            q = parse_qs(urlparse(self.path).query)
            name = (q.get("name") or ["archivo"])[0]
            data = self._read_body()
            ok = False
            try:
                safe = "".join(c for c in name if c.isalnum() or c in " ._-()[]").strip() or "archivo"
                dest = _inbox() / safe
                i = 2
                while dest.exists():
                    dest = _inbox() / f"{dest.stem}_{i}{dest.suffix}"; i += 1
                dest.write_bytes(data)
                _log(f"MÓVIL: archivo recibido «{safe}» ({len(data)} bytes)")
                ok = True
            except Exception as e:
                _log(f"MÓVIL: no pude guardar el archivo ({e})")
            self.send_response(200 if ok else 500); self.end_headers()
            return

        self.send_response(404); self.end_headers()


_server: ThreadingHTTPServer | None = None


def start(on_text, log=print):
    global _server, _on_text, _log
    if _server is not None:
        return
    c = _cfg()
    if not c.get("enabled", True):
        return
    _on_text, _log = on_text, log
    port = int(c.get("port", PORT))
    ip = lan_ip()
    try:
        _server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    except Exception as e:
        log(f"MÓVIL: no pude abrir el panel ({e})")
        return
    scheme = "http"
    if c.get("https", False) and _ensure_cert(ip):
        try:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=str(CERT), keyfile=str(KEY))
            _server.socket = ctx.wrap_socket(_server.socket, server_side=True)
            scheme = "https"
        except Exception as e:
            log(f"MÓVIL: HTTPS no disponible, sigo en http ({e}). El micro del móvil necesita https.")
    threading.Thread(target=_server.serve_forever, daemon=True, name="mobile").start()
    log(f"MÓVIL: abre {scheme}://{ip}:{port} en el móvil (misma wifi). PIN: {c['pin']}")


if __name__ == "__main__":
    import pathlib
    _root = str(pathlib.Path(__file__).resolve().parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    c = _cfg()
    sch = "https" if c.get("https", False) else "http"
    print(f"Panel del móvil: {sch}://{lan_ip()}:{c.get('port', PORT)}  ·  PIN: {c['pin']}")
    print("(Arranca ZAR para usarlo; esto solo muestra la dirección y el PIN.)")
