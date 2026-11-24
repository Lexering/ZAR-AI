"""
gmail_box.py — ZAR lee el Gmail de tu tienda

Se conecta a tu Gmail por IMAP (solo lectura) y:
  · Te dice los correos nuevos sin leer, resumidos (de quién, asunto, de qué va).
  · Te avisa (voz + Telegram) cuando llega uno importante: pedidos, pagos, clientes, Shopify.
  · Busca correos ("¿me ha escrito el proveedor?").

NO envía ni borra correos. Solo mira tu bandeja de entrada.

CONFIGURACIÓN (una vez):
  1. Tu cuenta de Google necesita la verificación en dos pasos activada.
  2. Entra en  https://myaccount.google.com/apppasswords  y crea una "contraseña de aplicación".
  3. Guárdala en ZAR:  venv\\Scripts\\python actions\\gmail_box.py
     (te pide el correo y esa contraseña; se guardan cifrados en la caja fuerte).
"""

from __future__ import annotations

import email
import imaplib
import os
import re
import sys
import threading
import time
from datetime import datetime
from email.header import decode_header
from pathlib import Path

IMAP_HOST = "imap.gmail.com"
CHECK_EVERY_S = 120
# Palabras que hacen "importante" un correo (para avisarte aunque estés fuera)
IMPORTANT = ["pedido", "order", "compra", "venta", "pago", "payment", "factura", "cliente",
             "shopify", "stripe", "paypal", "reembolso", "refund", "envío", "devolución",
             "urgente", "problema", "reclamación", "cancelado"]


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _creds() -> tuple[str, str]:
    from actions import vault
    return vault.get("gmail_address", ""), vault.get("gmail_app_password", "")


def configured() -> bool:
    a, p = _creds()
    return bool(a and p)


# ═══════════════════════════ CONEXIÓN ═══════════════════════════
def _connect() -> imaplib.IMAP4_SSL:
    addr, pw = _creds()
    if not addr or not pw:
        raise RuntimeError("Gmail no está configurado (ejecuta: python actions\\gmail_box.py).")
    M = imaplib.IMAP4_SSL(IMAP_HOST)
    M.login(addr, pw.replace(" ", ""))
    return M


def _decode(raw) -> str:
    if raw is None:
        return ""
    parts = []
    for text, enc in decode_header(raw):
        if isinstance(text, bytes):
            try:
                parts.append(text.decode(enc or "utf-8", "ignore"))
            except Exception:
                parts.append(text.decode("utf-8", "ignore"))
        else:
            parts.append(text)
    return "".join(parts).strip()


def _body_snippet(msg, limit=240) -> str:
    text = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                try:
                    text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                    break
                except Exception:
                    pass
    else:
        try:
            text = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "ignore")
        except Exception:
            text = ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _parse(num, data) -> dict:
    msg = email.message_from_bytes(data)
    frm = _decode(msg.get("From"))
    name = frm.split("<")[0].strip().strip('"') or frm
    return {"id": num.decode() if isinstance(num, bytes) else str(num),
            "from": name, "from_full": frm, "subject": _decode(msg.get("Subject")) or "(sin asunto)",
            "date": _decode(msg.get("Date")), "snippet": _body_snippet(msg)}


def fetch_unread(limit: int = 15) -> list[dict]:
    M = _connect()
    try:
        M.select("INBOX", readonly=True)
        typ, nums = M.search(None, "UNSEEN")
        ids = nums[0].split()[-limit:] if nums and nums[0] else []
        out = []
        for num in reversed(ids):
            typ, d = M.fetch(num, "(RFC822)")
            if d and d[0]:
                out.append(_parse(num, d[0][1]))
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass


def search(query: str, limit: int = 10) -> list[dict]:
    M = _connect()
    try:
        M.select("INBOX", readonly=True)
        # busca en asunto y remitente
        typ, nums = M.search(None, "OR", "SUBJECT", f'"{query}"', "FROM", f'"{query}"')
        ids = nums[0].split()[-limit:] if nums and nums[0] else []
        out = []
        for num in reversed(ids):
            typ, d = M.fetch(num, "(RFC822)")
            if d and d[0]:
                out.append(_parse(num, d[0][1]))
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass


def unread_count() -> int:
    M = _connect()
    try:
        M.select("INBOX", readonly=True)
        typ, nums = M.search(None, "UNSEEN")
        return len(nums[0].split()) if nums and nums[0] else 0
    finally:
        try:
            M.logout()
        except Exception:
            pass


# ═══════════════════════════ RESÚMENES ═══════════════════════════
def _is_important(m: dict) -> bool:
    text = (m["subject"] + " " + m["from"] + " " + m["snippet"]).lower()
    return any(w in text for w in IMPORTANT)


