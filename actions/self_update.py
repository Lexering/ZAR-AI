"""
ZAR - updates itself from GitHub (no more downloading ZIPs by hand).

Checks a few minutes after starting and every 6 hours; also on demand («ZAR, actualízate» or the button in
«Personalizar»). If GitHub has a newer version.txt it downloads the code, copies it over the app (never touching
config/, memory/, logs/ or keys), installs new Python packages if requirements changed, and restarts ZAR (exit code 75,
the guardian starts the new version at once).

config/update.json: {"repo": "Lexering/ZAR-AI", "branch": "main", "token": ""}
The token is only needed while the repository is private (a GitHub fine-grained token with read-only "Contents").
"""
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CFG = BASE_DIR / "config" / "update.json"
VERSION = BASE_DIR / "version.txt"
KEEP_DIRS = {"config", "memory", "logs", "dist", ".git", ".github", "venv", "__pycache__"}
KEEP_FILES = {"API", "API.txt", "my_voice.pkl"}
RESTART_CODE = 75
_state = {"checking": False, "last": "", "remote": None}
_restart_cb = None      # set by main: restart when ZAR isn't talking


def _cfg() -> dict:
    c = {"repo": "Lexering/ZAR-AI", "branch": "main", "token": "", "auto": True}
    try:
        c.update(json.loads(CFG.read_text(encoding="utf-8-sig")))
    except Exception:
        pass
    return c


def local_version() -> int:
    try:
        return int(VERSION.read_text().strip() or 0)
    except Exception:
        return 0


def _get(url: str, token: str, accept: str = "application/vnd.github.raw", timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ZAR-updater", "Accept": accept})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def remote_version(c: dict | None = None) -> int | None:
    c = c or _cfg()
    urls = [f"https://api.github.com/repos/{c['repo']}/contents/version.txt?ref={c['branch']}"]
    if not c.get("token"):
        urls.insert(0, f"https://raw.githubusercontent.com/{c['repo']}/{c['branch']}/version.txt")
    for u in urls:
        try:
            return int(_get(u, c.get("token", "")).decode().strip())
        except Exception as e:
            _state["last"] = f"no pude mirar GitHub: {e}"
    return None


def _copy_tree(src: Path, dst: Path) -> int:
    n = 0
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if rel.parts[0] in KEEP_DIRS or p.name in KEEP_FILES or "__pycache__" in rel.parts:
            continue
        if rel.parts[0] == "installer" and len(rel.parts) > 1 and rel.parts[1] == "deps":
            continue
        t = dst / rel
        if p.is_dir():
            t.mkdir(parents=True, exist_ok=True)
        else:
            t.parent.mkdir(parents=True, exist_ok=True)
            if not t.exists() or t.read_bytes() != p.read_bytes():
                shutil.copy2(p, t)
                n += 1
    return n


def update(force: bool = False) -> str:
    """Download and install the newest version. Returns a short report (Spanish)."""
    if _state["checking"]:
        return "Ya estoy actualizándome."
    _state["checking"] = True
    try:
        c = _cfg()
        mine, theirs = local_version(), remote_version(c)
        _state["remote"] = theirs
        if theirs is None:
            return "No puedo conectar con GitHub para actualizarme" + (" (el repositorio es privado: falta el token)." if not c.get("token") else ".")
        if theirs <= mine and not force:
            return f"Ya tengo la última versión ({mine})."
        url = f"https://api.github.com/repos/{c['repo']}/zipball/{c['branch']}"
        data = _get(url, c.get("token", ""), accept="application/vnd.github+json", timeout=180)
        old_req = (BASE_DIR / "requirements.txt").read_bytes() if (BASE_DIR / "requirements.txt").exists() else b""
        tmp = Path(os.environ.get("TEMP", "/tmp")) / f"zar_update_{int(time.time())}"
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
        root = next(p for p in tmp.iterdir() if p.is_dir())
        if not (root / "main.py").exists():
            return "La descarga no parece ZAR; no toco nada."
        n = _copy_tree(root, BASE_DIR)
        new_req = (BASE_DIR / "requirements.txt").read_bytes() if (BASE_DIR / "requirements.txt").exists() else b""
        if new_req != old_req:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(BASE_DIR / "requirements.txt")],
                           capture_output=True, timeout=1800, creationflags=flags)
        shutil.rmtree(tmp, ignore_errors=True)
        _state["last"] = f"actualizado a la versión {theirs} ({n} archivos)"
        print(f"[ZAR] Actualización: versión {mine} -> {theirs}, {n} archivos")
        if _restart_cb:
            threading.Thread(target=_restart_cb, args=(theirs,), daemon=True).start()
        return f"Actualizado a la versión {theirs}. Me reinicio en unos segundos."
    except Exception as e:
        _state["last"] = f"error: {e}"
        return f"No he podido actualizarme: {e}"
    finally:
        _state["checking"] = False


def status() -> dict:
    return {"version": local_version(), "remote": _state["remote"], "last": _state["last"], "checking": _state["checking"]}


def start(restart_cb=None, first_delay: int = 180, every_h: float = 6):
    """Background check: a few minutes after starting, then every few hours."""
    global _restart_cb
    _restart_cb = restart_cb

    def loop():
        time.sleep(first_delay)
        while True:
            if _cfg().get("auto", True):
                try:
                    r = update()
                    print(f"[ZAR] Comprobando actualizaciones: {r}")
                except Exception as e:
                    print(f"[ZAR] Actualizador: {e}")
            time.sleep(every_h * 3600)

    threading.Thread(target=loop, daemon=True, name="zar-updater").start()


def update_tool(parameters: dict | None = None) -> str:
    act = ((parameters or {}).get("action") or "update").lower()
    if act in ("check", "version"):
        mine, theirs = local_version(), remote_version()
        if theirs is None:
            return f"Tengo la versión {mine}; no he podido mirar GitHub."
        return f"Tengo la versión {mine}; en GitHub está la {theirs}." + (" Hay actualización." if theirs > mine else " Estoy al día.")
    return update(force=act == "force")
