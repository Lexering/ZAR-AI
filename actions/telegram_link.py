"""
telegram_link.py — Habla con ZAR desde el móvil (Telegram)

  · Escríbele o mándale audios: ZAR los recibe como si le hablaras y te responde por Telegram.
  · Te manda sus avisos (batería, precios, vigilancia con foto…) cuando no estás delante del PC.
  · Comandos: /estado /captura /foto /bloquear /vigilar /desarmar /parte /silencio /ayuda

Configuración (una sola vez):
  1. En Telegram, habla con @BotFather → /newbot → copia el token que te da.
  2. En la carpeta de ZAR:  venv\\Scripts\\python actions\\telegram_link.py
     Pega el token y envía a tu bot el código que te muestra. Solo tu cuenta queda autorizada.
"""

from __future__ import annotations

import json
import random
import sys
import threading
import time
from pathlib import Path

import requests
try:
    from actions.owner_profile import OWNER
except Exception:
    OWNER = "the user"



def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG = _base_dir() / "config" / "telegram.json"
REPLY_WINDOW_S = 90          # cuánto espera la respuesta de ZAR para reenviarla

HELP = (
    "Soy ZAR. Escríbeme, mándame un audio, una foto o un archivo.\n\n"
    "📋 Información\n/estado – cómo está el PC\n/parte – parte del día (tiempo + noticias)\n"
    "/noticias – titulares de hoy\n/agenda – tus citas\n/correos – resumen de tu Gmail\n/tienda – informe de Shopify\n"
    "/registro – qué he hecho hoy\n\n"
    "🛡 Seguridad\n/panel – panel de seguridad\n/auditoria – nota de seguridad del PC\n/centinela – estado del centinela\n"
    "/analizar <enlace> – ¿es peligroso?\n/clave <contraseña> – ¿está filtrada?\n"
    "/panico – silenciar, vigilar y bloquear\n/vigilar · /desarmar · /bloquear\n\n"
    "👁 Ver / crear\n/captura – pantalla\n/foto – webcam\n/pantalla – qué hay en la pantalla\n"
    "/imagen <texto> – crear imagen\n/presentacion <tema> – crear presentación\n"
    "/agente <objetivo> – que lo haga él\n/archivo <nombre> – te envío ese documento\n\n"
    "📎 Mándame un ARCHIVO y lo analizo (antivirus). Mándame una FOTO y te digo qué veo.\n"
    "/silencio – micro de casa · /llamada – probar llamada · /ayuda")


def _cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(cfg: dict):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def configured() -> bool:
    c = _cfg()
    return bool(c.get("token") and c.get("chat_id"))


def _api(method: str, token: str | None = None, http_timeout: float = 15, files=None, **params):
    token = token or _cfg().get("token")
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = requests.post(url, data=params, files=files, timeout=http_timeout)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "error de Telegram"))
    return data["result"]


def _tag(text: str) -> str:
    """With the same bot on two PCs, mark which PC sends the message (config: "pc_name")."""
    name = (_cfg().get("pc_name") or "").strip()
    return f"[{name}] {text}" if name and not text.startswith(f"[{name}]") else text


def send(text: str, photo: bytes | None = None) -> bool:
    text = _tag(text or "")
    c = _cfg()
    if not (c.get("token") and c.get("chat_id")):
        return False
    try:
        if photo:
            _api("sendPhoto", chat_id=c["chat_id"], caption=text[:1000],
                 files={"photo": ("foto.jpg", photo, "image/jpeg")}, http_timeout=30)
        else:
            for i in range(0, max(len(text), 1), 4000):
                _api("sendMessage", chat_id=c["chat_id"], text=text[i:i + 4000])
        return True
    except Exception as e:
        print(f"[ZAR] Telegram: no pude enviar ({e})")
        return False


def send_document(path, caption: str = "") -> bool:
    caption = _tag(caption or "")
    """Envía un archivo (PDF, Word, Excel…) al Telegram del dueño."""
    from pathlib import Path
    c = _cfg()
    p = Path(path)
    if not (c.get("token") and c.get("chat_id")) or not p.exists():
        return False
    try:
        with open(p, "rb") as f:
            _api("sendDocument", chat_id=c["chat_id"], caption=caption[:1000],
                 files={"document": (p.name, f, "application/octet-stream")}, http_timeout=120)
        return True
    except Exception as e:
        print(f"[ZAR] Telegram: no pude enviar el archivo ({e})")
        return False


# ═══════════════════════════ RESPUESTAS DE ZAR ═══════════════════════════
_pending_until = 0.0


def expect_reply():
    global _pending_until
    _pending_until = time.time() + REPLY_WINDOW_S


