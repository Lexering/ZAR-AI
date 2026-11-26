"""
hud_bridge.py — Publishes ZAR's state for the animated desktop wallpaper and serves its controls.

Local server on http://127.0.0.1:5055 (only reachable from this PC).

Open (no token, as before):
  GET  /state           state, voice levels, conversation log, system info
  POST /mute            mutes / unmutes the microphone
  POST /file            "Send to -> ZAR" from Windows Explorer
Wallpaper / chat window (need the X-ZAR-Token header, so other websites can't use them):
  GET  /news            world news headlines from today and the two previous days
  GET  /weather         current weather for the chosen or detected location
  GET  /geocode?q=City  city search for the weather settings
  GET  /settings        weather settings
  POST /settings        save weather settings
  POST /open?app=X      opens god's eye | gmail | whatsapp | chatgpt ; /open?news=N opens a headline
  POST /news_speak      ZAR reads the news aloud + PDF sent to Telegram
  POST /window?page=P   opens the chat or settings window (P = chat | settings)
  POST /chat            sends a typed message (and optionally an attached file) to ZAR
  POST /upload?name=F   uploads a file or photo from the chat window
  POST /action?kind=K   create_file | create_image: ZAR asks what to create
  GET  /ui/chat, /ui/settings   the chat and settings windows
"""

from __future__ import annotations

import json
import os
import sys
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

PORT = 5055
BASE_DIR = Path(__file__).resolve().parent.parent          # ZAR folder (where main.py is)
CONFIG_DIR = BASE_DIR / "config"
WALLPAPER_DIR = BASE_DIR / "wallpaper"
TOKEN_FILE = CONFIG_DIR / "hud_token.txt"
SETTINGS_FILE = CONFIG_DIR / "hud_settings.json"
APPS_CONFIG = BASE_DIR / "hud_apps.json"

_lock = threading.Lock()
_data = {
    "name": "ZAR",
    "state": "OFFLINE",
    "level_out": 0.0,
    "level_in": 0.0,
    "log": [],
    "sys": {},
    "started": time.time(),
    "updated": time.time(),
}
_log: deque = deque(maxlen=8)
_chat_log: deque = deque(maxlen=60)   # longer history for the chat window
_muted_getter = None
_mute_toggler = None        # safe function to mute/unmute the mic from the wallpaper
_dictating = False
_file_handler = None        # "Send to -> ZAR": ZAR announces the file
_quiet_file_handler = None  # chat window: loads the file without announcing it
_text_sender = None         # sends a text prompt to ZAR's live session
_speaker = None             # who is speaking (voice recognition)
_standby = False            # waiting for the wake word "ZAR"
_guard = False              # guard mode
_news_delivery = ""         # status of the news PDF sent to Telegram


def _token() -> str:
    """Per-PC secret for the desktop controls. Created automatically the first time."""
    try:
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    except Exception:
        pass
    try:
        import secrets
        tok = secrets.token_hex(20)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(tok, encoding="utf-8")
        return tok
    except Exception:
        return ""


_TOKEN_RE = re.compile(r'(const TOKEN = ")([0-9a-f]{40})(")')


def _with_token(html: str, tok: str) -> str:
    """This PC's token into a page: the placeholder, or a token from another PC (copied files)."""
    html = html.replace("__HUD_TOKEN__", tok)
    return _TOKEN_RE.sub(lambda m: m.group(1) + tok + m.group(3), html)


def _sync_wallpaper():
    """Puts this PC's token into the wallpaper (shared code ships with a placeholder) and refreshes
    Lively Wallpaper's own copy so updates show up without re-adding the wallpaper."""
    src = WALLPAPER_DIR / "index.html"
    tok = _token()
    if not src.exists() or not tok:
        return
    try:
        html = src.read_text(encoding="utf-8")
        fixed = _with_token(html, tok)
        if fixed != html:
            html = fixed
            src.write_text(html, encoding="utf-8")
        local = os.environ.get("LOCALAPPDATA", "")
        roots = [Path(local) / "Lively Wallpaper"] + list((Path(local) / "Packages").glob("*Lively*")) if local else []
        for root in roots:
            if not root.exists():
                continue
            for f in root.rglob("index.html"):
                if f.resolve() == src.resolve():
                    continue
                try:
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if "<title>ZAR HUD</title>" in txt and txt != html:
                    f.write_text(html, encoding="utf-8")
                    print(f"[ZAR] HUD: Lively wallpaper refreshed ({f})")
    except Exception as e:
        print(f"[ZAR] HUD: wallpaper sync skipped: {e}")


# ───────────────────────── hooks that main.py sets ─────────────────────────

def set_standby(on: bool):
    global _standby
    _standby = bool(on)


def set_guard(on: bool):
    global _guard
    _guard = bool(on)


def set_speaker(name):
    global _speaker
    _speaker = name
    with _lock:
        _data["speaker"] = name


def set_file_handler(fn):
    global _file_handler
    _file_handler = fn


def set_quiet_file_handler(fn):
    """main.py: load a file as ZAR's current document without him announcing it."""
    global _quiet_file_handler
    _quiet_file_handler = fn


def set_text_sender(fn):
    """main.py: function(text) that sends a prompt to ZAR's live session."""
    global _text_sender
    _text_sender = fn


def set_dictating(on: bool):
    """While dictating, the wallpaper shows the «Dictating» state."""
    global _dictating
    _dictating = bool(on)
_started = False


def set_mute_toggler(fn):
    """main.py registers here how to mute the mic (from the UI thread)."""
    global _mute_toggler
    _mute_toggler = fn


def set_name(name: str):
    with _lock:
        _data["name"] = name


def set_state(state: str):
    with _lock:
        _data["state"] = str(state).upper()
        _data["updated"] = time.time()


def set_level(kind: str, value: float):
    v = max(0.0, min(1.0, float(value)))
    with _lock:
        key = "level_out" if kind == "out" else "level_in"
        # fast rise, smooth fall: the reactor does not flicker
        _data[key] = v if v > _data[key] else _data[key] * 0.82 + v * 0.18


