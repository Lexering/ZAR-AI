"""
ZAR - quick notes ("Notas" panel on the desktop).
Added by voice («ZAR, apunta que mañana traigo la calculadora»), from the desktop or from the notes window.
Data lives in memory/notes.json (never sent in updates, never uploaded to GitHub).
"""
import json
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA = BASE_DIR / "memory" / "notes.json"
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items: list[dict]):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DATA)


def all_notes() -> list[dict]:
    with _lock:
        items = _load()
    return sorted(items, key=lambda n: (n.get("done", False), -(1 if n.get("pin") else 0), -n.get("ts", 0)))


def add(text: str, pin: bool = False) -> dict | None:
    text = " ".join((text or "").split())[:300]
    if not text:
        return None
    with _lock:
        items = _load()
        note = {"id": int(time.time() * 1000), "text": text, "done": False, "pin": bool(pin), "ts": time.time()}
        items.append(note)
        _save(items[-200:])
    return note


def _find(items, key):
    key = str(key).strip().lower()
    for n in items:
        if str(n["id"]) == key:
            return n
    return next((n for n in items if key and key in n["text"].lower()), None)


def update(key, **changes) -> dict | None:
    with _lock:
        items = _load()
        n = _find(items, key)
        if not n:
            return None
        for k, v in changes.items():
            if k in ("text", "done", "pin"):
                n[k] = v
        _save(items)
        return n


def delete(key) -> bool:
    with _lock:
        items = _load()
        n = _find(items, key)
        if not n:
            return False
        items.remove(n)
        _save(items)
        return True


def clear_done() -> int:
    with _lock:
        items = _load()
        keep = [n for n in items if not n.get("done")]
        _save(keep)
        return len(items) - len(keep)


def list_text() -> str:
    items = [n for n in all_notes() if not n.get("done")]
    if not items:
        return "No hay notas pendientes."
    return f"{len(items)} nota(s): " + "; ".join(n["text"] for n in items[:12]) + ("…" if len(items) > 12 else "")


def note_tool(parameters: dict) -> str:
    """Voice tool «desktop_note»."""
    p = parameters or {}
    act = (p.get("action") or "add").lower()
    text = (p.get("text") or "").strip()
    if act == "add":
        n = add(text, bool(p.get("pin")))
        return f"Apuntado: {n['text']}" if n else "¿Qué apunto?"
    if act == "list":
        return list_text()
    if act in ("done", "check"):
        n = update(text, done=True)
        return f"Tachada: {n['text']}" if n else "No encuentro esa nota."
    if act == "delete":
        return "Nota borrada." if delete(text) else "No encuentro esa nota."
    if act == "clear":
        return f"Quitadas {clear_done()} nota(s) hechas."
    if act == "open":
        try:
            from actions import hud_bridge
            hud_bridge.open_window("notes")
            return "Notas abiertas."
        except Exception as e:
            return f"No pude abrir las notas: {e}"
    return "Acción de notas desconocida."
