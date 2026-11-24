"""
notify.py — Cómo avisa ZAR de las cosas.

  · Si estás delante del PC → te lo dice en voz alta.
  · Si llevas un rato sin tocar el PC → te lo manda a Telegram (si está configurado).
  · Los avisos importantes van por los dos sitios.
"""

from __future__ import annotations

import os
import threading
import time

AWAY_AFTER_S = 5 * 60          # sin tocar teclado ni ratón este tiempo = "no estás"

_speaker = None                 # función que hace hablar a ZAR (la pone main.py)
_log = print
_recent: dict[str, float] = {}  # evita repetir el mismo aviso
_lock = threading.Lock()


def set_speaker(fn, log=None):
    global _speaker, _log
    _speaker = fn
    if log:
        _log = log


def idle_seconds() -> float:
    """Segundos desde la última vez que tocaste teclado o ratón."""
    if os.name != "nt":
        return 0.0
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        li = LASTINPUTINFO(); li.cbSize = ctypes.sizeof(li)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
        return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0
    except Exception:
        return 0.0


def user_present() -> bool:
    return idle_seconds() < AWAY_AFTER_S


def say(text: str):
    """Pide a ZAR que diga algo con sus palabras (estilo JARVIS)."""
    if _speaker:
        try:
            _speaker(f"[SISTEMA] Aviso para el usuario: {text} Díselo en una frase corta, estilo JARVIS.")
        except Exception:
            pass


def telegram(text: str, photo: bytes | None = None) -> bool:
    try:
        from actions import telegram_link
        return telegram_link.send(text, photo=photo)
    except Exception:
        return False


def whatsapp(text: str) -> bool:
    try:
        from actions import whatsapp_link
        return whatsapp_link.send(text)
    except Exception:
        return False


def alert(text: str, important: bool = False, key: str | None = None, cooldown_s: float = 900,
          photo: bytes | None = None):
    """
    Avisa al usuario por el mejor canal.
    key + cooldown_s: no repetir el mismo aviso durante ese tiempo.
    """
    k = key or text
    with _lock:
        now = time.time()
        if now - _recent.get(k, 0) < cooldown_s:
            return
        _recent[k] = now
    _log(f"AVISO: {text}")
    present = user_present()
    if present or important:
        say(text)
    if (not present or important or photo is not None):
        telegram(f"⚠️ {text}", photo=photo)
    if important or (not present):            # los importantes (y si no estás) también por WhatsApp
        whatsapp(f"⚠️ ZAR: {text}")