def add_log(text: str):
    text = (text or "").strip()
    if not text:
        return
    who = "sys"
    for prefix, tag in (("You:", "user"), ("Tú:", "user"), ("ZAR:", "ai"),
                        ("ZAR:", "ai"), ("ZAR:", "ai"), ("SYS:", "sys"), ("ERR:", "err"),
                        ("GESTO:", "sys"), ("GESTOS:", "sys"), ("DICTADO:", "dict"), ("VOZ:", "sys"), ("AVISO:", "sys"),
                        ("SEGURIDAD:", "sys"), ("RED:", "sys"), ("TELEGRAM:", "sys"), ("ACTIVACIÓN:", "sys"),
                        ("AVISOS:", "sys"),
                        ("MÚSICA:", "sys")):
        if text.startswith(prefix):
            who, text = tag, text[len(prefix):].strip()
            break
    entry = {"who": who, "text": text[:220], "t": time.time()}
    if who == "user" and _speaker:
        entry["speaker"] = _speaker
    with _lock:
        _log.append(entry)
        _data["log"] = list(_log)
        if who in ("user", "ai", "err"):
            _chat_log.append({**entry, "text": text[:2000]})


def snapshot() -> dict:
    """Snapshot of the current state (for the mobile panel)."""
    with _lock:
        d = dict(_data)
    d["muted"] = bool(_muted_getter()) if _muted_getter else False
    d["standby"] = _standby
    d["guard"] = _guard
    d["dictating"] = _dictating
    if _speaker:
        d["speaker"] = _speaker
    return d


def _sample_system():
    try:
        import psutil
    except ImportError:
        return
    psutil.cpu_percent(None)
    last_net = psutil.net_io_counters()
    last_t = time.time()
    while True:
        time.sleep(1.0)
        try:
            now = time.time()
            net = psutil.net_io_counters()
            dt = max(now - last_t, 1e-3)
            info = {
                "cpu": psutil.cpu_percent(None),
                "ram": psutil.virtual_memory().percent,
                "disk": psutil.disk_usage("C:\\" if psutil.WINDOWS else "/").percent,
                "down_kbps": (net.bytes_recv - last_net.bytes_recv) / dt / 1024,
                "up_kbps": (net.bytes_sent - last_net.bytes_sent) / dt / 1024,
                "uptime": now - psutil.boot_time(),
                "procs": len(psutil.pids()),
            }
            bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            if bat is not None:
                info["battery"] = bat.percent
                info["plugged"] = bool(bat.power_plugged)
            last_net, last_t = net, now
            with _lock:
                _data["sys"] = info
        except Exception:
            pass


def _owner() -> str:
    try:
        from actions.owner_profile import get_owner_name
        return get_owner_name()
    except Exception:
        return "the user"


def _send_to_alberto(text: str) -> bool:
    if not _text_sender:
        return False
    threading.Thread(target=_text_sender, args=(text,), daemon=True).start()
    return True


def _http_json(url: str, timeout: float = 8):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ZAR HUD)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ───────────────────────── quick-access buttons ─────────────────────────

WEB_APPS = {
    "gmail": "https://mail.google.com/",
    "whatsapp": "https://web.whatsapp.com/",
    "chatgpt": "https://chatgpt.com/",
    "gemini": "https://gemini.google.com/",
    "youtube": "https://www.youtube.com/",
    "drive": "https://drive.google.com/",
    "classroom": "https://classroom.google.com/",
    "scholar": "https://scholar.google.com/",
    "translate": "https://translate.google.com/",
    "notebooklm": "https://notebooklm.google.com/",
    "outlook": "https://outlook.live.com/",
    "teams": "https://teams.microsoft.com/",
    "onedrive": "https://onedrive.live.com/",
    "office": "https://www.office.com/",
    "canva": "https://www.canva.com/",
    "duolingo": "https://www.duolingo.com/",
    "khan": "https://www.khanacademy.org/",
    "wikipedia": "https://es.wikipedia.org/",
    "spotify": "https://open.spotify.com/",
    "calendar": "https://calendar.google.com/",
    "docs": "https://docs.google.com/",
    "quizlet": "https://quizlet.com/",
    "claude": "https://claude.ai/",
    "perplexity": "https://www.perplexity.ai/",
}

# ───────────────────────── desktop customisation (config/desktop.json) ─────────────────────────
DESKTOP_FILE = CONFIG_DIR / "desktop.json"
DESKTOP_DEFAULT = {
    "hidden": [],                         # panels not shown: weather, tasks, exams, files, pomodoro, apps, news
    "accent": "rose",                     # rose | blue | green | violet | amber | red
    "bg": True,                           # animated background
    "apps": [{"key": k} for k in ("classroom", "drive", "youtube", "scholar", "notebooklm", "translate", "gemini", "gmail")],
    "pomo": [25, 5],
}


def load_desktop() -> dict:
    d = dict(DESKTOP_DEFAULT)
    try:
        d.update(json.loads(DESKTOP_FILE.read_text(encoding="utf-8-sig")))
    except Exception:
        pass
    return d


def save_desktop(new: dict) -> dict:
    d = load_desktop()
    if isinstance(new.get("hidden"), list):
        d["hidden"] = [str(x)[:20] for x in new["hidden"]][:20]
    if str(new.get("accent", "")) in ("rose", "blue", "green", "violet", "amber", "red"):
        d["accent"] = new["accent"]
    if "bg" in new:
        d["bg"] = bool(new["bg"])
    if isinstance(new.get("apps"), list):
        apps = []
        for a in new["apps"][:16]:
            if a.get("key") in WEB_APPS:
                apps.append({"key": a["key"]})
            elif a.get("url") and a.get("name"):
                apps.append({"name": str(a["name"])[:24], "url": str(a["url"])[:500]})
        d["apps"] = apps
    if isinstance(new.get("pomo"), list) and len(new["pomo"]) == 2:
        try:
            d["pomo"] = [max(1, min(180, int(new["pomo"][0]))), max(1, min(60, int(new["pomo"][1])))]
        except (TypeError, ValueError):
            pass
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DESKTOP_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    d["rev"] = time.time()
    with _lock:
        _data["desktop_rev"] = d["rev"]
    return d


