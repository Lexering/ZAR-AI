"""
agenda.py — Calendario y recordatorios de ZAR

Lleva tu agenda en el propio equipo (sin cuentas ni internet) y te avisa de las citas a tiempo,
por voz si estás delante o por Telegram si no estás.

  · "Recuérdame llamar al dentista mañana a las 10"
  · "Tengo reunión el viernes a las 5 en la oficina"
  · "¿Qué tengo hoy?" / "¿Qué tengo esta semana?"
  · "¿Cuál es mi próxima cita?"
  · "Borra la cita del dentista"

Entiende fechas habladas (mañana, el viernes, en dos horas…) con la IA.
Avisa por defecto 15 minutos antes (puedes decir otra antelación).
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from actions import documents

CHECK_EVERY_S = 30
DEFAULT_LEAD_MIN = 15
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STORE = _base_dir() / "config" / "agenda.json"


# ═══════════════════════════ ALMACÉN ═══════════════════════════
def load() -> list[dict]:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("events", [])
    except Exception:
        return []


def save(events: list[dict]):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_id(events: list[dict]) -> int:
    return max([e.get("id", 0) for e in events], default=0) + 1


# ═══════════════════════════ FECHAS (con IA) ═══════════════════════════
PARSE_PROMPT = """Convierte esta frase en una cita de calendario. Ahora es {now} ({weekday}).
Frase: «{text}»