def on_alberto_text(text: str):
    """main.py llama a esto cada vez que ZAR termina de hablar."""
    global _pending_until
    if text and time.time() < _pending_until:
        _pending_until = 0.0
        send("🤖 " + text)


# ═══════════════════════════ ESCUCHA ═══════════════════════════
# Lista de comandos que Telegram muestra en el menú azul del bot y al escribir "/"
BOT_COMMANDS = [
    ("ayuda", "Ver todo lo que puedo hacer"),
    ("estado", "Cómo está el PC"),
    ("parte", "Parte del día (tiempo + noticias)"),
    ("noticias", "Titulares importantes de hoy"),
    ("agenda", "Mis citas de hoy y la semana"),
    ("correos", "Resumen de mi Gmail"),
    ("tienda", "Informe de mi tienda Shopify"),
    ("registro", "Qué ha hecho ZAR hoy"),
    ("panel", "Panel de seguridad"),
    ("auditoria", "Nota de seguridad del PC"),
    ("centinela", "Estado del centinela de seguridad"),
    ("analizar", "¿Es peligroso este enlace? /analizar <url>"),
    ("clave", "¿Está filtrada? /clave <contraseña>"),
    ("captura", "Captura de pantalla"),
    ("foto", "Foto de la webcam"),
    ("pantalla", "Qué hay en la pantalla"),
    ("imagen", "Crear una imagen /imagen <texto>"),
    ("presentacion", "Crear presentación /presentacion <tema>"),
    ("agente", "Que lo haga él /agente <objetivo>"),
    ("archivo", "Enviarme un documento /archivo <nombre>"),
    ("vigilar", "Activar vigilancia"),
    ("desarmar", "Desactivar vigilancia"),
    ("bloquear", "Bloquear el PC"),
    ("panico", "Silenciar, vigilar y bloquear"),
    ("llamada", "Probar la llamada de alerta"),
    ("whatsapp", "Probar aviso por WhatsApp"),
    ("silencio", "Silenciar o activar el micro de casa"),
]


def _register_commands():
    """Registra los comandos en Telegram para que aparezcan en el menú del bot."""
    try:
        cmds = [{"command": c, "description": d} for c, d in BOT_COMMANDS]
        _api("setMyCommands", commands=json.dumps(cmds))
    except Exception as e:
        print(f"[ZAR] Telegram: no pude registrar el menú de comandos ({e})")