def open_link(target: str):
    """Custom shortcut from the desktop: a web address, or a program / file / folder on this PC."""
    target = (target or "").strip()
    if not target:
        return False, "empty"
    if target.startswith(("http://", "https://")):
        _open_target(target)
        return True, "web"
    if "." in target and " " not in target and "\\" not in target and "/" not in target[:8]:
        _open_target("https://" + target)          # «youtube.com»
        return True, "web"
    p = os.path.expandvars(os.path.expanduser(target))
    if os.path.exists(p):
        _open_target(p)
        return True, "path"
    return False, "not found"
ZAR_CONFIG = CONFIG_DIR / "zar.json"


def zar_profile() -> dict:
    """Name and language of the student (config/zar.json, written by the installer)."""
    try:
        d = json.loads(ZAR_CONFIG.read_text(encoding="utf-8-sig"))
    except Exception:
        d = {}
    name = d.get("name") or _owner()
    return {"owner": "" if name == "the user" else name, "lang": (d.get("lang") or "es")[:2]}
GODSEYE_WORDS = (("god", "eye"), ("ojo", "dios"))     # words that identify the God's Eye View app


def _config_path(key: str):
    """Optional override: hud_apps.json next to main.py, e.g. {"godseye": "C:\\\\...\\\\God's Eye View.lnk"}."""
    try:
        return (json.loads(APPS_CONFIG.read_text(encoding="utf-8")) or {}).get(key)
    except Exception:
        return None


