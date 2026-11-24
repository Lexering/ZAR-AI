"""
projects.py — Centro de control de proyectos de ZAR

Vigila tus proyectos (código, webs, apps) y te dice de un vistazo qué está hecho,
qué falla y cuál es el siguiente paso.

  · "ZAR, añade el proyecto que tengo en C:\\dev\\virel-shop"
  · "¿Cómo van mis proyectos?"              → resumen de todos.
  · "¿Qué falta en el bot de trading?"      → estado de uno: git, tareas, web.
  · "¿Está online alberto-bot.com?"         → comprueba si tus webs responden.
  · Informe en PDF con el estado de todo.

De cada proyecto mira (solo lee, no toca nada):
  · Git: rama, cambios sin guardar, último commit, si está por detrás del remoto.
  · Tareas pendientes: TODO/FIXME en el código y líneas sin marcar de TODO.md / tareas.
  · Webs: si la URL responde y con qué código.
  · Último resultado: busca "passed/failed/error" en logs recientes del proyecto.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from actions import documents

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STORE = _base_dir() / "config" / "projects.json"
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".cs",
             ".php", ".rb", ".html", ".css", ".vue", ".svelte", ".sql"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next",
             "target", ".idea", ".vscode", "env", "site-packages"}
TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK|PENDIENTE)\b[: ]*(.{0,120})", re.IGNORECASE)


def _git(repo: Path, *args, timeout=15) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           errors="ignore", timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return ""


# ═══════════════════════════ ALMACÉN ═══════════════════════════
def load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {"projects": {}}


def save(data: dict):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add(path_or_name: str, url: str = "") -> str:
    data = load()
    path = Path(path_or_name.strip().strip('"')).expanduser()
    if not path.exists() or not path.is_dir():
        return f"No encuentro esa carpeta: {path}. Dame la ruta completa del proyecto."
    name = path.name
    data["projects"][name] = {"path": str(path), "url": url, "added": datetime.now().strftime("%Y-%m-%d")}
    save(data)
    extra = f" Vigilaré también {url}." if url else ""
    return f"Proyecto «{name}» añadido ({path}).{extra}"


def remove(name: str) -> str:
    data = load()
    key = _match(name, data)
    if not key:
        return f"No tengo ningún proyecto que se llame «{name}»."
    data["projects"].pop(key, None)
    save(data)
    return f"Proyecto «{key}» quitado de la lista (no se borra nada del disco)."


def _match(name: str, data: dict) -> str | None:
    name = (name or "").strip().lower()
    if not name:
        return None
    projs = data["projects"]
    if name in (k.lower() for k in projs):
        return next(k for k in projs if k.lower() == name)
    for k in projs:
        if name in k.lower() or k.lower() in name:
            return k
    return None


def set_url(name: str, url: str) -> str:
    data = load()
    key = _match(name, data)
    if not key:
        return f"No tengo el proyecto «{name}». Añádelo primero con su ruta."
    data["projects"][key]["url"] = url
    save(data)
    return f"Guardado: vigilaré {url} para «{key}»."


# ═══════════════════════════ ANÁLISIS DE UN PROYECTO ═══════════════════════════
def _scan_code(root: Path, max_files=4000) -> dict:
    todos, files, lines, n = [], 0, 0, 0
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() not in CODE_EXTS:
                continue
            files += 1
            n += 1
            if n > max_files:
                break
            try:
                newest = max(newest, p.stat().st_mtime)
                with open(p, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        lines += 1
                        m = TODO_RE.search(line)
                        if m and len(todos) < 60:
                            todos.append(f"{p.name}:{i} — {m.group(1).upper()} {m.group(2).strip()}")
            except Exception:
                pass
    return {"files": files, "lines": lines, "todos": todos, "newest": newest}


def _task_files(root: Path) -> list[str]:
    pending = []
    for name in ("TODO.md", "TODO.txt", "TASKS.md", "tareas.md", "PENDIENTE.md", "ROADMAP.md"):
        f = root / name
        if f.exists():
            try:
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    s = line.strip()
                    if re.match(r"[-*]\s*\[ \]", s) or re.match(r"[-*]\s+\w", s):
                        clean = re.sub(r"^[-*]\s*(\[ \])?\s*", "", s)[:120]
                        pending.append(f"{name}: {clean}")
            except Exception:
                pass
    return pending[:40]


def _recent_log_result(root: Path) -> str:
    """Busca el log más reciente y mira si acabó en éxito o error."""
    best, best_t = None, 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith((".log", ".out")) or fn in ("pytest.log", "test-results.txt"):
                p = Path(dirpath) / fn
                try:
                    t = p.stat().st_mtime
                    if t > best_t and time.time() - t < 7 * 86400:
                        best, best_t = p, t
                except Exception:
                    pass
    if not best:
        return ""
    try:
        tail = best.read_text(encoding="utf-8", errors="ignore")[-4000:].lower()
    except Exception:
        return ""
    if re.search(r"\b(\d+ failed|error|exception|traceback|fatal)\b", tail):
        return f"El log más reciente ({best.name}) tiene errores."
    if re.search(r"\b(\d+ passed|success|build succeeded|ok|done)\b", tail):
        return f"El log más reciente ({best.name}) salió bien."
    return ""


def check_url(url: str) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        import requests
        t0 = time.time()
        r = requests.get(url, timeout=12, allow_redirects=True,
                         headers={"User-Agent": "ZAR/1.0"})
        ms = (time.time() - t0) * 1000
        if r.status_code < 400:
            return f"{url} ONLINE ({r.status_code}, {ms:.0f} ms)"
        return f"{url} responde con error {r.status_code}"
    except Exception as e:
        return f"{url} NO responde ({type(e).__name__})"


def analyze(name: str, proj: dict) -> dict:
    root = Path(proj["path"])
    info = {"name": name, "path": str(root), "exists": root.exists()}
    if not root.exists():
        return info
    # Git
    if (root / ".git").exists():
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        status = _git(root, "status", "--porcelain")
        last = _git(root, "log", "-1", "--pretty=%cr — %s")
        ahead_behind = _git(root, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        changed = len([l for l in status.splitlines() if l.strip()])
        info["git"] = {"branch": branch, "changed": changed, "last": last}
        if ahead_behind and "\t" in ahead_behind:
            behind, ahead = ahead_behind.split("\t")[:2]
            info["git"]["ahead"] = int(ahead or 0)
            info["git"]["behind"] = int(behind or 0)
    code = _scan_code(root)
    info["code"] = code
    info["tasks"] = _task_files(root)
    info["log"] = _recent_log_result(root)
    if proj.get("url"):
        info["web"] = check_url(proj["url"])
    return info


def _line(info: dict) -> str:
    if not info.get("exists"):
        return f"«{info['name']}»: la carpeta ya no existe ({info['path']})."
    bits = []
    g = info.get("git")
    if g:
        s = f"rama {g['branch']}"
        if g["changed"]:
            s += f", {g['changed']} archivos sin guardar"
        if g.get("behind"):
            s += f", {g['behind']} commits por detrás del remoto"
        if g.get("ahead"):
            s += f", {g['ahead']} sin subir"
        s += f" (último: {g['last']})" if g.get("last") else ""
        bits.append(s)
    c = info.get("code", {})
    if c.get("files"):
        bits.append(f"{c['files']} archivos de código")
        if c["newest"]:
            dias = (time.time() - c["newest"]) / 86400
            bits.append("tocado hoy" if dias < 1 else f"sin tocar {dias:.0f} días")
    pend = len(c.get("todos", [])) + len(info.get("tasks", []))
    if pend:
        bits.append(f"{pend} tareas pendientes")
    if info.get("log"):
        bits.append(info["log"])
    if info.get("web"):
        bits.append(info["web"])
    return f"«{info['name']}»: " + "; ".join(bits) + "."


# ═══════════════════════════ RESÚMENES ═══════════════════════════
def overview() -> str:
    data = load()
    projs = data["projects"]
    if not projs:
        return ("No tienes proyectos guardados todavía. Dime la ruta de uno, por ejemplo: "
                "«añade el proyecto de C:\\dev\\mi-app».")
    lines = [_line(analyze(n, p)) for n, p in projs.items()]
    return f"Tienes {len(projs)} proyectos.\n" + "\n".join(f"• {l}" for l in lines)


def detail(name: str) -> str:
    data = load()
    key = _match(name, data)
    if not key:
        return f"No tengo ningún proyecto que se llame «{name}». ¿Quieres que lo añada?"
    info = analyze(key, data["projects"][key])
    out = [_line(info)]
    todos = info.get("code", {}).get("todos", [])
    tasks = info.get("tasks", [])
    if tasks:
        out.append("Tareas en tus listas:")
        out += [f"  · {t}" for t in tasks[:10]]
    if todos:
        out.append("TODO/FIXME en el código:")
        out += [f"  · {t}" for t in todos[:10]]
    return "\n".join(out)


REPORT_PROMPT = """Eres el jefe de proyectos del usuario. Con estos datos reales de sus proyectos, escribe
en español un parte de estado claro: qué va bien, qué está bloqueado o fallando y el siguiente paso
de cada proyecto. No inventes nada que no esté en los datos.