class Link:
    def __init__(self, on_text, on_mute=None, log=print):
        self.on_text = on_text          # función que pasa un texto a ZAR
        self.on_mute = on_mute
        self.log = log
        self.offset = None
        self.running = False

    def start(self):
        if self.running or not configured():
            return
        self.running = True
        _register_commands()
        threading.Thread(target=self._loop, daemon=True, name="telegram").start()
        self.log("TELEGRAM: conectado, puedes hablarme desde el móvil")

    def _loop(self):
        owner = int(_cfg()["chat_id"])
        while self.running:
            try:
                ups = _api("getUpdates", http_timeout=40, offset=self.offset, timeout=30)
            except Exception as e:
                if "conflict" in str(e).lower():
                    # The same bot is being read by ZAR on another PC. The "primary" PC keeps it;
                    # a "secondary" PC steps back and only takes over while the other one is off.
                    secondary = (_cfg().get("role") or "primary").lower() == "secondary"
                    if time.time() - getattr(self, "_conflict_logged", 0) > 3600:
                        self._conflict_logged = time.time()
                        print("[ZAR] TELEGRAM: the bot is also open on another PC"
                              + (" - this PC only sends alerts while the other one is on." if secondary else "."))
                    time.sleep(600 if secondary else 5)
                else:
                    time.sleep(5)
                continue
            for u in ups:
                self.offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if (msg.get("chat") or {}).get("id") != owner:
                    continue                        # solo tu cuenta
                try:
                    self._handle(msg)
                except Exception as e:
                    send(f"Error: {e}")

    def _handle(self, msg: dict):
        text = (msg.get("text") or "").strip()
        if msg.get("voice") or msg.get("audio"):
            text = self._transcribe(msg.get("voice") or msg.get("audio"))
            if not text:
                send("No he entendido el audio.")
                return
            send(f"🎙 «{text}»")
        if msg.get("photo"):
            self._photo(msg)
            return
        if msg.get("document"):
            self._document(msg)
            return
        if not text:
            return
        if text.startswith("/"):
            self._command(text.split()[0].lower().split("@")[0], text.split(" ", 1)[1] if " " in text else "")
            return
        expect_reply()
        self.on_text(f"[Mensaje de Telegram de {OWNER}, desde el móvil. Respóndele breve] {text}")

    def _photo(self, msg: dict):
        """Foto con texto = editarla con IA. Foto sin texto = se la pasa a ZAR para preguntarle."""
        caption = (msg.get("caption") or "").strip()
        best = msg["photo"][-1]
        info = _api("getFile", file_id=best["file_id"])
        token = _cfg()["token"]
        data = requests.get(f"https://api.telegram.org/file/bot{token}/{info['file_path']}", timeout=30).content
        if not caption:
            try:
                from actions import live_vision
                send("👁 " + live_vision._gemini_vision(data, live_vision.LOOK_PROMPT.format(
                    question="El usuario te manda esta foto. Di qué ves y si necesita algo.")))
            except Exception:
                send("Recibida. Escríbeme en el texto de la foto qué quieres que haga con ella "
                     "(por ejemplo «¿qué es esto?» o «ponle fondo de playa»).")
            return
        send("🎨 Editando la foto…")
        try:
            from actions import image_studio
            img, path = image_studio.make_for_telegram(caption, source=data)
            send(f"Listo ({path.name})", photo=img)
        except Exception as e:
            send(f"No pude editarla: {e}")

    def _document(self, msg: dict):
        """Un archivo recibido = analizarlo en busca de malware (VirusTotal), sin ejecutarlo."""
        d = msg["document"]
        name = d.get("file_name", "archivo")
        if d.get("file_size", 0) > 30 * 1024 * 1024:
            send("El archivo es muy grande para analizarlo aquí (más de 30 MB).")
            return
        send(f"🔍 Analizando «{name}»…")
        try:
            import tempfile, os
            from actions import cyber_tools
            info = _api("getFile", file_id=d["file_id"])
            token = _cfg()["token"]
            data = requests.get(f"https://api.telegram.org/file/bot{token}/{info['file_path']}", timeout=60).content
            tmp = os.path.join(tempfile.gettempdir(), name)
            with open(tmp, "wb") as f:
                f.write(data)
            send(cyber_tools.check_file(__import__("pathlib").Path(tmp)))
            try: os.remove(tmp)
            except Exception: pass
        except Exception as e:
            send(f"No pude analizarlo: {e}")

    def _transcribe(self, media: dict) -> str:
        from google.genai import types
        from actions import documents
        info = _api("getFile", file_id=media["file_id"])
        token = _cfg()["token"]
        data = requests.get(f"https://api.telegram.org/file/bot{token}/{info['file_path']}", timeout=30).content
        mime = media.get("mime_type") or "audio/ogg"
        return documents.generate([types.Part.from_bytes(data=data, mime_type=mime),
                                   "Transcribe exactamente lo que dice este audio en español. Solo el texto."]).strip()

    def _command(self, cmd: str, arg: str):
        from actions import security, watchdog
        if cmd in ("/start", "/ayuda", "/help"):
            send(HELP)
        elif cmd == "/estado":
            send(f"✅ ZAR en línea.\n{watchdog.pc_status()}\n"
                 f"Vigilancia: {'armada' if security.guard.armed else 'apagada'}.")
        elif cmd == "/parte":
            send(watchdog.briefing_facts())
        elif cmd in ("/panel", "/seguridad"):
            from actions import security_audit
            send("🛡 " + security_audit.panel())
        elif cmd in ("/auditoria", "/auditoría"):
            from actions import security_audit
            send("🛡 " + security_audit.quick_summary())
        elif cmd == "/centinela":
            from actions import cyber_watch
            send("👁 " + cyber_watch.status())
        elif cmd == "/analizar" and arg:
            from actions import cyber_tools
            send(cyber_tools.check_url(arg))
        elif cmd == "/clave" and arg:
            from actions import cyber_tools
            send(cyber_tools.check_password(arg))
        elif cmd == "/noticias":
            from actions import watchdog
            loc = watchdog.detect_location()
            n = watchdog.top_news(loc.get("city",""), loc.get("country",""))
            send("📰 Titulares de hoy:\n" + n if n else "No pude obtener las noticias ahora mismo.")
        elif cmd == "/pantalla":
            from actions import live_vision
            send("👁 " + live_vision.look_screen(arg))
        elif cmd in ("/presentacion", "/presentación") and arg:
            send("📊 Creando la presentación…")
            try:
                from actions import presentations
                path, outline = presentations.make_for_telegram(arg)
                send_document(path, f"Presentación: {outline.get('title','')}")
            except Exception as e:
                send(f"No pude crear la presentación: {e}")
        elif cmd == "/agente" and arg:
            expect_reply()
            self.on_text(f"[Mensaje de Telegram de {OWNER}] Usa la herramienta autopilot con el objetivo: {arg}")
        elif cmd == "/captura":
            shot = security.screenshot_jpeg()
            send("Pantalla ahora mismo:", photo=shot) if shot else send("No pude hacer la captura.")
        elif cmd == "/foto":
            try:
                cam = security.Camera(); f = cam.frame(); cam.close()
                send("Webcam ahora mismo:", photo=cam.jpeg(f)) if f is not None else send("La webcam no responde.")
            except Exception as e:
                send(f"No pude usar la webcam: {e}")
        elif cmd in ("/pdf", "/archivo", "/documento"):
            if not arg:
                send("Dime qué archivo quieres. Ejemplo: /archivo informe de ventas")
                return
            from actions import documents
            path = documents.find_document(arg)
            if path is None:
                send(f"No encuentro ningún archivo con «{arg}». Prueba con otras palabras del nombre.")
            elif send_document(path, f"Aquí tienes: {path.name}"):
                pass
            else:
                send("Lo encontré pero no pude enviarlo.")
        elif cmd == "/panico":
            from actions import security
            send("🛑 " + security.panic(True))
        elif cmd in ("/agenda", "/hoy"):
            from actions import agenda
            send("📅 " + agenda.list_day() + ("\n" + agenda.list_week() if cmd == "/agenda" else ""))
        elif cmd in ("/correos", "/correo", "/mail"):
            from actions import gmail_box
            send("📧 " + gmail_box.gmail_tool({"action": "digest"}))
        elif cmd == "/registro":
            from actions import audit_log
            send(audit_log.summary_today())
        elif cmd == "/whatsapp":
            from actions import whatsapp_link
            send("📲 " + whatsapp_link.test())
        elif cmd == "/llamada":
            from actions import phone_call
            send("📞 " + phone_call.test_call())
        elif cmd == "/imagen":
            if not arg:
                send("Escribe /imagen y lo que quieres. Ejemplo: /imagen un lobo con armadura de Iron Man")
                return
            send("🎨 Creando la imagen…")
            try:
                from actions import image_studio
                img, path = image_studio.make_for_telegram(arg)
                send(f"Listo ({path.name})", photo=img)
            except Exception as e:
                send(f"No pude crear la imagen: {e}")
        elif cmd == "/bloquear":
            security.lock_pc(); send("🔒 PC bloqueado.")
        elif cmd == "/vigilar":
            send(security.guard.arm())
        elif cmd == "/desarmar":
            send(security.guard.disarm())
        elif cmd == "/silencio":
            if self.on_mute:
                self.on_mute(); send("Micrófono de casa cambiado (silenciado ↔ activo).")
        elif cmd == "/protocolo" and arg:
            expect_reply()
            self.on_text(f"[Mensaje de Telegram de {OWNER}] Ejecuta el protocolo {arg}")
        else:
            send("No conozco ese comando. /ayuda para ver la lista.")


