"""
audit_log.py — Diario de acciones de ZAR

Apunta cada cosa que hace ZAR (qué herramienta, cuándo, quién la pidió y cómo acabó)
en un archivo que puedes revisar. Útil para saber "¿qué ha hecho hoy?" o para auditar
si algo raro pasó.

  · Se guarda en logs/acciones.jsonl (una línea por acción) y rota solo cuando crece.
  · Las acciones delicadas (borrar, enviar, trading, seguridad) se marcan.
  · Nunca guarda contraseñas ni valores de claves (se ocultan).

  "¿qué has hecho hoy?"  /  "enséñame el registro de acciones"
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()
SENSITIVE_TOOLS = {"trading_bot", "send_message", "whatsapp_scheduler", "email_controller",
                   "file_controller", "security", "vault", "shutdown_jarvis", "cyber_shield"}
HIDE_KEYS = {"value", "password", "contrasena", "clave", "token", "api_key", "secret"}


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


LOG = _base_dir() / "logs" / "acciones.jsonl"
MAX_BYTES = 3 * 1024 * 1024


def _safe_args(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if k.lower() in HIDE_KEYS:
            out[k] = "***"
        elif isinstance(v, str) and len(v) > 120:
            out[k] = v[:120] + "…"
        else:
            out[k] = v
    return out


def record(tool: str, args: dict | None = None, who: str | None = None, result: str = "", ok: bool = True):
    try:
        entry = {"t": datetime.now().isoformat(timespec="seconds"), "tool": tool,
                 "args": _safe_args(args or {}), "who": who or "desconocido",
                 "sensitive": tool in SENSITIVE_TOOLS, "ok": ok,
                 "result": (str(result)[:200] if result else "")}
        with _lock:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            if LOG.exists() and LOG.stat().st_size > MAX_BYTES:
                LOG.rename(LOG.with_name("acciones.old.jsonl"))
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def recent(limit: int = 20, only_sensitive: bool = False) -> list[dict]:
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if only_sensitive and not e.get("sensitive"):
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def summary_today() -> str:
    today = datetime.now().date().isoformat()
    entries = [e for e in recent(500) if e.get("t", "").startswith(today)]
    if not entries:
        return "Hoy no he registrado ninguna acción todavía."
    from collections import Counter
    counts = Counter(e["tool"] for e in entries)
    top = ", ".join(f"{t} ({n})" for t, n in counts.most_common(6))
    sens = [e for e in entries if e.get("sensitive")]
    txt = f"Hoy he hecho {len(entries)} acciones. Lo más usado: {top}."
    if sens:
        txt += f" Acciones delicadas: {len(sens)} (" + ", ".join(sorted({e['tool'] for e in sens})) + ")."
    return txt


def audit_log_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "today").lower()
    if action in ("today", "hoy", "resumen", "summary"):
        return summary_today()
    if action in ("recent", "list", "ultimas"):
        n = int(p.get("limit") or 15)
        sens = bool(p.get("sensitive"))
        items = recent(n, only_sensitive=sens)
        if not items:
            return "No hay acciones registradas."
        lines = [f"{e['t'][11:16]} · {e['tool']}" + (" ⚠️" if e.get("sensitive") else "") +
                 (f" ({e['who']})" if e.get("who") and e["who"] != "desconocido" else "") +
                 (" ✗" if not e.get("ok") else "") for e in items]
        return "Últimas acciones:\n" + "\n".join(f"• {l}" for l in lines)
    return "Acciones: today (resumen de hoy), recent (últimas), recent con sensitive=true."