def _find_godseye():
    custom = _config_path("godseye")
    if custom and os.path.exists(custom):
        return custom
    env = os.environ
    dirs = [
        os.path.join(env.get("APPDATA", ""), r"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"),
        os.path.join(env.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(env.get("PROGRAMDATA", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(env.get("USERPROFILE", ""), "Desktop"),
        os.path.join(env.get("USERPROFILE", ""), "OneDrive", "Desktop"),
        os.path.join(env.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
    ]
    for base in dirs:
        if not os.path.isdir(base):
            continue
        for root, _, files in os.walk(base):
            for f in files:
                low = f.lower().replace("'", "").replace("’", "")
                if not low.endswith((".lnk", ".url", ".exe", ".appref-ms")):
                    continue
                if any(all(w in low for w in words) for words in GODSEYE_WORDS):
                    return os.path.join(root, f)
    return _find_installed_app()


def _find_installed_app():
    """Apps from the Microsoft Store or installed as web apps have no normal shortcut: ask Windows."""
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        apps = json.loads(out or "[]")
        if isinstance(apps, dict):
            apps = [apps]
        for a in apps:
            low = str(a.get("Name", "")).lower().replace("'", "").replace("’", "")
            if any(all(w in low for w in words) for words in GODSEYE_WORDS):
                return "shell:AppsFolder\\" + a["AppID"]
    except Exception as e:
        print(f"[ZAR] HUD: couldn't list installed apps: {e}")
    return None


def _open_target(target: str):
    if target.startswith(("http://", "https://")):
        import webbrowser
        webbrowser.open(target)
    elif target.lower().startswith("shell:"):
        import subprocess
        subprocess.Popen(["explorer.exe", target])
    else:
        os.startfile(target)   # Windows: opens .lnk / .exe / .url like a double-click


# ───────────────────────── God's Eye View (runs inside Pinokio) ─────────────────────────

PINOKIO_PORTS = (42000, 80)


def _pinokio_homes():
    homes = []
    try:   # Pinokio saves its home folder in its config
        cfg = Path(os.environ.get("APPDATA", "")) / "Pinokio" / "config.json"
        h = json.loads(cfg.read_text(encoding="utf-8")).get("home")
        if h:
            homes.append(Path(h))
    except Exception:
        pass
    for h in (Path("C:/pinokio"), Path.home() / "pinokio", Path("D:/pinokio")):
        if h not in homes:
            homes.append(h)
    return [h for h in homes if (h / "api").is_dir()]


def _matches_godseye(text: str) -> bool:
    low = text.lower().replace("'", "").replace("’", "")
    return any(all(w in low for w in words) for words in GODSEYE_WORDS)


def _find_pinokio_app():
    """Folder of God's Eye View inside Pinokio's api folder (by folder name or by its title)."""
    for home in _pinokio_homes():
        for d in sorted((home / "api").iterdir()):
            if not d.is_dir():
                continue
            if _matches_godseye(d.name):
                return d.name
            for meta in ("pinokio.js", "pinokio.json", "pinokio_meta.json", "README.md"):
                try:
                    head = (d / meta).read_text(encoding="utf-8", errors="ignore")[:4000]
                except Exception:
                    continue
                if _matches_godseye(head):
                    return d.name
    return None


def _pinokio_port():
    import socket
    for port in PINOKIO_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return port
        except OSError:
            continue
    return None


def _launch_pinokio() -> bool:
    env = os.environ
    for exe in (os.path.join(env.get("LOCALAPPDATA", ""), "Programs", "Pinokio", "Pinokio.exe"),
                os.path.join(env.get("PROGRAMFILES", r"C:\Program Files"), "Pinokio", "Pinokio.exe")):
        if os.path.exists(exe):
            os.startfile(exe)
            return True
    try:
        import subprocess
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        apps = json.loads(out or "[]")
        for a in ([apps] if isinstance(apps, dict) else apps):
            if "pinokio" in str(a.get("Name", "")).lower():
                _open_target("shell:AppsFolder\\" + a["AppID"])
                return True
    except Exception as e:
        print(f"[ZAR] HUD: couldn't look for Pinokio: {e}")
    return False


def _open_app_window(url: str):
    exe = _browser_exe()
    if exe:
        import subprocess
        subprocess.Popen([exe, f"--app={url}", "--window-size=1400,900"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        _open_target(url)


def open_godseye():
    custom = _config_path("godseye")
    if custom and not str(custom).startswith("http") and os.path.exists(custom):
        _open_target(custom)
        return True, os.path.basename(custom)
    port = _pinokio_port()
    if port is None:
        if not _launch_pinokio():
            return False, "Pinokio not found. Is it installed?"
        for _ in range(60):                       # wait up to 30 s for Pinokio to start
            time.sleep(0.5)
            port = _pinokio_port()
            if port:
                break
        if port is None:
            return True, "Pinokio is starting… press God's Eye again in a few seconds"
    if custom and str(custom).startswith("http"):
        _open_app_window(custom)
        return True, "God's Eye View"
    folder = _find_pinokio_app()
    if not folder:
        return True, "Pinokio opened (couldn't find God's Eye View in it automatically)"
    base = f"http://localhost:{port}" if port != 80 else "http://localhost"
    _open_app_window(f"{base}/p/{quote(folder)}")
    print(f"[ZAR] HUD: God's Eye View → {base}/p/{folder}")
    return True, "God's Eye View"


def open_app(key: str):
    key = (key or "").lower()
    if key == "godseye":
        return open_godseye()
    url = _config_path(key) or WEB_APPS.get(key)
    if not url:
        return False, "unknown app"
    _open_target(url)
    return True, key


# ───────────────────────── chat and settings windows ─────────────────────────

WINDOWS = {"chat": (440, 720), "settings": (440, 600), "customize": (560, 780), "world": (1320, 820), "boards": (1320, 820)}


def _browser_exe():
    env = os.environ
    for p in (
        os.path.join(env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(env.get("PROGRAMFILES", r"C:\Program Files"), r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(env.get("PROGRAMFILES", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(env.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    ):
        if p and os.path.exists(p):
            return p
    return None


def open_window(page: str, extra: str = "") -> bool:
    if page not in WINDOWS:
        return False
    w, h = WINDOWS[page]
    url = f"http://127.0.0.1:{PORT}/ui/{page}" + (f"?{extra}" if extra else "")
    exe = _browser_exe()
    if not exe:
        import webbrowser
        webbrowser.open(url)
        return True
    x, y = 200, 80
    try:
        import ctypes
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        x = max(0, sw - w - 420)          # just left of the wallpaper panels
        if page in ("world", "boards"):
            x = max(0, (sw - w) // 2)
    except Exception:
        pass
    import subprocess
    subprocess.Popen([exe, f"--app={url}", f"--window-size={w},{h}", f"--window-position={x},{y}"],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return True


def _page(name: str) -> bytes | None:
    p = WALLPAPER_DIR / f"{name}.html"
    if not p.exists():
        return None
    html = _with_token(p.read_text(encoding="utf-8"), _token())
    return html.encode("utf-8")


# ───────────────────────── chat, uploads and create actions ─────────────────────────

def _uploads_dir() -> Path:
    try:
        from actions import documents
        d = documents.output_dir() / "Uploads"
    except Exception:
        d = Path.home() / "Documents" / "ZAR" / "Uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(name: str, data: bytes) -> Path:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", os.path.basename(name or "file")).strip() or "file"
    stem, ext = os.path.splitext(name)
    folder = _uploads_dir()
    path = folder / name
    i = 2
    while path.exists():
        path = folder / f"{stem} ({i}){ext}"
        i += 1
    path.write_bytes(data)
    return path


# ───────────────────────── "Mis archivos": the folder on the desktop ─────────────────────────

_IMG = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}


def _open_path(p: Path):
    import subprocess
    if sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", str(p)])
    elif os.name == "nt":
        os.startfile(str(p))
    else:
        subprocess.Popen(["xdg-open", str(p)])


def _file_in_folder(name: str) -> Path | None:
    p = (_uploads_dir() / os.path.basename(name or "")).resolve()
    return p if p.is_file() and p.parent == _uploads_dir().resolve() else None


def list_files() -> dict:
    folder = _uploads_dir()
    items = []
    for p in folder.iterdir():
        if p.is_file() and not p.name.startswith("."):
            st = p.stat()
            ext = p.suffix.lower()
            kind = ("image" if ext in _IMG else "pdf" if ext == ".pdf" else
                    "doc" if ext in (".doc", ".docx", ".odt", ".txt", ".rtf", ".pages") else
                    "sheet" if ext in (".xls", ".xlsx", ".csv", ".ods", ".numbers") else
                    "video" if ext in (".mp4", ".mov", ".avi", ".mkv") else "audio" if ext in (".mp3", ".m4a", ".wav") else "file")
            items.append({"name": p.name, "size": st.st_size, "mtime": time.strftime("%d/%m %H:%M", time.localtime(st.st_mtime)),
                          "ts": st.st_mtime, "kind": kind})
    items.sort(key=lambda x: x["ts"], reverse=True)
    return {"ok": True, "folder": str(folder), "items": items[:300]}


def files_action(body: dict) -> dict:
    a = body.get("action", "")
    if a == "open_folder":
        _open_path(_uploads_dir())
        return {"ok": True, "msg": "Carpeta abierta"}
    p = _file_in_folder(body.get("name", ""))
    if p is None:
        return {"ok": False, "msg": "No encuentro ese archivo"}
    if a == "open":
        _open_path(p)
        return {"ok": True, "msg": f"Abriendo {p.name}"}
    if a == "use":
        ok, info = chat(body.get("text", ""), str(p))
        return {"ok": ok, "msg": "Se lo he pasado a ZAR" if ok else info}
    if a == "delete":
        try:
            trash = _uploads_dir() / ".papelera"     # not gone for good: a hidden bin inside the folder
            trash.mkdir(exist_ok=True)
            p.replace(trash / f"{int(time.time())}-{p.name}")
            return {"ok": True, "msg": f"{p.name} quitado"}
        except Exception as e:
            return {"ok": False, "msg": f"No se pudo quitar: {e}"}
    return {"ok": False, "msg": "Acción desconocida"}


def chat(text: str, file_path: str = "") -> tuple[bool, str]:
    text = (text or "").strip()
    if not _text_sender:
        return False, "ZAR isn't connected yet"
    if file_path:
        p = Path(file_path)
        if not p.is_file():
            return False, "the attached file no longer exists"
        if _quiet_file_handler:
            try:
                _quiet_file_handler(str(p))
            except Exception as e:
                print(f"[ZAR] HUD: could not load the file: {e}")
        add_log(f"You: 📎 {p.name}" + (f" — {text}" if text else ""))
        kind = "photo" if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic") else "file"
        prompt = (f"[SISTEMA] {_owner()} attached a {kind} in the desktop chat: «{p.name}» (path: {p}). "
                  "It is now your current document. "
                  + (f"His message: «{text}». Do what he asks with it." if text
                     else "Ask him in one short sentence what he wants you to do with it."))
        return _send_to_alberto(prompt), "sent"
    if not text:
        return False, "empty message"
    add_log(f"You: {text}")
    return _send_to_alberto(f"[Typed by {_owner()} in the desktop chat] {text}"), "sent"


ACTIONS = {
    "create_file": ("[SISTEMA] the user pressed «Create a file» in the desktop chat. Ask him in one short sentence what "
                    "file he wants (what it should contain and whether Word or PDF). When he answers, create it with "
                    "create_document and tell him where it is saved."),
    "create_image": ("[SISTEMA] the user pressed «Create an image» in the desktop chat. Ask him in one short sentence what "
                     "image he wants. When he answers, create it with the image tool and tell him where it is saved."),
}


def run_action(kind: str) -> bool:
    prompt = ACTIONS.get(kind)
    return bool(prompt) and _send_to_alberto(prompt)


# ───────────────────────── weather ─────────────────────────

WMO = {
    0: ("Clear sky", "☀️", "🌙"), 1: ("Mainly clear", "🌤️", "🌙"), 2: ("Partly cloudy", "⛅", "☁️"),
    3: ("Overcast", "☁️", "☁️"), 45: ("Fog", "🌫️", "🌫️"), 48: ("Freezing fog", "🌫️", "🌫️"),
    51: ("Light drizzle", "🌦️", "🌧️"), 53: ("Drizzle", "🌦️", "🌧️"), 55: ("Heavy drizzle", "🌧️", "🌧️"),
    56: ("Freezing drizzle", "🌧️", "🌧️"), 57: ("Freezing drizzle", "🌧️", "🌧️"),
    61: ("Light rain", "🌦️", "🌧️"), 63: ("Rain", "🌧️", "🌧️"), 65: ("Heavy rain", "🌧️", "🌧️"),
    66: ("Freezing rain", "🌧️", "🌧️"), 67: ("Freezing rain", "🌧️", "🌧️"),
    71: ("Light snow", "🌨️", "🌨️"), 73: ("Snow", "🌨️", "🌨️"), 75: ("Heavy snow", "❄️", "❄️"),
    77: ("Snow grains", "🌨️", "🌨️"), 80: ("Light showers", "🌦️", "🌧️"), 81: ("Showers", "🌧️", "🌧️"),
    82: ("Violent showers", "⛈️", "⛈️"), 85: ("Snow showers", "🌨️", "🌨️"), 86: ("Snow showers", "🌨️", "🌨️"),
    95: ("Thunderstorm", "⛈️", "⛈️"), 96: ("Thunderstorm, hail", "⛈️", "⛈️"), 99: ("Thunderstorm, hail", "⛈️", "⛈️"),
}
_weather = {"t": 0.0, "data": None}
_geo = {"t": 0.0, "loc": None}


def load_settings() -> dict:
    try:
        s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("location", None)        # None = detect automatically
    s.setdefault("units", "C")
    return s


def save_settings(new: dict) -> dict:
    s = load_settings()
    loc = new.get("location", s["location"])
    if loc is not None:
        loc = {"name": str(loc.get("name", ""))[:80], "lat": float(loc["lat"]), "lon": float(loc["lon"])}
    s["location"] = loc
    s["units"] = "F" if str(new.get("units", s["units"])).upper() == "F" else "C"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    _weather["t"] = 0.0                    # refresh on the next request
    with _lock:
        _data["weather_changed"] = time.time()   # the wallpaper reloads the weather when it sees this
    return s


def detect_location() -> dict | None:
    if _geo["loc"] and time.time() - _geo["t"] < 6 * 3600:
        return _geo["loc"]
    loc = None
    try:
        d = _http_json("https://ipapi.co/json/")
        if d.get("latitude") is not None:
            loc = {"name": d.get("city") or d.get("region") or "Your location",
                   "lat": float(d["latitude"]), "lon": float(d["longitude"])}
    except Exception:
        pass
    if loc is None:
        try:
            d = _http_json("http://ip-api.com/json/?fields=status,city,lat,lon")
            if d.get("status") == "success":
                loc = {"name": d.get("city") or "Your location", "lat": float(d["lat"]), "lon": float(d["lon"])}
        except Exception:
            pass
    if loc:
        _geo.update(t=time.time(), loc=loc)
    return loc


def get_weather(force: bool = False) -> dict:
    if not force and _weather["data"] and time.time() - _weather["t"] < 600:
        return _weather["data"]
    s = load_settings()
    auto = s["location"] is None
    loc = detect_location() if auto else s["location"]
    if not loc:
        return {"ok": False, "error": "Couldn't detect your location"}
    unit = "fahrenheit" if s["units"] == "F" else "celsius"
    d = _http_json(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        f"&timezone=auto&forecast_days=1&temperature_unit={unit}&wind_speed_unit=mph")
    cur, day = d.get("current") or {}, d.get("daily") or {}
    code = int(cur.get("weather_code", 0))
    text, icon_day, icon_night = WMO.get(code, ("—", "🌡️", "🌡️"))

    def first(k):
        v = day.get(k) or [None]
        return v[0]
    data = {
        "ok": True, "auto": auto, "location": loc["name"], "units": s["units"],
        "temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
        "wind_mph": cur.get("wind_speed_10m"), "code": code, "text": text,
        "icon": icon_day if cur.get("is_day", 1) else icon_night,
        "hi": first("temperature_2m_max"), "lo": first("temperature_2m_min"),
        "rain": first("precipitation_probability_max"),
    }
    _weather.update(t=time.time(), data=data)
    return data


def geocode(q: str) -> list:
    q = (q or "").strip()
    if len(q) < 2:
        return []
    d = _http_json("https://geocoding-api.open-meteo.com/v1/search?count=6&language=en&format=json&name=" + quote(q))
    out = []
    for r in d.get("results") or []:
        label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
        out.append({"name": r.get("name"), "label": label, "lat": r.get("latitude"), "lon": r.get("longitude")})
    return out


# ───────────────────────── world news ─────────────────────────

NEWS_FEEDS = [
    ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("The Guardian", "https://www.theguardian.com/world/rss"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("NPR", "https://feeds.npr.org/1004/rss.xml"),
    ("Sky News", "https://feeds.skynews.com/feeds/rss/world.xml"),
]
NEWS_DAYS = 3          # today and the two previous days
NEWS_MAX = 45
NEWS_CACHE_S = 600
_news = {"t": 0.0, "items": [], "urls": []}
_news_lock = threading.Lock()


def _fetch_feed(source, url):
    import urllib.request
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ZAR HUD)"})
    with urllib.request.urlopen(req, timeout=8) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        date = it.findtext("pubDate") or ""
        try:
            ts = parsedate_to_datetime(date).timestamp()
        except Exception:
            continue
        if title and link.startswith(("http://", "https://")):
            out.append({"title": title, "source": source, "ts": ts, "url": link})
    return out


def _safe_feed(source, url):
    try:
        return _fetch_feed(source, url)
    except Exception as e:
        print(f"[ZAR] HUD: news feed {source} failed: {e}")
        return []


def get_news(force=False):
    with _news_lock:
        if not force and _news["items"] and time.time() - _news["t"] < NEWS_CACHE_S:
            return _news["items"]
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta
    start = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
             - timedelta(days=NEWS_DAYS - 1)).timestamp()
    items = []
    with ThreadPoolExecutor(max_workers=len(NEWS_FEEDS)) as ex:
        for res in ex.map(lambda f: _safe_feed(*f), NEWS_FEEDS):
            items.extend(res)
    seen, clean = set(), []
    for it in sorted(items, key=lambda x: x["ts"], reverse=True):
        key = it["title"].lower()[:70]
        if it["ts"] < start or key in seen:
            continue
        seen.add(key)
        clean.append(it)
    clean = clean[:NEWS_MAX]
    public = [{"id": i, "title": it["title"], "source": it["source"], "ts": it["ts"]} for i, it in enumerate(clean)]
    with _news_lock:
        _news.update(t=time.time(), items=public, urls=[it["url"] for it in clean])
    return public


def open_news(idx: int):
    with _news_lock:
        urls = list(_news["urls"])
    if 0 <= idx < len(urls):
        _open_target(urls[idx])
        return True
    return False


def _day_label(ts: float) -> str:
    from datetime import date, datetime
    d = datetime.fromtimestamp(ts).date()
    diff = (date.today() - d).days
    return "Today" if diff == 0 else "Yesterday" if diff == 1 else d.strftime("%A %d %B").replace(" 0", " ")


def news_pdf() -> Path:
    """World News briefing as a PDF with clickable headlines."""
    from datetime import datetime
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    get_news()
    with _news_lock:
        items, urls = list(_news["items"]), list(_news["urls"])
    font, bold = "Helvetica", "Helvetica-Bold"
    try:
        from actions import documents
        font, bold = documents._pdf_fonts()
    except Exception:
        pass
    ink, muted, accent = colors.HexColor("#1d2430"), colors.HexColor("#5b6572"), colors.HexColor("#0e6e8c")
    st_title = ParagraphStyle("t", fontName=bold, fontSize=22, leading=27, textColor=ink, spaceAfter=4)
    st_sub = ParagraphStyle("s", fontName=font, fontSize=11, leading=15, textColor=muted, spaceAfter=10)
    st_day = ParagraphStyle("d", fontName=bold, fontSize=14, leading=18, textColor=accent, spaceBefore=10, spaceAfter=4)
    st_item = ParagraphStyle("i", fontName=font, fontSize=11, leading=15, textColor=ink, spaceAfter=2)
    st_meta = ParagraphStyle("m", fontName=font, fontSize=9, leading=12, textColor=muted, spaceAfter=7)
    now = datetime.now()
    story = [Paragraph("World News Briefing", st_title),
             Paragraph(escape(f"Today and the two previous days · {len(items)} headlines · "
                              f"{now:%A %d %B %Y, %H:%M}"), st_sub)]
    last = ""
    for it in items:
        day = _day_label(it["ts"])
        if day != last:
            story.append(Paragraph(escape(day), st_day))
            last = day
        url = urls[it["id"]] if it["id"] < len(urls) else ""
        title = escape(it["title"])
        story.append(Paragraph(f'<link href="{escape(url)}" color="#1d2430">{title}</link>' if url else title, st_item))
        story.append(Paragraph(escape(f"{it['source']} · {datetime.fromtimestamp(it['ts']):%H:%M}"), st_meta))
    if not items:
        story.append(Spacer(1, 10))
        story.append(Paragraph("No headlines could be loaded.", st_item))

    def footer(c, d):
        c.saveState()
        c.setFont(font, 8.5)
        c.setFillColor(muted)
        c.drawString(20 * mm, 12 * mm, f"Generated by ZAR · {now:%d/%m/%Y %H:%M}")
        c.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Page {d.page}")
        c.restoreState()

    try:
        from actions import documents
        folder = documents.output_dir() / "News"
    except Exception:
        folder = Path.home() / "Documents" / "ZAR" / "News"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"World News {now:%Y-%m-%d %H%M%S}.pdf"
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=20 * mm, bottomMargin=20 * mm,
                      title="World News Briefing", author="ZAR").build(story, onFirstPage=footer,
                                                                                 onLaterPages=footer)
    return path


def _deliver_news():
    global _news_delivery
    _news_delivery = "Creating the PDF…"
    try:
        path = news_pdf()
    except Exception as e:
        _news_delivery = f"Couldn't create the PDF: {e}"
        return
    try:
        from actions import telegram_link
        if not telegram_link.configured():
            _news_delivery = f"PDF saved ({path.name}). Telegram isn't set up."
            return
        ok = telegram_link.send_document(path, "🌍 World News Briefing — today and the two previous days")
        _news_delivery = "PDF sent to your Telegram ✓" if ok else f"PDF saved ({path.name}), but Telegram failed."
    except Exception as e:
        _news_delivery = f"PDF saved ({path.name}), but Telegram failed: {e}"


NEWS_REPEAT_MIN = 60      # after a briefing, pressing World News again only shows the list for this long
NEWS_STATE_FILE = CONFIG_DIR / "hud_news_state.json"


def _last_briefing() -> float:
    try:
        return float(json.loads(NEWS_STATE_FILE.read_text(encoding="utf-8")).get("last", 0))
    except Exception:
        return 0.0


def _save_briefing_time():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        NEWS_STATE_FILE.write_text(json.dumps({"last": time.time()}), encoding="utf-8")
    except Exception:
        pass


def speak_news() -> dict:
    """First press: ZAR reads a spoken briefing and the PDF goes to Telegram.
    Pressing again within NEWS_REPEAT_MIN minutes only shows the list; after that, fresh briefing + new PDF."""
    import math
    last = _last_briefing()
    wait = NEWS_REPEAT_MIN * 60 - (time.time() - last)
    if last and wait > 0:
        return {"ok": True, "spoken": False, "repeat_in_min": max(1, math.ceil(wait / 60)),
                "last_min_ago": int((time.time() - last) / 60)}
    items = get_news(force=True)          # fresh headlines for a new briefing
    spoken = False
    if items and _text_sender:
        from datetime import datetime
        lines = [f"- [{datetime.fromtimestamp(x['ts']).strftime('%a %d %b %H:%M')}] {x['title']} ({x['source']})"
                 for x in items[:35]]
        prompt = ("[SISTEMA] the user pressed the World News button on the desktop. Give him a spoken world news "
                  "briefing in English covering today and the two previous days, using ONLY these headlines. "
                  "Start with today, group related stories, keep it to about 2 minutes, and at the end tell him "
                  "the full list is on his Telegram as a PDF. Do not call any tools.\n" + "\n".join(lines))
        spoken = _send_to_alberto(prompt)
    if items:
        _save_briefing_time()
        threading.Thread(target=_deliver_news, daemon=True).start()
    return {"ok": bool(items), "spoken": spoken}


# ───────────────────────── HTTP server ─────────────────────────

PROTECTED = ("/news", "/weather", "/geocode", "/settings", "/open", "/news_speak", "/window",
             "/chat", "/upload", "/action", "/chatlog", "/world_events", "/boards", "/zar_event", "/files", "/files_action", "/desktop")
MAX_UPLOAD = 100 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    def _headers_common(self):
        self.send_header("Access-Control-Allow-Origin", "*")   # the wallpaper is a local page
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-ZAR-Token")
        self.send_header("Cache-Control", "no-store")

    def _send(self, body: bytes, code=200, ctype="application/json; charset=utf-8"):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self._headers_common()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass   # the wallpaper reloaded mid-answer: harmless

    def _send_json(self, obj, code=200):
        self._send(json.dumps(obj).encode("utf-8"), code)

    def _authorized(self) -> bool:
        tok = _token()
        return bool(tok) and self.headers.get("X-ZAR-Token", "") == tok

    def _read_body(self, limit=1024 * 1024) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("too large")
        return self.rfile.read(n) if n else b""

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self._headers_common()
            self.end_headers()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_GET(self):
        route = self.path.split("?")[0]
        q = parse_qs(urlparse(self.path).query)
        if route == "/file":
            if q.get("t", [""])[0] != _token():
                self._send_json({"ok": False}, 403)
                return
            p = _file_in_folder(q.get("name", [""])[0])
            if p is None:
                self._send_json({"ok": False}, 404)
                return
            import mimetypes
            self._send(p.read_bytes(), 200, mimetypes.guess_type(p.name)[0] or "application/octet-stream")
            return
        if route.startswith("/ui/"):
            body = _page(route[4:])
            if body is None:
                self._send_json({"ok": False}, 404)
            else:
                self._send(body, 200, "text/html; charset=utf-8")
            return
        if route in PROTECTED:
            if not self._authorized():
                self._send_json({"ok": False, "error": "unauthorized"}, 403)
                return
            try:
                if route == "/news":
                    self._send_json({"ok": True, "items": get_news(force="refresh" in q), "days": NEWS_DAYS})
                elif route == "/weather":
                    self._send_json(get_weather(force="refresh" in q))
                elif route == "/geocode":
                    self._send_json({"ok": True, "results": geocode(q.get("q", [""])[0])})
                elif route == "/settings":
                    s = load_settings()
                    detected = detect_location() if s["location"] is None else None
                    self._send_json({"ok": True, **s, "detected": detected})
                elif route == "/desktop":
                    self._send_json({"ok": True, **load_desktop(), "presets": sorted(WEB_APPS)})
                elif route == "/boards":
                    from actions import boards
                    self._send_json(boards.api({"op": "get"}))
                elif route == "/files":
                    self._send_json(list_files())
                elif route == "/world_events":
                    from actions import world_monitor
                    self._send_json({"ok": True, **world_monitor.get_events(force="refresh" in q)})
                elif route == "/chatlog":
                    with _lock:
                        log = list(_chat_log)
                    self._send_json({"ok": True, "log": log, "state": _data.get("state")})
                else:
                    self._send_json({"ok": False}, 405)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
            return
        if route not in ("/state", "/"):
            self._send_json({"ok": False}, 404)
            return
        with _lock:
            payload = dict(_data)
        payload.update(zar_profile())
        if _standby and payload["state"] == "LISTENING":
            payload["state"] = "STANDBY"
        if _dictating:
            payload["state"] = "DICTATING"
        if _guard:
            payload["state"] = "GUARD"
        if _muted_getter:
            try:
                if _muted_getter():
                    payload["state"] = "MUTED"
            except Exception:
                pass
        payload["can_talk"] = _text_sender is not None
        payload["news_delivery"] = _news_delivery
        self._send_json(payload)

    def do_POST(self):
        route = self.path.split("?")[0]
        q = parse_qs(urlparse(self.path).query)

        if route in PROTECTED:
            if not self._authorized():
                self._send_json({"ok": False, "error": "unauthorized"}, 403)
                return
            try:
                self._protected_post(route, q)
            except Exception as e:
                print(f"[ZAR] HUD: {route} failed: {e}")
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

        # POST /file → "Send to -> ZAR" from Windows Explorer
        if route == "/file":
            raw = self._read_body()
            try:
                path = raw.decode("utf-8")
            except UnicodeDecodeError:
                path = raw.decode("cp1252", errors="replace")
            path = path.strip().strip('"')
            if path.startswith("path="):
                from urllib.parse import unquote_plus
                path = unquote_plus(path[5:])
            ok = bool(path) and os.path.isfile(path) and _file_handler is not None
            if ok:
                try:
                    _file_handler(path)
                except Exception as e:
                    print(f"[ZAR] HUD: could not pass the file: {e}")
                    ok = False
            self._send_json({"ok": ok}, 200 if ok else 400)
            return

        # POST /mute → mutes or unmutes the microphone (wallpaper button)
        if route != "/mute":
            self._send_json({"ok": False}, 404)
            return
        ok = False
        if _mute_toggler:
            try:
                _mute_toggler(); ok = True
            except Exception as e:
                print(f"[ZAR] HUD: could not toggle the mic: {e}")
        self._send_json({"ok": ok}, 200 if ok else 503)

    def _protected_post(self, route, q):
        if route == "/boards":
            from actions import boards
            self._send_json(boards.api(json.loads(self._read_body() or b"{}")))
            return
        if route == "/open":
            if "news" in q:
                ok, info = open_news(int(q["news"][0])), "news"
            elif "url" in q:
                ok, info = open_link(q["url"][0])
            else:
                ok, info = open_app(q.get("app", [""])[0])
            if not ok:
                print(f"[ZAR] HUD: could not open: {info}")
            self._send_json({"ok": ok, "info": info}, 200 if ok else 400)
        elif route == "/news_speak":
            self._send_json(speak_news())
        elif route == "/window":
            extra = "menu=1" if q.get("menu") else ""
            ok = open_window(q.get("page", [""])[0], extra)
            self._send_json({"ok": ok}, 200 if ok else 400)
        elif route == "/settings":
            body = json.loads(self._read_body() or b"{}")
            self._send_json({"ok": True, **save_settings(body)})
        elif route == "/zar_event":
            # events from the wallpaper (Pomodoro finished, ...): ZAR says one short line about it
            body = json.loads(self._read_body() or b"{}")
            ev = str(body.get("event", ""))[:60]
            texts = {
                "pomodoro_done": "[SISTEMA] The student's Pomodoro focus block just finished. Congratulate them in ONE short "
                                 "friendly sentence and tell them to take a short break. Don't call any tool.",
                "break_done": "[SISTEMA] The student's Pomodoro break just finished. In ONE short motivating sentence, "
                              "tell them it's time to get back to studying. Don't call any tool.",
            }
            ok = ev in texts and _send_to_alberto(texts[ev])
            self._send_json({"ok": bool(ok)})
        elif route == "/chat":
            body = json.loads(self._read_body() or b"{}")
            ok, info = chat(body.get("text", ""), body.get("file", ""))
            self._send_json({"ok": ok, "info": info}, 200 if ok else 400)
        elif route == "/desktop":
            self._send_json({"ok": True, **save_desktop(json.loads(self._read_body() or b"{}"))})
        elif route == "/files_action":
            self._send_json(files_action(json.loads(self._read_body() or b"{}")))
        elif route == "/upload":
            data = self._read_body(limit=MAX_UPLOAD)
            path = save_upload(q.get("name", ["file"])[0], data)
            self._send_json({"ok": True, "path": str(path), "name": path.name})
        elif route == "/action":
            ok = run_action(q.get("kind", [""])[0])
            self._send_json({"ok": ok}, 200 if ok else 400)
        else:
            self._send_json({"ok": False}, 405)

    def log_message(self, *args):
        pass   # no spam in the terminal


def start(muted_getter=None, port: int = PORT):
    """Starts the server only once. Safe to call several times."""
    global _started, _muted_getter
    _muted_getter = muted_getter
    if _started:
        return
    _started = True
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    except OSError as e:
        print(f"[ZAR] HUD: could not open port {port}: {e}")
        return
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=_sample_system, daemon=True).start()
    threading.Thread(target=_sync_wallpaper, daemon=True).start()
    print(f"[ZAR] Desktop HUD available at http://127.0.0.1:{port}/state")


def attach_ui(ui):
    """Hooks ZAR's UI: every state change or message also reaches the wallpaper."""
    orig_state = ui.set_state
    orig_log = ui.write_log

    def set_state_hook(state):
        set_state(state)
        return orig_state(state)

    def write_log_hook(text):
        add_log(text)
        return orig_log(text)

    ui.set_state = set_state_hook
    ui.write_log = write_log_hook
    start(muted_getter=lambda: ui.muted)