_link: Link | None = None


def start(on_text, on_mute=None, log=print) -> Link | None:
    global _link
    if _link is None and configured():
        _link = Link(on_text, on_mute, log)
        _link.start()
    return _link


# ═══════════════════════════ CONFIGURACIÓN INICIAL ═══════════════════════════
def setup():
    print("\n=== Conectar ZAR con Telegram ===\n")
    print("1) En Telegram busca @BotFather, escribe /newbot y sigue los pasos.")
    print("2) Copia el token que te da (algo como 123456789:ABC-DEF...).\n")
    token = input("Pega aquí el token y pulsa Enter: ").strip()
    try:
        me = _api("getMe", token=token)
    except Exception as e:
        print(f"\nEse token no funciona: {e}")
        return
    code = f"{random.randint(0, 999999):06d}"
    print(f"\n3) Abre tu bot @{me['username']} en Telegram y envíale este código: {code}\n   Esperando…")
    offset = None
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            ups = _api("getUpdates", token=token, http_timeout=40, offset=offset, timeout=30)
        except Exception:
            time.sleep(3); continue
        for u in ups:
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if (m.get("text") or "").strip() == code:
                _save({"token": token, "chat_id": m["chat"]["id"], "bot": me["username"]})
                _api("sendMessage", token=token, chat_id=m["chat"]["id"],
                     text="✅ Conectado. Soy ZAR: desde ahora puedes hablarme por aquí.\n\n" + HELP)
                print("\n¡Listo! Telegram conectado. Reinicia ZAR para activarlo.")
                return
    print("\nNo recibí el código en 5 minutos. Vuelve a intentarlo.")


if __name__ == "__main__":
    import sys as _s, pathlib as _pl
    _root = str(_pl.Path(__file__).resolve().parent.parent)
    if _root not in _s.path:
        _s.path.insert(0, _root)
    setup()