DATOS:
{data}

Devuelve SOLO JSON: {{"title": "Estado de mis proyectos", "spoken": "2-3 frases con lo más importante",
 "sections": [{{"heading": "<nombre del proyecto>", "paragraphs": ["estado y siguiente paso"],
                "bullets": ["puntos pendientes"]}}]}}"""


def report(fmt: str = "pdf") -> tuple[Path, dict]:
    data = load()
    projs = data["projects"]
    if not projs:
        raise RuntimeError("No hay proyectos guardados.")
    raw = []
    for n, p in projs.items():
        info = analyze(n, p)
        block = _line(info)
        todos = info.get("code", {}).get("todos", []) + info.get("tasks", [])
        if todos:
            block += "\nPendiente:\n" + "\n".join(f"- {t}" for t in todos[:20])
        raw.append(f"### {n}\n{block}")
    doc = documents._parse_json(documents.generate(
        REPORT_PROMPT.format(data="\n\n".join(raw)[:120_000]), json_mode=True))
    doc["subtitle"] = f"ZAR · Centro de proyectos · {datetime.now():%d/%m/%Y %H:%M}"
    path = documents.save_document(doc, fmt, name="Estado de proyectos")
    return path, doc


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def projects_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "overview").lower()
    name = (p.get("name") or "").strip()

    if action == "add":
        return add(p.get("path") or name, p.get("url", ""))
    if action == "remove":
        return remove(name)
    if action == "set_url":
        return set_url(name, p.get("url", ""))
    if action in ("overview", "all", "list"):
        return overview()
    if action in ("detail", "status"):
        if not name:
            return overview()
        return detail(name)
    if action in ("web", "url", "online"):
        data = load()
        if name:
            key = _match(name, data)
            url = data["projects"].get(key, {}).get("url") if key else None
            if not url:
                return check_url(name) if "." in name else f"«{name}» no tiene web guardada."
            return check_url(url)
        urls = [(n, pr.get("url")) for n, pr in data["projects"].items() if pr.get("url")]
        if not urls:
            return "Ninguno de tus proyectos tiene web guardada."
        return "\n".join(f"• {n}: {check_url(u)}" for n, u in urls)
    if action == "report":
        try:
            path, doc = report(p.get("format", "pdf"))
        except Exception as e:
            return f"No pude hacer el informe: {e}"
        documents.open_file(path)
        return f"Informe de proyectos listo. {doc.get('spoken', '')} Guardado y abierto: {path.name}."

    return "Acciones: add, remove, overview, detail, web, report, set_url."
