"""
whatsapp_link.py — Avisos de ZAR a tu WhatsApp (una dirección)

ZAR te manda sus avisos importantes también a WhatsApp (además de la voz y Telegram).
Usa la API gratuita de CallMeBot. Es solo de SALIDA: ZAR te escribe a ti; no sirve para
que tú le hables por WhatsApp (eso es otro tema, más complicado y con riesgo de baneo).

CONFIGURACIÓN (una vez):
  1. Guarda en tus contactos el número  +34 611 04 87 48  (CallMeBot).
  2. Desde WhatsApp, mándale:  I allow callmebot to send me messages
  3. Te responde con tu API key.
  4. En la carpeta de ZAR:  venv\\Scripts\\python -m actions.whatsapp_link
     Pon tu número (con prefijo, p. ej. +34600...) y la API key.
"""

from __future__ import annotations

import sys
import urllib.parse


def _creds() -> tuple[str, str]:
    from actions import vault
    return vault.get("whatsapp_phone", ""), vault.get("whatsapp_apikey", "")


def configured() -> bool:
    phone, key = _creds()
    return bool(phone and key)


def send(text: str) -> bool:
    """Envía un mensaje a TU WhatsApp. Devuelve True si se mandó."""
    phone, key = _creds()
    if not phone or not key:
        return False
    try:
        import requests
        r = requests.get("https://api.callmebot.com/whatsapp.php", timeout=30, params={
            "phone": phone, "text": text[:900], "apikey": key})
        body = (r.text or "").lower()
        if r.status_code == 200 and not any(w in body for w in ("error", "apikey", "not allowed", "invalid")):
            return True
        # CallMeBot a veces responde 203 con "Message queued"
        if "queued" in body or "message sent" in body:
            return True
        print(f"[ZAR] WhatsApp: respuesta inesperada ({r.text[:120].strip()})")
        return False
    except Exception as e:
        print(f"[ZAR] WhatsApp: no pude enviar ({e})")
        return False


def test() -> str:
    if not configured():
        return "WhatsApp no está configurado (python -m actions.whatsapp_link)."
    return ("Te he mandado un WhatsApp de prueba." if send("Hola, soy ZAR. Prueba de WhatsApp correcta.")
            else "No pude enviar el WhatsApp. Revisa el número y la API key.")


# ═══════════════════════════ CONFIGURACIÓN ═══════════════════════════
def setup():
    from actions import vault
    print("\n=== Avisos de ZAR por WhatsApp (CallMeBot) ===\n")
    print("1) Guarda en contactos el número  +34 611 04 87 48")
    print("2) Mándale por WhatsApp:  I allow callmebot to send me messages")
    print("3) Te responderá con tu API key.\n")
    phone = input("Tu número de WhatsApp con prefijo (ej. +34600112233): ").strip()
    key = input("La API key que te dio CallMeBot: ").strip()
    if not phone or not key:
        print("Faltan datos."); return
    if not vault.available():
        print("Falta cifrado: pip install cryptography."); return
    vault.set_secret("whatsapp_phone", phone)
    vault.set_secret("whatsapp_apikey", key)
    print("\nGuardado. Te mando una prueba…")
    print(test())
    print("Reinicia ZAR para que use WhatsApp en los avisos.")


if __name__ == "__main__":
    import pathlib
    _root = str(pathlib.Path(__file__).resolve().parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    setup()
