"""
whatsapp_cloud.py — Hablar con ZAR por WhatsApp (API oficial de Meta)

Vía OFICIAL (WhatsApp Cloud API), sin riesgo de baneo. Dos direcciones: le escribes y te responde,
igual que por Telegram. Necesita montarse (una vez):

  1. Crea una app en  https://developers.facebook.com/  → tipo "Business" → añade "WhatsApp".
  2. Te dan un número de prueba, un "Phone number ID" y un token temporal. Para uso continuo,
     genera un token permanente (System User) en Business Settings.
  3. ZAR levanta un servidor web local para RECIBIR tus mensajes. Meta necesita llegar a él
     por una dirección pública: usa un túnel (Cloudflare). Ver la guía que te paso.
  4. En Meta, configura el Webhook con esa dirección pública (…/whatsapp) y tu "token de verificación".
  5. Configura ZAR:  venv\\Scripts\\python -m actions.whatsapp_cloud

Regla de Meta: ZAR puede responderte libremente dentro de las 24h desde tu último mensaje
(para ti, que escribes tú, no es problema).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
try:
    from actions.owner_profile import OWNER
except Exception:
    OWNER = "the user"


WA_PORT = 5056
GRAPH = "https://graph.facebook.com/v21.0"
REPLY_WINDOW_S = 120


def _cfg() -> dict:
    from actions import vault
    return {"token": vault.get("wa_cloud_token", ""), "phone_id": vault.get("wa_phone_id", ""),
            "verify": vault.get("wa_verify_token", ""), "me": vault.get("wa_my_number", "")}


def configured() -> bool:
    c = _cfg()
    return bool(c["token"] and c["phone_id"] and c["verify"])


# ═══════════════════════════ ENVIAR ═══════════════════════════
def send(text: str, to: str | None = None) -> bool:
    c = _cfg()
    to = (to or c["me"]).lstrip("+")
    if not c["token"] or not c["phone_id"] or not to:
        return False
    try:
        import requests
        r = requests.post(f"{GRAPH}/{c['phone_id']}/messages", timeout=30,
                          headers={"Authorization": f"Bearer {c['token']}", "Content-Type": "application/json"},
                          json={"messaging_product": "whatsapp", "to": to, "type": "text",
                                "text": {"body": text[:4000]}})
        if r.status_code < 300:
            return True
        print(f"[ZAR] WhatsApp Cloud: no pude enviar ({r.status_code} {r.text[:150]})")
        return False
    except Exception as e:
        print(f"[ZAR] WhatsApp Cloud: error al enviar ({e})")
        return False


# ═══════════════════════════ RESPUESTAS DE ZAR ═══════════════════════════
_pending_until = 0.0


def expect_reply():
    global _pending_until
    _pending_until = time.time() + REPLY_WINDOW_S


def on_alberto_text(text: str):
    global _pending_until
    if text and time.time() < _pending_until:
        _pending_until = 0.0
        send("🤖 " + text)


# ═══════════════════════════ SERVIDOR (webhook) ═══════════════════════════
_on_text = None
_log = print


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        mode = (q.get("hub.mode") or [""])[0]
        token = (q.get("hub.verify_token") or [""])[0]
        challenge = (q.get("hub.challenge") or [""])[0]
        if mode == "subscribe" and token == _cfg()["verify"]:
            self.send_response(200); self.end_headers()
            self.wfile.write(challenge.encode())
            _log("WHATSAPP: webhook verificado por Meta")
        else:
            self.send_response(403); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        self.send_response(200); self.end_headers()      # responder YA a Meta
        try:
            self._process(json.loads(raw.decode("utf-8", "ignore")))
        except Exception as e:
            _log(f"WHATSAPP: error procesando mensaje ({e})")

    def _process(self, data: dict):
        me = _cfg()["me"].lstrip("+")
        for entry in data.get("entry", []):
            for ch in entry.get("changes", []):
                val = ch.get("value", {})
                for msg in val.get("messages", []):
                    frm = (msg.get("from") or "").lstrip("+")
                    if me and frm != me:                  # solo tú
                        continue
                    text = ""
                    if msg.get("type") == "text":
                        text = (msg.get("text") or {}).get("body", "")
                    elif msg.get("type") == "audio":
                        text = self._transcribe(val, msg)
                    if not text:
                        continue
                    _log(f"WHATSAPP: «{text}»")
                    expect_reply()
                    if _on_text:
                        _on_text(f"[Mensaje de WhatsApp de {OWNER}, desde el móvil. Respóndele breve] {text}")

    def _transcribe(self, val: dict, msg: dict) -> str:
        try:
            import requests
            from google.genai import types
            from actions import documents
            c = _cfg()
            mid = msg["audio"]["id"]
            info = requests.get(f"{GRAPH}/{mid}", timeout=20,
                               headers={"Authorization": f"Bearer {c['token']}"}).json()
            audio = requests.get(info["url"], timeout=30,
                                headers={"Authorization": f"Bearer {c['token']}"}).content
            mime = msg["audio"].get("mime_type", "audio/ogg")
            return documents.generate([types.Part.from_bytes(data=audio, mime_type=mime),
                                       "Transcribe este audio en español, solo el texto."]).strip()
        except Exception:
            return ""


_server: ThreadingHTTPServer | None = None


def start(on_text, log=print):
    global _server, _on_text, _log
    if _server is not None or not configured():
        return
    _on_text, _log = on_text, log
    try:
        _server = ThreadingHTTPServer(("0.0.0.0", WA_PORT), _Handler)
    except Exception as e:
        log(f"WHATSAPP: no pude abrir el servidor en el puerto {WA_PORT} ({e})")
        return
    threading.Thread(target=_server.serve_forever, daemon=True, name="whatsapp-cloud").start()
    log(f"WHATSAPP: servidor listo en el puerto {WA_PORT} (apunta el webhook de Meta aquí a través del túnel)")


# ═══════════════════════════ CONFIGURACIÓN ═══════════════════════════
def setup():
    from actions import vault
    print("\n=== Conectar ZAR con WhatsApp (API oficial de Meta) ===\n")
    print("Necesitas de Meta for Developers: el token, el Phone number ID y un token de verificación")
    print("que tú inventas (una palabra cualquiera, la misma que pondrás en el webhook).\n")
    token = input("Token de acceso (Bearer): ").strip()
    phone_id = input("Phone number ID: ").strip()
    verify = input("Token de verificación (invéntatelo, ej. alberto123): ").strip()
    me = input("Tu número de WhatsApp con prefijo (ej. +34600112233): ").strip()
    if not token or not phone_id or not verify:
        print("Faltan datos."); return
    if not vault.available():
        print("Falta cifrado: pip install cryptography."); return
    vault.set_secret("wa_cloud_token", token)
    vault.set_secret("wa_phone_id", phone_id)
    vault.set_secret("wa_verify_token", verify)
    vault.set_secret("wa_my_number", me)
    print("\nGuardado. Ahora:")
    print(f"  1. Arranca ZAR (levantará el servidor en el puerto {WA_PORT}).")
    print("  2. Abre un túnel público a ese puerto (te paso la guía de Cloudflare).")
    print("  3. En Meta, pon el webhook  https://TU-TUNEL/whatsapp  y el token de verificación de arriba.")
    print("  4. Suscríbete al evento 'messages'. Escríbele por WhatsApp y te responderá.")


if __name__ == "__main__":
    import pathlib
    _root = str(pathlib.Path(__file__).resolve().parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    setup()