Devuelve SOLO JSON:
{{"title": "título corto y claro de la cita (sin la fecha)",
  "datetime": "YYYY-MM-DD HH:MM" (interpreta fechas relativas como mañana, el viernes, en 2 horas; si no
               dicen hora usa 09:00; si no dicen fecha pero sí hora y ya pasó hoy, usa mañana),
  "remind_before_min": <minutos antes para avisar, por defecto {lead}>,
  "place": "lugar si lo menciona, si no cadena vacía"}}"""


def parse_event(text: str) -> dict:
    now = datetime.now()
    raw = documents.generate(PARSE_PROMPT.format(
        now=now.strftime("%Y-%m-%d %H:%M"), weekday=DIAS[now.weekday()], text=text, lead=DEFAULT_LEAD_MIN),
        json_mode=True)
    data = documents._parse_json(raw)
    dt = datetime.strptime(data["datetime"], "%Y-%m-%d %H:%M")
    return {"title": (data.get("title") or text).strip(), "when": dt.strftime("%Y-%m-%d %H:%M"),
            "remind_before_min": int(data.get("remind_before_min") or DEFAULT_LEAD_MIN),
            "place": (data.get("place") or "").strip()}


def _when(e: dict) -> datetime:
    return datetime.strptime(e["when"], "%Y-%m-%d %H:%M")


def _human(dt: datetime) -> str:
    now = datetime.now()
    d = (dt.date() - now.date()).days
    hora = dt.strftime("%H:%M")
    if d == 0:
        return f"hoy a las {hora}"
    if d == 1:
        return f"mañana a las {hora}"
    if 0 < d < 7:
        return f"el {DIAS[dt.weekday()]} a las {hora}"
    return f"el {dt.day} de {MESES[dt.month - 1]} a las {hora}"


# ═══════════════════════════ OPERACIONES ═══════════════════════════
def add(text: str) -> str:
    try:
        ev = parse_event(text)
    except Exception as e:
        return f"No he entendido la fecha. Dímelo con día y hora. ({e})"
    events = load()
    ev["id"] = _next_id(events)
    ev["notified"] = False
    events.append(ev)
    save(events)
    place = f" en {ev['place']}" if ev["place"] else ""
    return f"Apuntado: {ev['title']} {_human(_when(ev))}{place}. Te avisaré {ev['remind_before_min']} minutos antes."


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    return s.strip()


def delete(query: str) -> str:
    events = load()
    q = _norm(query)
    match = [e for e in events if q and q in _norm(e["title"])]
    if not match:
        return f"No encuentro ninguna cita con «{query}»."
    for e in match:
        events.remove(e)
    save(events)
    if len(match) == 1:
        return f"Borrada: {match[0]['title']} ({_human(_when(match[0]))})."
    return f"Borradas {len(match)} citas que coincidían con «{query}»."


def _sorted_future() -> list[dict]:
    now = datetime.now()
    return sorted([e for e in load() if _when(e) >= now - timedelta(minutes=1)], key=_when)


def list_day(day: datetime | None = None) -> str:
    day = day or datetime.now()
    evs = [e for e in sorted(load(), key=_when) if _when(e).date() == day.date()]
    label = "hoy" if day.date() == datetime.now().date() else day.strftime("el %d/%m")
    if not evs:
        return f"No tienes nada en la agenda {label}."
    lines = [f"{_when(e).strftime('%H:%M')} · {e['title']}" + (f" ({e['place']})" if e.get("place") else "") for e in evs]
    return f"Para {label} tienes {len(evs)}: " + "; ".join(lines) + "."


def list_week() -> str:
    now = datetime.now()
    end = now + timedelta(days=7)
    evs = [e for e in _sorted_future() if _when(e) <= end]
    if not evs:
        return "No tienes nada en la agenda esta semana."
    lines = [f"{_human(_when(e))} · {e['title']}" for e in evs]
    return f"Esta semana tienes {len(evs)}: " + "; ".join(lines) + "."


def next_event() -> str:
    evs = _sorted_future()
    if not evs:
        return "No tienes ninguna cita próxima."
    e = evs[0]
    place = f" en {e['place']}" if e.get("place") else ""
    return f"Tu próxima cita: {e['title']} {_human(_when(e))}{place}."


def today_for_briefing() -> str:
    evs = [e for e in sorted(load(), key=_when) if _when(e).date() == datetime.now().date()
           and _when(e) >= datetime.now()]
    if not evs:
        return ""
    return "Hoy tienes: " + ", ".join(f"{e['title']} a las {_when(e).strftime('%H:%M')}" for e in evs) + "."


# ═══════════════════════════ RECORDATORIOS ═══════════════════════════
class Reminder:
    def __init__(self, log=print):
        self.log = log

    def check_once(self):
        from actions import notify
        now = datetime.now()
        events = load()
        changed = False
        for e in events:
            if e.get("notified"):
                continue
            dt = _when(e)
            lead = int(e.get("remind_before_min", DEFAULT_LEAD_MIN))
            if now >= dt - timedelta(minutes=lead):
                mins = max(0, int((dt - now).total_seconds() // 60))
                place = f" en {e['place']}" if e.get("place") else ""
                when = "ahora" if mins <= 1 else f"en {mins} minutos"
                notify.alert(f"Recordatorio: {e['title']} {when}{place}.", important=True,
                             key=f"agenda-{e['id']}", cooldown_s=0)
                e["notified"] = True
                changed = True
        # limpiar citas viejas (más de 2 días pasadas)
        fresh = [e for e in events if _when(e) >= now - timedelta(days=2)]
        if len(fresh) != len(events):
            events, changed = fresh, True
        if changed:
            save(events)

    def run_forever(self):
        time.sleep(20)
        while True:
            try:
                self.check_once()
            except Exception as e:
                self.log(f"AGENDA: {e}")
            time.sleep(CHECK_EVERY_S)


_reminder: Reminder | None = None


def start(log=print):
    global _reminder
    if _reminder is None:
        _reminder = Reminder(log)
        threading.Thread(target=_reminder.run_forever, daemon=True, name="agenda").start()
        log("AGENDA: recordatorios activos")
    return _reminder


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def agenda_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "today").lower()
    text = (p.get("text") or "").strip()
    if action in ("add", "nueva", "recordar", "crear"):
        if not text:
            return "Dime qué apunto y cuándo. Ejemplo: «dentista mañana a las 10»."
        return add(text)
    if action in ("today", "hoy"):
        return list_day()
    if action in ("week", "semana"):
        return list_week()
    if action in ("next", "proxima", "siguiente"):
        return next_event()
    if action in ("list", "todo"):
        evs = _sorted_future()
        if not evs:
            return "No tienes citas próximas."
        return "Tus próximas citas: " + "; ".join(f"{e['title']} {_human(_when(e))}" for e in evs[:10]) + "."
    if action in ("delete", "borrar", "eliminar"):
        return delete(text)
    return "Acciones: add, today, week, next, list, delete."
