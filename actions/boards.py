"""
Boards - ZAR's own project manager (monday.com style), local and free.

Boards -> columns (To do / In progress / Done ...) -> tasks with owner, due date, priority, tags, notes.
Data lives in memory/boards.json (never sent in updates, never uploaded to GitHub).

Used by:
  - the voice tool  "boards"            (add / move / done / list / pending / summary / open ...)
  - the HUD window  /ui/boards          (kanban with drag & drop)   data: GET/POST /boards
  - a reminder loop that warns about tasks due today or overdue (voice + Telegram, once a day each)
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA = BASE_DIR / "memory" / "boards.json"
DEFAULT_COLUMNS = ["To do", "In progress", "Done"]
PRIORITIES = ["low", "medium", "high", "urgent"]

_lock = threading.RLock()
_reminders_started = False


# ───────────────────────── storage ─────────────────────────
def _empty() -> dict:
    return {"boards": [], "reminded": {}}


def load() -> dict:
    with _lock:
        try:
            d = json.loads(DATA.read_text(encoding="utf-8-sig"))
            d.setdefault("boards", []); d.setdefault("reminded", {})
            return d
        except Exception:
            return _empty()


def save(d: dict):
    with _lock:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        tmp = DATA.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(DATA)


def _now() -> float:
    return time.time()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def _done_col(b: dict) -> str:
    for c in b["columns"]:
        if _norm(c) in ("done", "hecho", "terminado", "completado", "finished", "completed"):
            return c
    return b["columns"][-1]


def _is_done(b: dict, t: dict) -> bool:
    return t.get("column") == _done_col(b)


# ───────────────────────── parsing helpers ─────────────────────────
WEEKDAYS = {"monday": 0, "lunes": 0, "tuesday": 1, "martes": 1, "wednesday": 2, "miercoles": 2,
            "thursday": 3, "jueves": 3, "friday": 4, "viernes": 4, "saturday": 5, "sabado": 5,
            "sunday": 6, "domingo": 6}


def parse_due(text: str | None) -> str | None:
    """'2026-12-01', '01/12', 'today', 'mañana', 'viernes', 'in 3 days', 'next week' -> 'YYYY-MM-DD'."""
    if not text:
        return None
    t = _norm(text)
    today = date.today()
    if t in ("", "none", "ninguna", "sin fecha", "no date"):
        return None
    if t in ("today", "hoy"):
        return today.isoformat()
    if t in ("tomorrow", "manana"):
        return (today + timedelta(days=1)).isoformat()
    if t in ("pasado manana", "day after tomorrow"):
        return (today + timedelta(days=2)).isoformat()
    if t in ("next week", "la semana que viene", "proxima semana", "semana que viene"):
        return (today + timedelta(days=7)).isoformat()
    m = re.search(r"(?:in|en|dentro de)\s+(\d+)\s+(?:days?|dias?)", t)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    for name, wd in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", t):
            delta = (wd - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t.replace(" ", "-")) or re.match(r"^(\d{4})\s(\d{1,2})\s(\d{1,2})$", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})\s(\d{1,2})(?:\s(\d{2,4}))?$", t)          # 01/12 or 01/12/2026 (day first)
    if m:
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            d = date(y, int(m.group(2)), int(m.group(1)))
            if not m.group(3) and d < today:
                d = date(y + 1, d.month, d.day)
            return d.isoformat()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text.strip()[:10]).date().isoformat()
    except Exception:
        return None


def parse_priority(text: str | None) -> str:
    t = _norm(text or "")
    if t in ("urgent", "urgente", "critical", "critica"):
        return "urgent"
    if t in ("high", "alta", "importante", "important"):
        return "high"
    if t in ("low", "baja"):
        return "low"
    return "medium"


# ───────────────────────── finding things ─────────────────────────
def _score(query: str, text: str) -> float:
    q, x = _norm(query), _norm(text)
    if not q or not x:
        return 0
    if q == x:
        return 100
    if q in x:
        return 80 - (len(x) - len(q)) * 0.1
    qw, xw = set(q.split()), set(x.split())
    common = len(qw & xw)
    return common * 20 / max(len(qw), 1)


def find_board(d: dict, name: str | None, create: bool = False) -> dict | None:
    bs = d["boards"]
    if not name:
        return bs[0] if bs else (create_board(d, "My tasks") if create else None)
    best = max(bs, key=lambda b: _score(name, b["name"]), default=None)
    if best and _score(name, best["name"]) >= 20:
        return best
    return create_board(d, name.strip()) if create else None


def find_task(d: dict, query: str, board: str | None = None):
    best, best_s = None, 0
    for b in d["boards"]:
        if board and _score(board, b["name"]) < 20:
            continue
        for t in b["tasks"]:
            s = _score(query, t["title"]) + (5 if not _is_done(b, t) else 0)
            if s > best_s:
                best, best_s = (b, t), s
    return best if best_s >= 20 else None


def find_column(b: dict, name: str | None) -> str | None:
    if not name:
        return None
    alias = {"todo": "To do", "to do": "To do", "pendiente": "To do", "por hacer": "To do",
             "doing": "In progress", "en curso": "In progress", "en progreso": "In progress",
             "haciendo": "In progress", "in progress": "In progress",
             "done": "Done", "hecho": "Done", "hecha": "Done", "terminado": "Done", "terminada": "Done"}
    target = alias.get(_norm(name), name)
    best = max(b["columns"], key=lambda c: _score(target, c))
    return best if _score(target, best) >= 20 else None


# ───────────────────────── operations ─────────────────────────
def create_board(d: dict, name: str, columns: list[str] | None = None) -> dict:
    b = {"id": uuid.uuid4().hex[:8], "name": name.strip() or "Board", "columns": list(columns or DEFAULT_COLUMNS),
         "tasks": [], "created": _now()}
    d["boards"].append(b)
    return b


def add_task(d: dict, title: str, board: str | None = None, column: str | None = None, owner: str = "",
             due: str | None = None, priority: str | None = None, tags=None, notes: str = "") -> tuple[dict, dict]:
    b = find_board(d, board, create=True)
    col = find_column(b, column) or b["columns"][0]
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.split(",") if x.strip()]
    t = {"id": uuid.uuid4().hex[:10], "title": title.strip(), "column": col, "owner": (owner or "").strip(),
         "due": parse_due(due) if due else None, "priority": parse_priority(priority), "tags": tags or [],
         "notes": notes or "", "created": _now(), "updated": _now(), "done_at": None}
    b["tasks"].append(t)
    return b, t


def move_task(b: dict, t: dict, column: str) -> bool:
    col = find_column(b, column) if column not in b["columns"] else column
    if not col:
        return False
    t["column"] = col
    t["updated"] = _now()
    t["done_at"] = _now() if col == _done_col(b) else None
    return True


def update_task(b: dict, t: dict, fields: dict):
    for k in ("title", "owner", "notes"):
        if fields.get(k) is not None:
            t[k] = str(fields[k]).strip()
    if "due" in fields:
        t["due"] = parse_due(fields["due"]) if fields["due"] else None
    if fields.get("priority"):
        t["priority"] = parse_priority(fields["priority"])
    if fields.get("tags") is not None:
        tg = fields["tags"]
        t["tags"] = [x.strip() for x in tg.split(",") if x.strip()] if isinstance(tg, str) else list(tg)
    if fields.get("column"):
        move_task(b, t, fields["column"])
    t["updated"] = _now()


# ───────────────────────── queries ─────────────────────────
def _due_label(due: str | None) -> str:
    if not due:
        return ""
    d = date.fromisoformat(due)
    diff = (d - date.today()).days
    if diff < 0:
        return f"overdue {-diff} d"
    if diff == 0:
        return "due today"
    if diff == 1:
        return "due tomorrow"
    return f"due {d.strftime('%d %b')}"


def open_tasks(d: dict, board: str | None = None, owner: str | None = None):
    for b in d["boards"]:
        if board and _score(board, b["name"]) < 20:
            continue
        for t in b["tasks"]:
            if _is_done(b, t):
                continue
            if owner and _score(owner, t.get("owner", "")) < 20:
                continue
            yield b, t


def pending(d: dict, scope: str = "all", board: str | None = None, owner: str | None = None) -> str:
    today = date.today()
    rows = []
    for b, t in open_tasks(d, board, owner):
        due = date.fromisoformat(t["due"]) if t.get("due") else None
        if scope == "today" and not (due and due <= today):
            continue
        if scope == "overdue" and not (due and due < today):
            continue
        if scope == "week" and not (due and due <= today + timedelta(days=7)):
            continue
        rows.append((b, t, due))
    if not rows:
        return {"today": "Nothing due today.", "overdue": "Nothing overdue.",
                "week": "Nothing due this week."}.get(scope, "No open tasks.")
    pr = {p: i for i, p in enumerate(reversed(PRIORITIES))}
    rows.sort(key=lambda r: (r[2] or date.max, pr.get(r[1].get("priority"), 9)))
    lines = [f"- {t['title']} [{b['name']} · {t['column']}"
             + (f" · {t['owner']}" if t.get("owner") else "")
             + (f" · {_due_label(t['due'])}" if t.get("due") else "")
             + (f" · {t['priority']}" if t.get("priority") in ("high", "urgent") else "") + "]"
             for b, t, _ in rows[:15]]
    more = f"\n(+{len(rows) - 15} more)" if len(rows) > 15 else ""
    return f"{len(rows)} open task(s):\n" + "\n".join(lines) + more


def summary(d: dict) -> str:
    if not d["boards"]:
        return "There are no boards yet. Say for example: 'create a board called Virel Shop'."
    today = date.today()
    out = []
    for b in d["boards"]:
        counts = {c: 0 for c in b["columns"]}
        overdue = 0
        for t in b["tasks"]:
            counts[t["column"]] = counts.get(t["column"], 0) + 1
            if t.get("due") and not _is_done(b, t) and date.fromisoformat(t["due"]) < today:
                overdue += 1
        out.append(f"{b['name']}: " + ", ".join(f"{n} {c.lower()}" for c, n in counts.items())
                   + (f", {overdue} OVERDUE" if overdue else ""))
    return "Boards:\n" + "\n".join(out)


# ───────────────────────── HUD API ─────────────────────────
def api(op: dict) -> dict:
    """Operations from the boards window. Every call returns the whole data set."""
    with _lock:
        d = load()
        kind = op.get("op")
        b = next((x for x in d["boards"] if x["id"] == op.get("board")), None)
        t = next((x for x in (b["tasks"] if b else []) if x["id"] == op.get("task")), None)
        if kind == "create_board":
            b = create_board(d, op.get("name") or "New board")
        elif kind == "rename_board" and b:
            b["name"] = (op.get("name") or b["name"]).strip()
        elif kind == "delete_board" and b:
            d["boards"].remove(b)
        elif kind == "add_column" and b and op.get("name"):
            if op["name"] not in b["columns"]:
                b["columns"].insert(max(len(b["columns"]) - 1, 0), op["name"].strip())
        elif kind == "rename_column" and b and op.get("old") in b["columns"] and op.get("name"):
            new = op["name"].strip()
            b["columns"][b["columns"].index(op["old"])] = new
            for x in b["tasks"]:
                if x["column"] == op["old"]:
                    x["column"] = new
        elif kind == "delete_column" and b and op.get("name") in b["columns"] and len(b["columns"]) > 1:
            b["columns"].remove(op["name"])
            for x in b["tasks"]:
                if x["column"] == op["name"]:
                    x["column"] = b["columns"][0]
        elif kind == "add_task" and b:
            _, t = add_task(d, op.get("title") or "New task", b["name"], op.get("column"), op.get("owner", ""),
                            op.get("due"), op.get("priority"), op.get("tags"), op.get("notes", ""))
            if op.get("column") in b["columns"]:
                t["column"] = op["column"]
        elif kind == "update_task" and b and t:
            update_task(b, t, op.get("fields") or {})
        elif kind == "move_task" and b and t:
            move_task(b, t, op.get("column", ""))
            if isinstance(op.get("index"), int):
                b["tasks"].remove(t)
                same = [i for i, x in enumerate(b["tasks"]) if x["column"] == t["column"]]
                pos = same[op["index"]] if op["index"] < len(same) else (same[-1] + 1 if same else len(b["tasks"]))
                b["tasks"].insert(pos, t)
        elif kind == "delete_task" and b and t:
            b["tasks"].remove(t)
        save(d)
        return {"ok": True, "boards": d["boards"], "today": date.today().isoformat()}


# ───────────────────────── reminders ─────────────────────────
def _remind_loop():
    while True:
        try:
            d = load()
            today = date.today().isoformat()
            due_now = []
            for b, t in open_tasks(d):
                if t.get("due") and t["due"] <= today and d["reminded"].get(t["id"]) != today:
                    due_now.append((b, t))
            if due_now and 8 <= datetime.now().hour <= 21:
                from actions.addons_notify import alert as _alert
                names = "; ".join(f"{t['title']} ({_due_label(t['due'])})" for _, t in due_now[:5])
                more = f" and {len(due_now) - 5} more" if len(due_now) > 5 else ""
                _alert(f"Tasks due: {names}{more}.", key="boards-" + today + str(len(due_now)),
                             cooldown_s=3600)
                for _, t in due_now:
                    d["reminded"][t["id"]] = today
                d["reminded"] = {k: v for k, v in d["reminded"].items() if v == today}
                save(d)
        except Exception as e:
            print(f"[ZAR] Boards reminders: {e}")
        time.sleep(1800)


def start():
    global _reminders_started
    if _reminders_started:
        return
    _reminders_started = True
    threading.Thread(target=_remind_loop, daemon=True, name="boards-reminders").start()


# ───────────────────────── voice tool ─────────────────────────
def boards_tool(parameters: dict, player=None, speak=None) -> str:
    a = parameters or {}
    action = (a.get("action") or "summary").lower()
    try:
        with _lock:
            d = load()
            if action == "open":
                from actions import addons
                return "Boards window opened." if addons.open_page("boards") else "I couldn't open the boards window."
            if action == "create_board":
                name = (a.get("board") or a.get("title") or "").strip()
                if not name:
                    return "Tell me the board name."
                if find_board(d, name):
                    return f"There's already a board called {find_board(d, name)['name']}."
                cols = [c.strip() for c in (a.get("columns") or "").split(",") if c.strip()] or None
                b = create_board(d, name, cols); save(d)
                return f"Board '{b['name']}' created with columns: {', '.join(b['columns'])}."
            if action == "add_task":
                title = (a.get("title") or "").strip()
                if not title:
                    return "Tell me what the task is."
                b, t = add_task(d, title, a.get("board"), a.get("column"), a.get("owner", ""), a.get("due"),
                                a.get("priority"), a.get("tags"), a.get("notes", ""))
                save(d)
                extra = ", ".join(x for x in (t["owner"] and f"for {t['owner']}", _due_label(t["due"]),
                                                t["priority"] if t["priority"] != "medium" else "") if x)
                return f"Added '{t['title']}' to {b['name']} ({t['column']}{', ' + extra if extra else ''})."
            if action in ("move", "done", "update", "delete"):
                hit = find_task(d, a.get("title", ""), a.get("board"))
                if not hit:
                    return f"I can't find a task like '{a.get('title', '')}'."
                b, t = hit
                if action == "done":
                    move_task(b, t, _done_col(b)); msg = f"'{t['title']}' marked as done."
                elif action == "move":
                    if not move_task(b, t, a.get("column", "")):
                        return f"{b['name']} has no column '{a.get('column')}'. Columns: {', '.join(b['columns'])}."
                    msg = f"'{t['title']}' moved to {t['column']}."
                elif action == "delete":
                    b["tasks"].remove(t); msg = f"'{t['title']}' deleted."
                else:
                    fields = {k: a.get(k) for k in ("owner", "due", "priority", "tags", "notes", "column")
                              if a.get(k) not in (None, "")}
                    if a.get("new_title"):
                        fields["title"] = a["new_title"]
                    update_task(b, t, fields); msg = f"'{t['title']}' updated."
                save(d)
                return msg
            if action == "pending":
                return pending(d, (a.get("scope") or "all").lower(), a.get("board"), a.get("owner"))
            if action == "list":
                b = find_board(d, a.get("board"))
                if not b:
                    return "No boards yet." if not d["boards"] else f"Boards: {', '.join(x['name'] for x in d['boards'])}."
                parts = []
                for c in b["columns"]:
                    ts = [t["title"] for t in b["tasks"] if t["column"] == c]
                    parts.append(f"{c} ({len(ts)}): " + ("; ".join(ts[:8]) if ts else "-"))
                return f"{b['name']}:\n" + "\n".join(parts)
            return summary(d)
    except Exception as e:
        return f"Boards error: {e}"


TOOL_DECLARATION = {
    "name": "boards",
    "description": (
        "ZAR's own project manager (like monday.com): boards with columns and tasks (owner, due date, "
        "priority, tags). Use for ANY request about tasks, to-dos, projects, boards, kanban, who does what, "
        "deadlines, what's pending or overdue. Examples: 'add task X to board Y for Friday', 'mark X as done', "
        "'move X to in progress', 'what's pending today?', 'what's overdue?', 'show the Virel Shop board', "
        "'create a board called Z', 'open the boards'. action: add_task | done | move | update | delete | "
        "pending | list | summary | create_board | open."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "add_task | done | move | update | delete | pending | list | summary | create_board | open"},
            "title": {"type": "STRING", "description": "Task title (or words of it to find the task)"},
            "board": {"type": "STRING", "description": "Board name (optional; first board if empty; created if new when adding)"},
            "column": {"type": "STRING", "description": "Column, e.g. To do, In progress, Done"},
            "owner": {"type": "STRING", "description": "Person responsible"},
            "due": {"type": "STRING", "description": "Due date: YYYY-MM-DD, today, tomorrow, friday, in 3 days..."},
            "priority": {"type": "STRING", "description": "low | medium | high | urgent"},
            "tags": {"type": "STRING", "description": "Comma-separated tags"},
            "notes": {"type": "STRING", "description": "Extra notes"},
            "new_title": {"type": "STRING", "description": "New title when renaming a task (action=update)"},
            "scope": {"type": "STRING", "description": "For pending: all | today | overdue | week"},
            "columns": {"type": "STRING", "description": "For create_board: comma-separated column names"},
        },
        "required": ["action"],
    },
}
