"""
protocols.py — Protocolos de ZAR (como el "House Party Protocol" de JARVIS)

Un protocolo es una lista de pasos que ZAR ejecuta con una sola orden:
  "Protocolo estudio", "Buenas noches, ZAR", "Activa el protocolo gaming"…

Tipos de paso (se guardan en config/protocols.json, puedes editarlo a mano):
  {"tool": "music_player", "args": {...}}   usar cualquier herramienta de ZAR
  {"open": "https://... | C:\\ruta\\archivo"} abrir web, carpeta o archivo
  {"timer": 50, "message": "..."}           avisar dentro de N minutos
  {"power": "lock|sleep|shutdown", "delay": 120}   bloquear, suspender o apagar
  {"wait": 2}                               esperar N segundos
  {"say": "..."}                            algo que ZAR debe decir
  {"ask": "..."}                            una tarea que ZAR hace él mismo al terminar
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STORE = _base_dir() / "config" / "protocols.json"

DEFAULTS = {
    "estudio": {
        "aliases": ["estudiar", "modo estudio", "a estudiar"],
        "steps": [
            {"tool": "music_player", "args": {"action": "play", "query": "lofi hip hop para estudiar"}},
            {"wait": 2},
            {"tool": "music_player", "args": {"action": "volume", "volume": 35}},
            {"timer": 50, "message": "Lleva 50 minutos estudiando. Toca un descanso de 10 minutos."},
            {"say": "Protocolo estudio activado: música suave y descanso en 50 minutos."},
            {"ask": "Pregúntale en una frase qué va a estudiar hoy y ofrécele abrir sus apuntes con use_document."},
        ],
    },
    "gaming": {
        "aliases": ["modo gaming", "a jugar", "juegos"],
        "steps": [
            {"tool": "music_player", "args": {"action": "stop"}},
            {"tool": "open_app", "args": {"app_name": "Steam"}},
            {"tool": "open_app", "args": {"app_name": "Discord"}},
            {"say": "Protocolo gaming activado. Steam y Discord en marcha."},
        ],
    },
    "buenas noches": {
        "aliases": ["me voy a dormir", "a dormir", "hasta mañana", "protocolo noche"],
        "steps": [
            {"tool": "music_player", "args": {"action": "stop"}},
            {"ask": "Despídete hasta mañana con una frase de ánimo para los estudios "
                    "y avisa de que el equipo se apagará en 2 minutos salvo que diga «cancela el apagado»."},
            {"power": "shutdown", "delay": 120},
        ],
    },
}

STEP_KEYS = {"tool", "open", "timer", "power", "wait", "say", "ask"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"\b(protocolo|el|la|de|modo|activa|activar|inicia|iniciar|alberto|zar)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ═══════════════════════════ ALMACÉN ═══════════════════════════
def load() -> dict:
    data = {}
    if STORE.exists():
        try:
            data = json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    changed = False
    for name, proto in DEFAULTS.items():
        if name not in data and f"-{name}" not in data.get("_deleted", []):
            data[name] = proto
            changed = True
    if changed or not STORE.exists():
        save(data)
    return data


def save(data: dict):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find(name: str) -> tuple[str, dict] | None:
    data = load()
    q = _norm(name)
    if not q:
        return None
    best = None
    for key, proto in data.items():
        if key.startswith("_"):
            continue
        names = [key] + list(proto.get("aliases", []))
        for n in names:
            nn = _norm(n)
            if nn == q:
                return key, proto
            if nn and (nn in q or q in nn):
                best = best or (key, proto)
    return best


# ═══════════════════════════ PASOS LOCALES ═══════════════════════════
def _power(kind: str, delay: int = 0) -> str:
    if os.name != "nt":
        return f"(energía «{kind}» solo en Windows)"
    if kind == "lock":
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return "PC bloqueado."
    if kind == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", str(max(0, int(delay)))], capture_output=True)
        return f"Apagado programado en {int(delay)} segundos."
    if kind == "restart":
        subprocess.run(["shutdown", "/r", "/t", str(max(0, int(delay)))], capture_output=True)
        return f"Reinicio programado en {int(delay)} segundos."
    if kind == "sleep":
        def go():
            time.sleep(max(0, int(delay)))
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        threading.Thread(target=go, daemon=True).start()
        return f"Suspensión en {int(delay)} segundos."
    return f"Tipo de energía desconocido: {kind}"


def cancel_shutdown() -> str:
    if os.name != "nt":
        return "Solo en Windows."
    r = subprocess.run(["shutdown", "/a"], capture_output=True)
    return "Apagado cancelado." if r.returncode == 0 else "No había ningún apagado programado."


def _timer(minutes: float, message: str) -> str:
    def fire():
        from actions import notify
        notify.alert(message, key=f"timer-{time.time()}", cooldown_s=0)
    t = threading.Timer(float(minutes) * 60, fire)
    t.daemon = True
    t.start()
    return f"Aviso programado en {minutes:g} minutos."


def run_local(step: dict) -> str:
    """Pasos que no necesitan a Gemini. Los de tipo 'tool' los ejecuta main.py."""
    if "open" in step:
        target = str(step["open"])
        try:
            if os.name == "nt":
                os.startfile(target)
            else:
                import webbrowser; webbrowser.open(target)
            return f"Abierto {target}."
        except Exception as e:
            return f"No pude abrir {target}: {e}"
    if "timer" in step:
        return _timer(step["timer"], step.get("message") or "Se acabó el tiempo.")
    if "power" in step:
        return _power(step["power"], step.get("delay", 0))
    if "wait" in step:
        time.sleep(min(float(step["wait"]), 30))
        return ""
    return ""


# ═══════════════════════════ CREAR POR VOZ ═══════════════════════════
CREATE_PROMPT = """Convierte esta descripción de una rutina en un protocolo para un asistente de voz.
Descripción del usuario: «{desc}»
Nombre del protocolo: «{name}»

