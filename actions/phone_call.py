"""
phone_call.py — ZAR te llama por teléfono cuando pasa algo grave.

Dos formas de llamar (eliges una al configurar):

  · callmebot (GRATIS) → llamada de voz por Telegram. ZAR te lee el aviso en voz alta.
        Límite: unos 256 caracteres por llamada y a veces tarda un poco en entrar.
  · twilio (DE PAGO, céntimos por llamada) → llamada de teléfono normal a tu móvil,
        sin límite de texto, entra al momento aunque no tengas internet en el móvil.

Configuración (una sola vez):
    venv\\Scripts\\python actions\\phone_call.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from xml.sax.saxutils import escape

import requests


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG = _base_dir() / "config" / "phone_call.json"
CALL_COOLDOWN_S = 5 * 60          # como mucho una llamada cada 5 minutos (luego solo Telegram)
CALLMEBOT_MAX = 256
CALLMEBOT_VOICE = "es-ES-Standard-B"     # voz masculina española de Google
TWILIO_VOICE = "Polly.Enrique"           # voz masculina española de Amazon (vía Twilio)

_last_call = 0.0
_lock = threading.Lock()


def _cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(cfg: dict):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def configured() -> bool:
    c = _cfg()
    if not c.get("enabled", True):
        return False
    if c.get("provider") == "callmebot":
        return bool(c.get("user"))
    if c.get("provider") == "twilio":
        return all(c.get(k) for k in ("sid", "token", "from", "to"))
    return False


# ═══════════════════════════ PROVEEDORES ═══════════════════════════
def _callmebot(c: dict, text: str):
    r = requests.get("https://api.callmebot.com/start.php", timeout=30, params={
        "user": c["user"], "text": text[:CALLMEBOT_MAX], "lang": c.get("voice") or CALLMEBOT_VOICE,
        "rpt": c.get("repeat", 2), "cc": "missed",
    })
    text_resp = _plain(r.text)
    low = text_resp.lower()
    bad = ("not authorized", "need to authorize", "too many requests", "user not found", "username not found",
           "invalid", "not allowed", "missing", "blocked")
    if r.status_code != 200 or any(w in low for w in bad):
        raise RuntimeError(f"CallMeBot respondió: {text_resp[:300] or r.status_code}")
    return text_resp


def _plain(html: str) -> str:
    """Quita el HTML (scripts, estilos, etiquetas) y deja solo el texto que se lee."""
    import re
    html = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html or "")
    html = re.sub(r"(?is)<head>.*?(</head>|<body)", " ", html)
    txt = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", txt).strip()


def _twilio(c: dict, text: str):
    voice = c.get("voice") or TWILIO_VOICE
    say = f'<Say language="es-ES" voice="{voice}">{escape(text)}</Say>'
    twiml = (f"<Response><Pause length=\"1\"/>{say}<Pause length=\"1\"/>"
             f'<Say language="es-ES" voice="{voice}">Repito.</Say>{say}</Response>')
    r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{c['sid']}/Calls.json",
                      auth=(c["sid"], c["token"]), timeout=30,
                      data={"To": c["to"], "From": c["from"], "Twiml": twiml})
    if r.status_code >= 300:
        try:
            msg = r.json().get("message")
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"Twilio respondió: {msg}")


# ═══════════════════════════ LLAMAR ═══════════════════════════
def fit(parts: list[str], limit: int) -> str:
    """Une las frases por orden de importancia sin pasarse del límite (las últimas son prescindibles)."""
    out = ""
    for p in parts:
        cand = (out + " " + p).strip()
        if len(cand) <= limit:
            out = cand
    return out


def call(parts: list[str] | str, force: bool = False, log=print) -> tuple[bool, str]:
    """
    Te llama y te lee el mensaje. `parts` = frases ordenadas de más a menos importante.
    Devuelve (llamado, explicación).
    """
    global _last_call
    if isinstance(parts, str):
        parts = [parts]
    c = _cfg()
    if not configured():
        return False, "Las llamadas no están configuradas (python actions\\phone_call.py)."
    with _lock:
        if not force and time.time() - _last_call < CALL_COOLDOWN_S:
            return False, "Ya te llamé hace poco; esta vez solo aviso por Telegram."
        _last_call = time.time()
    try:
        if c["provider"] == "callmebot":
            info = _callmebot(c, fit(parts, CALLMEBOT_MAX))
        else:
            info = _twilio(c, " ".join(parts))
        log(f"LLAMADA: te he llamado ({c['provider']})" + (f" → {info[:200]}" if info else ""))
        return True, "Llamada en curso."
    except Exception as e:
        with _lock:
            _last_call = 0.0
        log(f"LLAMADA: no pude llamar ({e})")
        return False, f"No pude llamar: {e}"


def call_async(parts, force=False, log=print, on_done=None):
    def run():
        ok, msg = call(parts, force, log)
        if on_done:
            on_done(ok, msg)
    threading.Thread(target=run, daemon=True, name="phone-call").start()


def test_call(log=print) -> str:
    ok, msg = call(["Hola, soy ZAR.", "Esto es una prueba de llamada.",
                    "Si alguien toca el ordenador en modo vigilancia, te llamaré así."], force=True, log=log)
    return "Te estoy llamando para probar." if ok else msg


# ═══════════════════════════ CONFIGURACIÓN INICIAL ═══════════════════════════
def setup():
    print("\n=== Llamadas de ZAR ===\n")
    print("  1) Llamada por Telegram con CallMeBot  (GRATIS)")
    print("  2) Llamada de teléfono normal con Twilio (de pago, céntimos por llamada)")
    print("  3) Desactivar llamadas\n")
    op = input("Elige 1, 2 o 3: ").strip()
    if op == "3":
        c = _cfg(); c["enabled"] = False; _save(c)
        print("Llamadas desactivadas.")
        return
    if op == "2":
        print("\nNecesitas una cuenta en twilio.com y un número comprado allí.")
        print("Los datos están en console.twilio.com (Account SID y Auth Token).\n")
        cfg = {"provider": "twilio", "enabled": True,
               "sid": input("Account SID: ").strip(),
               "token": input("Auth Token: ").strip(),
               "from": input("Tu número de Twilio (ej. +447700900123): ").strip(),
               "to": input("Tu móvil, con prefijo (ej. +447...): ").strip()}
    else:
        print("\nPaso 1: en Telegram busca @CallMeBot_txtbot y envíale /start (autoriza las llamadas).")
        print("Paso 2: comprueba que tienes nombre de usuario en Telegram (Ajustes → Nombre de usuario).\n")
        user = input("Tu usuario de Telegram (ej. @ouail): ").strip()
        if user and not user.startswith(("@", "+")):
            user = "@" + user
        cfg = {"provider": "callmebot", "enabled": True, "user": user}
    _save(cfg)
    print("\nGuardado. Te hago una llamada de prueba…")
    print(test_call())
    print("\nSi no te llega, revisa los pasos y vuelve a ejecutar esto. Reinicia ZAR al terminar.")


if __name__ == "__main__":
    import sys as _s, pathlib as _pl
    _root = str(_pl.Path(__file__).resolve().parent.parent)
    if _root not in _s.path:
        _s.path.insert(0, _root)
    setup()
