"""
vault.py — Caja fuerte de claves de ZAR

Guarda las claves de API y tokens cifrados, en vez de tenerlos en texto plano en
config/api_keys.json. El cifrado va ligado a ESTE equipo y ESTE usuario: el archivo
cifrado, copiado a otro ordenador, no se puede abrir.

Uso desde el código:
    from actions import vault
    token = vault.get("gemini_api_key")      # busca primero en la caja, luego en api_keys.json

Uso por voz / herramienta:
    "guarda mi clave de VirusTotal: 1234..."   → la guarda cifrada
    "¿qué claves tienes guardadas?"            → lista los nombres (nunca los valores)

Necesita:  pip install cryptography
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


VAULT = _base_dir() / "config" / "vault.enc"
PLAIN = _base_dir() / "config" / "api_keys.json"


# ═══════════════════════════ CLAVE DE CIFRADO (ligada al equipo) ═══════════════════════════
def _machine_secret() -> bytes:
    """Un secreto estable de este equipo+usuario. No es perfecto, pero evita el texto plano."""
    parts = [os.environ.get("COMPUTERNAME", ""), os.environ.get("USERNAME", ""),
             os.environ.get("USERDOMAIN", ""), str(_base_dir())]
    if os.name == "nt":
        try:
            import subprocess
            mg = subprocess.run(["powershell", "-NoProfile", "-Command",
                                 "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
                                capture_output=True, text=True, timeout=10,
                                creationflags=0x08000000).stdout.strip()
            parts.append(mg)
        except Exception:
            pass
    return "||".join(parts).encode()


def _fernet():
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"alberto-vault-v1", iterations=200_000)
    key = base64.urlsafe_b64encode(kdf.derive(_machine_secret()))
    return Fernet(key)


def available() -> bool:
    try:
        import cryptography  # noqa
        return True
    except Exception:
        return False


# ═══════════════════════════ LECTURA / ESCRITURA ═══════════════════════════
def _load() -> dict:
    if not VAULT.exists():
        return {}
    try:
        data = _fernet().decrypt(VAULT.read_bytes())
        return json.loads(data.decode())
    except Exception:
        return {}


def _store(d: dict):
    VAULT.parent.mkdir(parents=True, exist_ok=True)
    token = _fernet().encrypt(json.dumps(d).encode())
    tmp = VAULT.with_suffix(".tmp")
    tmp.write_bytes(token)
    tmp.replace(VAULT)


def _plain() -> dict:
    try:
        return json.loads(PLAIN.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get(name: str, default=None):
    """Busca una clave: primero en la caja fuerte, luego en api_keys.json (compatibilidad)."""
    v = _load().get(name)
    if v is not None:
        return v
    return _plain().get(name, default)


def set_secret(name: str, value: str) -> str:
    if not available():
        return ("No puedo cifrar porque falta la librería. Instálala con: pip install cryptography. "
                "De momento la clave no se ha guardado.")
    d = _load()
    d[name] = value
    _store(d)
    return f"Guardada «{name}» en la caja fuerte, cifrada. (No la repito en voz alta por seguridad.)"


def delete(name: str) -> str:
    d = _load()
    if name not in d:
        return f"No tengo ninguna clave llamada «{name}» en la caja fuerte."
    d.pop(name)
    _store(d)
    return f"Borrada «{name}» de la caja fuerte."


def names() -> list[str]:
    return sorted(set(_load().keys()))


def import_plain() -> str:
    """Mueve las claves de api_keys.json a la caja fuerte cifrada (deja Gemini para no romper el arranque)."""
    if not available():
        return "Instala primero: pip install cryptography."
    plain = _plain()
    if not plain:
        return "No hay nada en api_keys.json que importar."
    d = _load()
    moved = []
    for k, v in plain.items():
        if isinstance(v, str) and v:
            d[k] = v
            moved.append(k)
    _store(d)
    return (f"Importadas {len(moved)} claves a la caja fuerte: {', '.join(moved)}. "
            "Siguen también en api_keys.json; cuando confirmes que todo va, puedes borrarlas de ahí a mano.")


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def vault_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "list").lower()
    if action in ("set", "save", "guardar"):
        name = (p.get("name") or "").strip()
        value = (p.get("value") or "").strip()
        if not name or not value:
            return "Dime el nombre y el valor de la clave. Ejemplo: guarda virustotal_api_key con valor 1234."
        return set_secret(name, value)
    if action in ("list", "lista"):
        n = names()
        return "Claves guardadas en la caja fuerte: " + ", ".join(n) + "." if n else \
               "La caja fuerte está vacía. Puedo importar las de api_keys.json si quieres."
    if action in ("delete", "borrar"):
        return delete((p.get("name") or "").strip())
    if action in ("import", "importar"):
        return import_plain()
    return "Acciones: set (guardar), list (listar), delete (borrar), import (importar)."