def summarize(mails: list[dict], spoken: bool = False) -> str:
    if not mails:
        return "No tienes correos nuevos sin leer."
    lines = []
    for m in mails:
        star = "⭐ " if _is_important(m) else ""
        lines.append(f"{star}{m['from']} — {m['subject']}" + (f": {m['snippet'][:100]}" if m['snippet'] and not spoken else ""))
    head = f"Tienes {len(mails)} correos sin leer."
    if spoken:
        imp = [m for m in mails if _is_important(m)]
        if imp:
            head += " Importantes: " + "; ".join(f"{m['from']} sobre {m['subject']}" for m in imp[:3]) + "."
        return head + " ¿Quieres que te los lea o te los mande por Telegram?"
    return head + "\n" + "\n".join(f"• {l}" for l in lines)


def ai_digest(mails: list[dict]) -> str:
    if not mails:
        return "No tienes correos nuevos."
    from actions import documents
    data = "\n".join(f"- De {m['from']} | Asunto: {m['subject']} | {m['snippet']}" for m in mails)
    prompt = ("Eres el asistente de una tienda online. Resume estos correos nuevos en español para el dueño: "
              "agrúpalos (pedidos, clientes, proveedores, publicidad/otros), di cuáles necesitan respuesta o "
              "acción y cuáles se pueden ignorar. Sé breve y claro.\n\n" + data)
    try:
        return documents.generate(prompt)
    except Exception:
        return summarize(mails)


# ═══════════════════════════ MONITOR ═══════════════════════════
class Monitor:
    def __init__(self, log=print):
        self.log = log
        self._seen: set[str] = set()
        self._first = True

    def check_once(self):
        from actions import notify
        try:
            mails = fetch_unread(15)
        except Exception as e:
            self.log(f"GMAIL: no pude conectar ({e})")
            return
        new = [m for m in mails if m["id"] not in self._seen]
        for m in mails:
            self._seen.add(m["id"])
        if self._first:
            self._first = False
            return
        important = [m for m in new if _is_important(m)]
        for m in important:
            notify.alert(f"Correo importante de {m['from']}: {m['subject']}.",
                         important=True, key=f"mail-{m['id']}", cooldown_s=0)
        otros = [m for m in new if m not in important]
        if otros:
            n=len(otros)
            notify.alert(f"{n} correo{'s' if n!=1 else ''} nuevo{'s' if n!=1 else ''} en la tienda.", key="mail-batch", cooldown_s=1800)

    def run_forever(self):
        time.sleep(40)
        while True:
            try:
                self.check_once()
            except Exception as e:
                self.log(f"GMAIL: {e}")
            time.sleep(CHECK_EVERY_S)


_monitor: Monitor | None = None


def start(log=print):
    global _monitor
    if _monitor is None and configured():
        _monitor = Monitor(log)
        threading.Thread(target=_monitor.run_forever, daemon=True, name="gmail").start()
        log("GMAIL: vigilando tu bandeja de entrada")
    return _monitor


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def gmail_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "unread").lower()
    if not configured():
        return ("El correo no está configurado. Ejecuta en la carpeta de ZAR: "
                "python actions\\gmail_box.py")
    try:
        if action in ("unread", "nuevos", "new"):
            return summarize(fetch_unread(15))
        if action in ("count", "cuantos"):
            return f"Tienes {unread_count()} correos sin leer."
        if action in ("digest", "resumen", "summary"):
            return ai_digest(fetch_unread(20))
        if action in ("search", "buscar"):
            q = (p.get("query") or "").strip()
            if not q:
                return "¿Qué quieres buscar en el correo?"
            res = search(q)
            return summarize(res) if res else f"No encuentro correos con «{q}»."
        if action in ("telegram", "enviar"):
            from actions import telegram_link
            txt = ai_digest(fetch_unread(20))
            telegram_link.send("📧 Correos de la tienda:\n" + txt)
            return "Te he mandado el resumen de correos por Telegram."
    except Exception as e:
        return f"No pude leer el correo: {e}"
    return "Acciones: unread, count, digest, search, telegram."


# ═══════════════════════════ CONFIGURACIÓN ═══════════════════════════
def setup():
    from actions import vault
    print("\n=== Conectar ZAR con tu Gmail (solo lectura) ===\n")
    print("1) Tu cuenta Google necesita la verificación en dos pasos activada.")
    print("2) Crea una 'contraseña de aplicación' en https://myaccount.google.com/apppasswords")
    print("   (elige 'Correo' / 'Otra', ponle 'ZAR').\n")
    addr = input("Tu dirección de Gmail: ").strip()
    pw = input("La contraseña de aplicación (16 letras): ").strip()
    if not addr or not pw:
        print("Faltan datos."); return
    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST); M.login(addr, pw.replace(" ", "")); M.logout()
    except Exception as e:
        print(f"\nNo pude entrar: {e}\nRevisa el correo y la contraseña de aplicación."); return
    if not vault.available():
        print("\nFalta cifrado: pip install cryptography. No lo guardo sin cifrar."); return
    vault.set_secret("gmail_address", addr)
    vault.set_secret("gmail_app_password", pw.replace(" ", ""))
    print("\n¡Listo! Correo conectado y guardado cifrado. Reinicia ZAR.")


if __name__ == "__main__":
    import sys as _s, pathlib as _pl
    _root = str(_pl.Path(__file__).resolve().parent.parent)
    if _root not in _s.path:
        _s.path.insert(0, _root)
    setup()