Herramientas disponibles para pasos "tool" (usa solo estas, con sus argumentos):
{tools}

Tipos de paso permitidos:
{{"tool": "<nombre>", "args": {{...}}}}
{{"open": "<url o ruta>"}}
{{"timer": <minutos>, "message": "<aviso>"}}
{{"power": "lock|sleep|shutdown|restart", "delay": <segundos>}}
{{"wait": <segundos>}}
{{"say": "<frase corta que el asistente dirá>"}}
{{"ask": "<instrucción para que el asistente haga algo él mismo al final>"}}

Para abrir programas usa {{"tool": "open_app", "args": {{"app_name": "..."}}}}.
Para música usa {{"tool": "music_player", "args": {{"action": "play", "query": "..."}}}}.
Devuelve SOLO JSON: {{"aliases": ["otras formas de llamarlo"], "steps": [ ... ]}}"""


def create(name: str, description: str, tool_names: list[str], generate=None) -> dict:
    if generate is None:
        from actions.documents import generate as _g, _parse_json
        raw = _g(CREATE_PROMPT.format(desc=description, name=name, tools=", ".join(tool_names)), json_mode=True)
        proto = _parse_json(raw)
    else:
        proto = generate(description)
    steps = [s for s in proto.get("steps", []) if isinstance(s, dict) and STEP_KEYS & s.keys()]
    steps = [s for s in steps if "tool" not in s or s["tool"] in tool_names]
    if not steps:
        raise ValueError("No he sabido convertir eso en pasos. Descríbelo de otra forma.")
    proto = {"aliases": [a for a in proto.get("aliases", []) if isinstance(a, str)][:6], "steps": steps,
             "description": description}
    data = load()
    data[name.lower().strip()] = proto
    save(data)
    return proto


def delete(name: str) -> bool:
    found = find(name)
    if not found:
        return False
    data = load()
    data.pop(found[0], None)
    if found[0] in DEFAULTS:                                   # que no reaparezca
        data.setdefault("_deleted", []).append(f"-{found[0]}")
    save(data)
    return True


def describe(proto: dict) -> str:
    parts = []
    for s in proto.get("steps", []):
        if "tool" in s:
            a = s.get("args", {})
            parts.append(f"{s['tool']}({', '.join(f'{k}={v}' for k, v in a.items())})")
        elif "open" in s:  parts.append(f"abrir {s['open']}")
        elif "timer" in s: parts.append(f"aviso en {s['timer']} min")
        elif "power" in s: parts.append(f"{s['power']} en {s.get('delay', 0)} s")
        elif "say" in s:   parts.append(f"decir «{s['say']}»")
        elif "ask" in s:   parts.append(f"tarea: {s['ask'][:60]}")
    return "; ".join(parts)


def list_text() -> str:
    data = load()
    names = [k for k in data if not k.startswith("_")]
    return "Protocolos disponibles: " + ", ".join(names) + "." if names else "No hay protocolos."
