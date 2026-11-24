"""
ZAR add-ons: new tools that plug into ANY ZAR without replacing its code.

  - office_pdf     LibreOffice + PDF tools          (actions/libreoffice.py)
  - world_monitor  live world map and alerts         (actions/world_monitor.py)
  - boards         own project manager (kanban)      (actions/boards.py)

main.py only needs two small hooks (added by tools/addon_patch.py):
  TOOL_DECLARATIONS += addons.DECLARATIONS         -> Gemini knows the new tools
  if name in addons.HANDLERS: ...                  -> they get executed

The map and the boards are served by a tiny local web server (127.0.0.1:5058) unless the
newer HUD (hud_bridge with windows) is present, in which case that one is used.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
PAGES = BASE_DIR / "wallpaper"
PORT = 5058
TOKEN_FILE = BASE_DIR / "config" / "addons_token.txt"

DECLARATIONS: list[dict] = []
HANDLERS: dict = {}
_server_started = False


def _token() -> str:
    try:
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    except Exception:
        pass
    t = secrets.token_hex(20)
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(t, encoding="utf-8")
    except Exception:
        pass
    return t


# ───────────────────────── tool registry ─────────────────────────
def _speak_of(jarvis):
    return getattr(jarvis, "speak", None) if jarvis is not None else None


def _register():
    try:
        from actions import libreoffice
        DECLARATIONS.append(libreoffice.TOOL_DECLARATION)
        HANDLERS["office_pdf"] = lambda args, j=None: libreoffice.office_tool(
            args, speak=_speak_of(j), current_file=getattr(getattr(j, "ui", None), "current_file", None))
    except Exception as e:
        print(f"[ZAR] Add-on office_pdf disabled: {e}")
    try:
        from actions import world_monitor
        DECLARATIONS.append(world_monitor.TOOL_DECLARATION)
        HANDLERS["world_monitor"] = lambda args, j=None: world_monitor.world_tool(args, speak=_speak_of(j))
        world_monitor.start()
    except Exception as e:
        print(f"[ZAR] Add-on world_monitor disabled: {e}")
    try:
        from actions import boards
        DECLARATIONS.append(boards.TOOL_DECLARATION)
        HANDLERS["boards"] = lambda args, j=None: boards.boards_tool(args, speak=_speak_of(j))
        boards.start()
    except Exception as e:
        print(f"[ZAR] Add-on boards disabled: {e}")


# ───────────────────────── local pages ─────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-ZAR-Token", ""), _token())

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/ui/"):
            name = u.path[4:].strip("/")
            f = PAGES / f"{name}.html"
            if name not in ("world", "boards") or not f.exists():
                self._json({"ok": False, "error": "not found"}, 404)
                return
            body = f.read_text(encoding="utf-8").replace("__HUD_TOKEN__", _token()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            return
        if not self._authorized():
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return
        try:
            if u.path == "/world_events":
                from actions import world_monitor
                self._json({"ok": True, **world_monitor.get_events(force="refresh" in (u.query or ""))})
            elif u.path == "/boards":
                from actions import boards
                self._json(boards.api({"op": "get"}))
            else:
                self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def do_POST(self):
        if not self._authorized():
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if urlparse(self.path).path == "/boards":
                from actions import boards
                self._json(boards.api(data))
            else:
                self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)


def start_server() -> bool:
    global _server_started
    if _server_started:
        return True
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True, name="addons-web").start()
        _server_started = True
        return True
    except OSError as e:
        print(f"[ZAR] Add-ons web server not started: {e}")
        return False


def _app_window(url: str) -> bool:
    for exe in (shutil.which("msedge"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", shutil.which("chrome"),
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if exe and Path(exe).exists():
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1320,820"])
                return True
            except Exception:
                pass
    return webbrowser.open(url)


def open_page(page: str) -> bool:
    """Open the world map / boards window with whatever this ZAR has."""
    try:
        from actions import hud_bridge as hb
        if page in getattr(hb, "WINDOWS", {}) and hasattr(hb, "open_window"):
            if hb.open_window(page):
                return True
    except Exception:
        pass
    if not start_server():
        return False
    return _app_window(f"http://127.0.0.1:{PORT}/ui/{page}")


_register()
