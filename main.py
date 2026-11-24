import os
import sys as _sys
from pathlib import Path as _Path
try:
    from actions.owner_profile import OWNER
except Exception:
    OWNER = "the user"


# Trabajar siempre desde la carpeta de ZAR (aunque se lance desde el Programador de tareas)
_APP_DIR = _Path(_sys.executable).parent if getattr(_sys, "frozen", False) else _Path(__file__).resolve().parent
os.chdir(_APP_DIR)

# Sin consola (pythonw.exe): todo lo que antes salía en la terminal va a logs/alberto.log
if _sys.stdout is None or _sys.stderr is None:
    (_APP_DIR / "logs").mkdir(exist_ok=True)
    _logp = _APP_DIR / "logs" / "alberto.log"
    try:
        if _logp.exists() and _logp.stat().st_size > 5 * 1024 * 1024:     # que no crezca sin fin
            _old = _logp.with_name("alberto.old.log")
            if _old.exists():
                _old.unlink()
            _logp.rename(_old)
    except Exception:
        pass
    _logf = open(_logp, "a", encoding="utf-8", buffering=1)
    _sys.stdout = _sys.stderr = _logf

# Si ZAR se cae, que quede escrito por qué (incluidos fallos internos de Qt, audio, etc.)
import faulthandler as _fh
import datetime as _dt
try:
    _fh.enable(file=_sys.stderr, all_threads=True)
    # If start-up gets stuck, write where it is stuck to the log (cancelled once the window is up)
    _fh.dump_traceback_later(100, repeat=False, file=_sys.stderr)
except Exception:
    pass
print(f"\n===== ZAR arrancando {_dt.datetime.now():%Y-%m-%d %H:%M:%S} "
      f"(pid {os.getpid()}{', reiniciado tras un fallo' if os.environ.get('ALBERTO_RESTART_REASON') else ''}) =====")


def _log_unhandled(exc_type, exc, tb, where="principal"):
    import traceback as _tb
    print(f"[ZAR] ERROR NO CONTROLADO ({where}) {_dt.datetime.now():%H:%M:%S}:")
    _tb.print_exception(exc_type, exc, tb)


# PyQt6 cierra la aplicación entera ante cualquier error sin capturar en la interfaz.
# Con esto el error se apunta en el log y ZAR sigue funcionando.
_sys.excepthook = _log_unhandled
try:
    import threading as _th
    _th.excepthook = lambda a: _log_unhandled(a.exc_type, a.exc_value, a.exc_traceback,
                                              f"hilo {getattr(a.thread, 'name', '?')}")
except Exception:
    pass

# Solo un ZAR a la vez (si ya hay uno, este se cierra sin hacer nada)
if os.name == "nt":
    try:
        import ctypes as _ct
        _MUTEX = _ct.windll.kernel32.CreateMutexW(None, False, "Local\\ALBERTO_Mark_XXXIX")
        if _ct.windll.kernel32.GetLastError() == 183:          # ERROR_ALREADY_EXISTS
            print("[ZAR] Ya hay un ZAR en marcha; cierro esta copia.")
            os._exit(0)
    except Exception:
        pass

# Sin terminal, cada programa de consola que lance ZAR (PowerShell, nvidia-smi, cmd...)
# abriría su propia ventana negra. Esto hace que se ejecuten siempre ocultos.
if os.name == "nt":
    import subprocess as _sp
    _CREATE_NO_WINDOW, _NEW_CONSOLE, _DETACHED = 0x08000000, 0x00000010, 0x00000008
    _popen_init = _sp.Popen.__init__

    def _popen_hidden(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        if not flags & (_NEW_CONSOLE | _DETACHED):      # respeta a quien pide una consola a propósito
            kwargs["creationflags"] = flags | _CREATE_NO_WINDOW
        return _popen_init(self, *args, **kwargs)

    _sp.Popen.__init__ = _popen_hidden

import asyncio
import importlib
import re
import threading
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types


def _relax_gemini_keepalive():
    """Gemini Live pings every 20 s and drops the call if the answer takes >20 s (any short stall
    inside ZAR). Give it more room, and more time to reconnect."""
    try:
        from google.genai import live as _live
    except Exception:
        return
    for _name in ("connect", "ws_connect"):
        _orig = getattr(_live, _name, None)
        if _orig is None or getattr(_orig, "_alberto", False):
            continue

        def _patched(*a, _o=_orig, **k):
            k.setdefault("ping_interval", 30)
            k.setdefault("ping_timeout", 60)
            k.setdefault("open_timeout", 30)
            try:
                return _o(*a, **k)
            except TypeError:                     # older websockets without these options
                for x in ("ping_interval", "ping_timeout", "open_timeout"):
                    k.pop(x, None)
                return _o(*a, **k)

        _patched._alberto = True
        setattr(_live, _name, _patched)


_relax_gemini_keepalive()
from ui import JarvisUI
import ui as _ui_module


def _throttle_ui_metrics():
    """La ventana medía la temperatura con PowerShell y la GPU con nvidia-smi cada 1,5 s.
    Ahora: si no funcionan, deja de intentarlo; si funcionan, mide cada 30 s."""
    cls = getattr(_ui_module, "_SysMetrics", None)
    if cls is None:
        return
    for name in ("_get_temp", "_get_gpu"):
        orig = getattr(cls, name, None)
        if orig is None:
            continue
        cache = {"t": 0.0, "v": -1.0, "dead": False}

        def wrapped(self, _orig=orig, _c=cache):
            if _c["dead"]:
                return -1.0
            now = time.time()
            if now - _c["t"] < 30:
                return _c["v"]
            _c["t"] = now
            _c["v"] = _orig(self)
            if _c["v"] < 0:
                _c["dead"] = True                 # no hay sensor / no hay NVIDIA: no repetir
            return _c["v"]

        setattr(cls, name, wrapped)


_throttle_ui_metrics()
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)


# ═══════════════════════════ AJUSTES DE FLUIDEZ ═══════════════════════════
# Cambia estos valores si quieres afinar el comportamiento.

# Voz de ZAR. Para un JARVIS grave y sereno, prueba estas de oído y quédate con la que más te guste:
#   "Charon"      grave e informativa (la de ahora, buena base JARVIS)
#   "Alnilam"     firme y seria
#   "Gacrux"      madura, de más edad
#   "Iapetus"     clara y neutra
#   "Rasalgethi"  serena y "sabia"
#   "Orus" / "Enceladus"  otras masculinas (Enceladus más susurrante)
# La cadencia de JARVIS (grave, pausada, elegante) la marca el bloque VOICE DELIVERY de prompt.txt.
VOICE_NAME = "Puck"   # friendly, upbeat voice

# Interrumpir a ZAR hablándole encima (como Tony con JARVIS)
BARGE_IN            = True     # False = el micro se apaga mientras habla (comportamiento antiguo)
BARGE_IN_MIN_RMS    = 1400     # Volumen mínimo de tu voz para cortarle. Súbelo si se corta solo.
BARGE_IN_ECHO_RATIO = 2.5      # Cuántas veces más fuerte que su propio eco tiene que ser tu voz
BARGE_IN_FRAMES     = 3        # Bloques seguidos (~64 ms cada uno) por encima del umbral para cortarle
BARGE_IN_HOLD_S     = 1.5      # Segundos que el micro queda abierto tras detectar que le hablas

# Detección de fin de frase: cuanto menor, antes responde (demasiado bajo = te corta a mitad)
SILENCE_MS          = 550
PREFIX_PADDING_MS   = 200

# El modelo de voz (resemblyzer + torch) no se usa en ningún sitio y ralentiza el arranque
PRELOAD_VOICE_MODEL = False

# Saludo al arrancar (solo una vez, no en cada reconexión)
GREET_ON_START      = True

# Ventana: ZAR vive en el fondo del escritorio y en el icono junto al reloj
START_HIDDEN        = True     # False = abrir la ventana al arrancar, como antes
TRAY_ICON           = True     # icono en la bandeja del sistema (mostrar / silenciar / salir)
MUTE_HOTKEY         = "ctrl+alt+m"   # atajo global para silenciar / activar el micro ("" = desactivado)
PANIC_HOTKEY        = "ctrl+alt+p"   # atajo de pánico: silencia, arma vigilancia y bloquea el PC ("" = off)

# Palabra de activación: solo escucha de verdad cuando oye «ZAR»
WAKE_WORD           = True
AWAKE_WINDOW_S      = 15       # segundos que sigue atento después de hablar contigo
WAKE_PREROLL_S      = 1.6      # audio de antes de «ZAR» que también se envía

# Reconocimiento de quién habla
VOICE_ID            = True
# Solo estas personas pueden usar lo delicado (trading, mensajes, correos, borrar archivos).
# Vacío = cualquiera. Ejemplo: ["Ana"]
RESTRICT_SENSITIVE_TO: list[str] = []   # ZAR: no voice lock by default
SENSITIVE_TOOLS = {"trading_bot", "send_message", "whatsapp_scheduler", "email_controller", "file_controller",
                   "security", "cyber_shield"}
SENSITIVE_ACTIONS = {"email_controller": {"send"}, "file_controller": {"delete", "move", "rename", "write"},
                     "security": {"guard_off", "presence_off"},
                     "cyber_shield": {"unpanic", "vault_set", "vault_list"}}


def _lazy(module: str, attr: str):
    """Importa el módulo la primera vez que se usa, no al arrancar."""
    holder = {}
    def call(*args, **kwargs):
        if "fn" not in holder:
            holder["fn"] = getattr(importlib.import_module(module), attr)
        return holder["fn"](*args, **kwargs)
    return call


# Módulos pesados o lentos de importar: se cargan la primera vez que se usan
file_processor     = _lazy("actions.file_processor", "file_processor")
flight_finder      = _lazy("actions.flight_finder", "flight_finder")
screen_process     = _lazy("actions.screen_processor", "screen_process")
youtube_video      = _lazy("actions.youtube_video", "youtube_video")
code_helper        = _lazy("actions.code_helper", "code_helper")
dev_agent          = _lazy("actions.dev_agent", "dev_agent")
game_updater       = _lazy("actions.game_updater", "game_updater")
email_controller   = _lazy("actions.email_controller", "email_controller")
whatsapp_scheduler = _lazy("actions.whatsapp_scheduler", "whatsapp_scheduler")
spotify_controller = _lazy("actions.spotify_controller", "spotify_controller")
music_player       = _lazy("actions.youtube_music", "music_player")
create_document    = _lazy("actions.documents", "create_document_tool")
summarize_document = _lazy("actions.documents", "summarize_document_tool")
meeting_tool       = _lazy("actions.meeting", "meeting_tool")
dictation          = _lazy("actions.voice_dictation", "dictation_tool")
briefing_tool      = _lazy("actions.watchdog", "briefing_tool")
price_alert_tool   = _lazy("actions.watchdog", "price_alert_tool")
security_tool      = _lazy("actions.security", "security_tool")
research_tool      = _lazy("actions.research", "research_tool")
data_tool          = _lazy("actions.research", "data_tool")
image_tool         = _lazy("actions.image_studio", "image_tool")
it_tool            = _lazy("actions.it_helper", "it_tool")
projects_tool      = _lazy("actions.projects", "projects_tool")
security_audit_tool= _lazy("actions.security_audit", "security_audit_tool")
cyber_watch_tool   = _lazy("actions.cyber_watch", "cyber_watch_tool")
cyber_tool         = _lazy("actions.cyber_tools", "cyber_tool")
forensics_tool     = _lazy("actions.forensics", "forensics_tool")
malware_scan_tool  = _lazy("actions.malware_scan", "malware_scan_tool")
net_monitor_tool   = _lazy("actions.net_monitor", "net_monitor_tool")
mitre_tool         = _lazy("actions.mitre", "mitre_tool")
security_report_tool = _lazy("actions.security_report", "security_report_tool")
vault_tool         = _lazy("actions.vault", "vault_tool")
audit_log_tool     = _lazy("actions.audit_log", "audit_log_tool")
gmail_tool         = _lazy("actions.gmail_box", "gmail_tool")
shopify_tool       = lambda *a, **k: "Not available in ZAR."
vision_tool        = _lazy("actions.live_vision", "vision_tool")
autopilot_tool     = _lazy("actions.autopilot", "autopilot_tool")
agenda_tool        = _lazy("actions.agenda", "agenda_tool")
presentation_tool  = _lazy("actions.presentations", "presentation_tool")
gesture_controller = _lazy("actions.gesture_controller", "gesture_controller")
emotion_detector   = _lazy("actions.emotion_detector", "emotion_detector")

from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from agents.agent_manager import detect_agent, get_agent
import agents.investigador as agent_investigador
import agents.programador as agent_programador
import agents.organizador as agent_organizador
import agents.entretenimiento as agent_entretenimiento
import agents.seguridad as agent_seguridad
from server import run_in_background, emit_log
from actions.conversation_history import save_message, get_today_history, get_history_summary
from actions.alberto_agents import TOOL_DECLARATIONS as AGENT_TOOLS, handle_tool_async as agents_handle_tool
# ZAR: tools of the original ZAR that make no sense for students
DISABLED_TOOLS = {"trading_bot", "shopify", "game_updater", "replica_hq"}
TRADING_TOOL = {"name": "trading_bot", "description": "disabled", "parameters": {"type": "OBJECT", "properties": {}}}
trading_action = lambda *a, **k: "Not available in ZAR."
try:
    from actions.libreoffice import TOOL_DECLARATION as OFFICE_TOOL, office_tool
    _OFFICE_TOOLS = [OFFICE_TOOL]
except Exception as _e:
    print(f"[ZAR] LibreOffice tools disabled: {_e}")
    office_tool, _OFFICE_TOOLS = None, []
try:
    from actions import world_monitor
    _OFFICE_TOOLS.append(world_monitor.TOOL_DECLARATION)
except Exception as _e:
    print(f"[ZAR] World Monitor disabled: {_e}")
    world_monitor = None
try:
    from actions import boards as boards_mod
    _OFFICE_TOOLS.append(boards_mod.TOOL_DECLARATION)
except Exception as _e:
    print(f"[ZAR] Boards disabled: {_e}")
    boards_mod = None


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are ZAR, an AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). If the user does NOT name a browser, DEFAULT TO EDGE: pass browser='edge'. Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Default is edge; omit only to keep using the already-open browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Puts the assistant to rest or closes it. mode=standby (DEFAULT): stop listening until the user says "
            "'ZAR' again — use it when the user says goodbye, 'descansa', 'apágate', 'eso es todo', "
            "'déjame tranquilo', 'silencio'. mode=quit: close the program completely — ONLY when the user "
            "explicitly asks to CLOSE THE PROGRAM ('cierra ZAR del todo', 'sal del programa') AND then "
            "confirms when you ask. Never use quit for a simple goodbye, thanks or 'buenas noches'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode":    {"type": "STRING", "description": "standby (default) | quit"},
                "confirm": {"type": "BOOLEAN", "description": "For quit: true only after the user confirmed"},
            },
        }
    },
    {
        "name": "file_processor",
        "description": (
            "Processes any file that the user has uploaded or dropped onto the interface. "
            "Use this when the user refers to an uploaded file and wants an action on it. "
            "Supports: images (describe/ocr/resize/compress/convert), "
            "PDFs (summarize/extract_text/to_word), "
            "Word docs & text files (summarize/fix/reformat/translate), "
            "CSV/Excel (analyze/stats/filter/sort/convert), "
            "JSON/XML (validate/format/analyze), "
            "code files (explain/review/fix/optimize/run/document/test), "
            "audio (transcribe/trim/convert/info), "
            "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
            "archives (list/extract), "
            "presentations (summarize/extract_text). "
            "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
            "If the user's command is ambiguous, pick the most logical action for that file type."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path":   {"type": "STRING", "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."},
                "action":      {"type": "STRING", "description": "image: describe|ocr|resize|compress|convert|info / pdf: summarize|extract_text|to_word|info / docx/txt: summarize|fix|reformat|translate_hint|word_count|to_bullet / csv/excel: analyze|stats|filter|sort|convert|info / json: validate|format|analyze|to_csv / code: explain|review|fix|optimize|run|document|test / audio: transcribe|trim|convert|info / video: trim|extract_audio|extract_frame|compress|transcribe|info|convert / archive: list|extract / pptx: summarize|extract_text|analyze"},
                "instruction": {"type": "STRING", "description": "Free-form instruction if action doesn't cover it."},
                "format":      {"type": "STRING", "description": "Target format for conversion."},
                "width":       {"type": "INTEGER", "description": "Target width for image resize"},
                "height":      {"type": "INTEGER", "description": "Target height for image resize"},
                "scale":       {"type": "NUMBER",  "description": "Scale factor for image resize"},
                "quality":     {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
                "start":       {"type": "STRING",  "description": "Start time for trim"},
                "end":         {"type": "STRING",  "description": "End time for trim"},
                "timestamp":   {"type": "STRING",  "description": "Timestamp for video frame extraction"},
                "column":      {"type": "STRING",  "description": "Column name for CSV filter/sort"},
                "value":       {"type": "STRING",  "description": "Filter value for CSV filter"},
                "condition":   {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
                "ascending":   {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
                "save":        {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
                "destination": {"type": "STRING",  "description": "Output folder for archive extract"},
            },
            "required": []
        }
    },
    {
        "name": "emotion_detector",
        "description": (
            "Activates or deactivates real-time voice emotion detection. "
            "Detects if the user is happy, sad, angry, calm or neutral and responds accordingly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | stop | status"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "gesture_controller",
        "description": (
            "Iron Man style hand gesture control via webcam, with two modes. "
            "MEDIA mode: open palm=play/pause, thumb up/down=volume, victory=next track, "
            "index up=previous track, fist=mute, palm swipe=next/previous track. "
            "MOUSE mode: index finger moves the cursor, pinch=click, two pinches=double click, "
            "long pinch=drag, two fingers up/down=scroll, thumb up=switch window, swipe=back/forward. "
            "Holding the 'I love you' sign (🤟) for 1 second switches modes. "
            "Use action=start to turn it on (optionally with mode), stop to turn it off, "
            "mode to switch mode, preview to show/hide the camera window with the hand overlay, "
            "status or guide to explain the gestures."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "start | stop | mode | preview | status | guide"},
                "mode":    {"type": "STRING", "description": "media | mouse"},
                "preview": {"type": "BOOLEAN", "description": "Show (true) or hide (false) the camera window"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "music_player",
        "description": (
            "Plays music from YouTube in the background (no browser window). Use for ANY music request: "
            "play a song, artist, genre or playlist URL, pause, resume, next/previous track, volume, "
            "what's playing, add a song to the queue, shuffle, repeat. Playing a song automatically "
            "queues similar songs (radio). Never use Spotify or the browser for music."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "play | pause | resume | toggle | next | previous | stop | volume | current | add | shuffle | repeat"},
                "query":     {"type": "STRING",  "description": "Song, artist, genre or YouTube URL (for play / add)"},
                "volume":    {"type": "INTEGER", "description": "Music volume 0-100 (for volume)"},
                "direction": {"type": "STRING",  "description": "up | down (for volume without a number)"},
                "radio":     {"type": "BOOLEAN", "description": "Queue similar songs after this one (default true)"},
                "state":     {"type": "BOOLEAN", "description": "true/false for repeat"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "whatsapp_scheduler",
        "description": (
            "Schedules and sends WhatsApp messages at a specific time. "
            "Phone number must include country code e.g. +34612345678"
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "phone":   {"type": "STRING",  "description": "Phone number with country code"},
                "message": {"type": "STRING",  "description": "Message to send"},
                "hour":    {"type": "INTEGER", "description": "Hour to send (24h format)"},
                "minute":  {"type": "INTEGER", "description": "Minute to send"},
            },
            "required": ["phone", "message"]
        }
    },
    {
        "name": "email_controller",
        "description": (
            "Sends and reads emails. Supports Gmail, Outlook, Hotmail, Yahoo automatically."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "send | read"},
                "to":      {"type": "STRING", "description": "Recipient email address (for send)"},
                "subject": {"type": "STRING", "description": "Email subject (for send)"},
                "body":    {"type": "STRING", "description": "Email body text (for send)"},
                "count":   {"type": "INTEGER", "description": "Number of emails to read (default: 5)"},
                "folder":  {"type": "STRING", "description": "Folder to read: INBOX, SENT, etc."},
            },
            "required": ["action"]
        }
    },
    {
        "name": "specialist_agent",
        "description": (
            "Routes tasks to specialized agents: "
            "ATLAS (research/investigation), NEXUS (programming/code), "
            "ARIA (organization/reminders/tasks), PULSE (entertainment/music/youtube), "
            "GUARDIAN (security/system monitoring)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "agent": {"type": "STRING", "description": "investigador | programador | organizador | entretenimiento | seguridad"},
                "query": {"type": "STRING", "description": "The task or question for the specialist agent"},
            },
            "required": ["agent", "query"]
        }
    },
    {
        "name": "create_document",
        "description": (
            "Writes a complete document (report, letter, essay, plan, contract draft, notes, CV, etc.) "
            "and saves it as Word (.docx) or PDF in Documents\\ZAR, then opens it. "
            "Use whenever the user asks you to write, draft, create or make a document, letter or report."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "request": {"type": "STRING", "description": "Full description of what the document must contain, with every detail the user gave (in Spanish)"},
                "format":  {"type": "STRING", "description": "docx (default) | pdf"},
                "kind":    {"type": "STRING", "description": "Optional type: carta, informe, ensayo, plan, contrato, apuntes, CV..."},
                "length":  {"type": "STRING", "description": "corto | medio (default) | largo"},
                "title":   {"type": "STRING", "description": "Optional exact title"},
            },
            "required": ["request"]
        }
    },
    {
        "name": "summarize_document",
        "description": (
            "Summarizes a document file on this computer (PDF, Word, TXT, Markdown, PowerPoint). "
            "The file can be given as a full path, by words of its name (searched in Downloads, Desktop "
            "and Documents), or as 'el último' for the most recent one. Optionally saves the summary "
            "as a PDF/Word document. Speak the result in 2-4 sentences."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file":   {"type": "STRING",  "description": "Path, words of the file name, or 'último' for the latest document"},
                "detail": {"type": "STRING",  "description": "breve | medio (default) | detallado"},
                "focus":  {"type": "STRING",  "description": "Optional aspect to focus on"},
                "save":   {"type": "BOOLEAN", "description": "Also save the summary as a document and open it"},
                "format": {"type": "STRING",  "description": "pdf (default) | docx, when save is true"},
            },
            "required": []
        }
    },
    {
        "name": "dictation",
        "description": (
            "Voice dictation to PDF. action=start begins dictation: from then on everything the user says "
            "is transcribed into a PDF (not sent to you) until they say 'fin del dictado'. "
            "Use when the user asks to dictate, take dictation, or write down what they say. "
            "action=stop ends it, action=status reports progress."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | stop | status"},
                "title":  {"type": "STRING", "description": "Optional document title"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "use_document",
        "description": (
            "Loads a file from this computer as the CURRENT document so you can work on it "
            "(answer questions, fix, translate, summarize, analyze...). Find it by full path, by words of its "
            "name (searched in Downloads, Desktop and Documents) or 'último' for the most recent. "
            "Use when the user says 'coge / abre / trabaja con / usa el documento X'. "
            "After loading, use file_processor (it automatically uses the current document) or summarize_document."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file": {"type": "STRING", "description": "Path, words of the file name, or 'último'"},
            },
            "required": ["file"]
        }
    },
    {
        "name": "protocol",
        "description": (
            "Protocols = the user's routines, like JARVIS's 'House Party Protocol'. action=run executes one "
            "(e.g. 'protocolo estudio', 'buenas noches', 'modo gaming', 'protocolo trading'). action=create makes "
            "a new one from a description; list, delete, cancel_shutdown (cancels a scheduled shutdown)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "run | create | list | delete | cancel_shutdown"},
                "name":        {"type": "STRING", "description": "Protocol name"},
                "description": {"type": "STRING", "description": "For create: what the routine must do, step by step, in the user's words"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "briefing",
        "description": "Daily briefing: date, time, weather, PC status, price alerts and trading bot. Use for 'dame el parte', '¿cómo está todo?', 'buenos días'.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "price_alert",
        "description": (
            "Crypto price alerts and prices (Binance). add: alert when a coin goes above/below a price or moves a "
            "percentage; list; remove; price: current price."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING", "description": "add | list | remove | price"},
                "symbol":    {"type": "STRING", "description": "Coin: BTC, ETH, SOL... or pair like BTCUSDT"},
                "condition": {"type": "STRING", "description": "above | below | move"},
                "value":     {"type": "NUMBER", "description": "Price for above/below"},
                "percent":   {"type": "NUMBER", "description": "Percentage for move"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "security",
        "description": (
            "PC security. presence_on/off: lock the PC when the webcam sees the user has left. guard_on/off: "
            "surveillance mode (if someone touches the PC, take photos, send them to Telegram, lock and PHONE the user "
            "with all the details). test_call: call the user now to test the alert call. "
            "lock: lock now. network: scan the wifi for new devices. trust_network: mark current devices as known. status."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "presence_on | presence_off | guard_on | guard_off | test_call | lock | network | trust_network | status"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "research",
        "description": "Researches a topic on the internet and writes a report with sources (PDF or Word), then opens it. Use for 'investiga…', 'hazme un informe sobre…'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic":  {"type": "STRING", "description": "What to research, with every detail the user gave"},
                "depth":  {"type": "STRING", "description": "breve | medio (default) | profundo"},
                "focus":  {"type": "STRING", "description": "Optional angle"},
                "format": {"type": "STRING", "description": "pdf (default) | docx"},
            },
            "required": ["topic"]
        }
    },
    {
        "name": "image",
        "description": (
            "Creates or edits images with AI and saves them in Pictures\\ZAR. action=create: new image from prompt. "
            "action=edit: change an existing image (source = 'ultima' for the last one created, a file name, "
            "'pantalla' for a screenshot, 'webcam' for a photo of the user). action=send: send the last image to "
            "Telegram. action=open: open the last image. Write the prompt in detail (subject, style, lighting, "
            "composition), in the user's words enriched. Use for 'hazme una imagen', 'dibuja', 'crea un logo', "
            "'edita la foto', 'cámbiale el fondo'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":       {"type": "STRING", "description": "create (default) | edit | send | open"},
                "prompt":       {"type": "STRING", "description": "Detailed description of the image or of the change"},
                "source":       {"type": "STRING", "description": "For edit: ultima | file name | pantalla | webcam"},
                "aspect_ratio": {"type": "STRING", "description": "1:1 | 16:9 | 9:16 | 4:3 | 3:4 | 4:5 | 21:9 (or words: horizontal, vertical, fondo de pantalla)"},
                "telegram":     {"type": "BOOLEAN", "description": "Also send it to the user's Telegram"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "it_support",
        "description": (
            "IT technician for the user's OWN computer (read-only diagnostics). "
            "action=status: quick health check. errors: read Windows event-log errors of the last hours and explain "
            "them. explain: explain an error/message the user read (text=...). report: full diagnostic PDF (hardware, "
            "services, startup, updates, recent errors) and open it. processes: what uses CPU/RAM. network: internet "
            "check. disk: disk SMART health. Use for 'por qué va lento', 'revisa los errores', 'diagnóstico', "
            "'qué significa este error'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "status | errors | explain | report | processes | network | disk"},
                "text":   {"type": "STRING", "description": "For explain: the error or message to explain"},
                "hours":  {"type": "INTEGER", "description": "For errors: how many hours back (default 24)"},
                "format": {"type": "STRING", "description": "For report: pdf (default) | docx"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "presentation",
        "description": (
            "Creates a PowerPoint presentation (.pptx, editable, opens in PowerPoint and imports into Canva) from a "
            "topic, with AI-written slides and speaker notes. args: topic, slides? (default 8), audience?, telegram?. "
            "Use for 'hazme una presentación sobre X', 'una presentación de 10 diapositivas de Y para clientes', "
            "'prepárame unas slides de Z'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic":    {"type": "STRING", "description": "What the presentation is about, with detail"},
                "slides":   {"type": "INTEGER", "description": "Number of slides (default 8)"},
                "audience": {"type": "STRING", "description": "Who it is for (clients, class, investors...)"},
                "telegram": {"type": "BOOLEAN", "description": "Also send it to the user's Telegram"},
            },
            "required": ["topic"]
        }
    },
    {
        "name": "agenda",
        "description": (
            "Calendar and reminders (local). action=add: add an event from natural language (text='dentista mañana "
            "a las 10 en la clínica'). today: what's on today. week: this week. next: next event. list: upcoming. "
            "delete: remove by words (text='dentista'). Use for 'recuérdame…', 'tengo reunión el viernes', "
            "'qué tengo hoy', 'cuál es mi próxima cita', 'borra la cita de…'. ZAR reminds before each event."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "add | today | week | next | list | delete"},
                "text":   {"type": "STRING", "description": "For add: the event with date/time/place in the user's words. For delete: words of the title."},
            },
            "required": ["action"]
        }
    },
    {
        "name": "autopilot",
        "description": (
            "Autonomous agent. Give it a GOAL and it breaks it into steps, does the SAFE ones itself (research, "
            "analyze data, write documents, reports, check the PC/store), and leaves anything irreversible as "
            "'needs your permission', then delivers a report. Use for 'organiza X y hazme un informe', "
            "'investiga y compara X y gu\u00e1rdalo', 'prep\u00e1rame todo sobre Y'. NOT for a single simple task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal": {"type": "STRING", "description": "The goal in the user's words, with all detail"},
            },
            "required": ["goal"]
        }
    },
    {
        "name": "vision",
        "description": (
            "ZAR's eyes — looks through the webcam or at the screen with AI vision. action=look: take a webcam "
            "photo now and describe/answer about what the user is pointing at (question=...). read: transcribe the "
            "text the webcam sees (OCR). screen: analyze what is on the user's screen (question=...). "
            "Use for 'qué estoy viendo', 'qué pieza es esta', 'lee lo que pone aquí', 'mira la pantalla', "
            "'qué significa este error en pantalla'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":   {"type": "STRING", "description": "look (webcam) | read (OCR) | screen"},
                "question": {"type": "STRING", "description": "What the user is asking about the image"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "shopify",
        "description": (
            "Reads the user's Shopify store (read-only). action=summary: spoken sales status today vs yesterday. "
            "report: full daily report (sales, orders, AOV, new customers, top products, low stock, unfulfilled "
            "orders) with news and what to improve — makes a PDF and sends it to Telegram. low_stock: products "
            "running low. Use for 'cómo va la tienda', 'parte de la tienda', 'hazme el informe de la tienda', "
            "'qué productos se están agotando'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":   {"type": "STRING", "description": "summary | report | low_stock"},
                "telegram": {"type": "BOOLEAN", "description": "For report: also send to Telegram (default true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "gmail",
        "description": (
            "Reads the user's STORE Gmail inbox (read-only). action=unread: new unread emails summarized. "
            "count: how many unread. digest: AI summary grouping orders/customers/suppliers and what needs action. "
            "search (query=...): find emails. telegram: send the email summary to the user's Telegram. "
            "Use for 'tengo correos nuevos', 'resume mi bandeja', 'me ha escrito el proveedor', 'mándame los correos'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "unread | count | digest | search | telegram"},
                "query":  {"type": "STRING", "description": "For search: words to look for"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "send_file",
        "description": (
            "Sends a file (PDF, Word, Excel, image…) to the user's Telegram. Use when the user asks you to send, "
            "pass or forward them a document, report or file — especially from a Telegram message like 'mándame el "
            "informe de ventas', 'pásame el PDF del diagnóstico'. Finds the file by name among the user's documents."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Words from the file name, or 'el último' for the most recent"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "meeting",
        "description": ("Meeting / recording mode. action=start begins recording the microphone; "
                        "action=stop ends it, transcribes and returns a summary with key points, decisions "
                        "and action items (saved to Desktop and sent to Telegram). Use when the user says "
                        "'start the meeting', 'empieza la reunion', 'graba la reunion', 'termina la reunion'."),
        "parameters": {"type": "OBJECT", "properties": {
            "action": {"type": "STRING", "description": "start | stop | status"}},
            "required": ["action"]}
    },
    {
        "name": "desktop_note",
        "description": ("Opens a floating translucent sticky-note widget on the desktop where the user can "
                        "type and it saves itself. Use when the user asks for a notepad, sticky note, a place "
                        "to write, 'bloc de notas', 'dame un bloc de notas', 'abre notas', 'quiero escribir algo'. "
                        "action=open."),
        "parameters": {"type": "OBJECT", "properties": {
            "action": {"type": "STRING", "description": "open"}},
            "required": []}
    },
    {
        "name": "widget",
        "description": ("Opens a floating translucent desktop widget (HUD style). kind options: "
                        "todo (to-do list), clock (clock + timer), calc (calculator), postit (sticky note), "
                        "camera (webcam preview), sysmon (CPU/RAM/network monitor), music (now playing). "
                        "Use when the user asks for any of these, e.g. 'dame una lista de tareas', 'abre un "
                        "temporizador', 'abre la calculadora', 'pon un post-it', 'ensename la camara', "
                        "'monitor del sistema', 'que esta sonando'."),
        "parameters": {"type": "OBJECT", "properties": {
            "kind": {"type": "STRING", "description": "todo | clock | calc | postit | camera | sysmon | music"}},
            "required": ["kind"]}
    },
    {
        "name": "cyber_shield",
        "description": (
            "Live defensive security of the user's OWN PC. action=status: current sentinel status (antivirus, "
            "firewall, failed logins, USB). scan_file: check a file for malware via hash+VirusTotal without running "
            "it (value=name/path, or current file). check_url: is a link dangerous without opening it (value=url). "
            "check_password: has a password leaked (value=password, k-anonymous). check_email: has an email been in "
            "breaches (value=email). panic: PANIC MODE — mute mic, arm surveillance, lock PC (confirm first). "
            "unpanic: undo it. log_today / log_recent: what ZAR has done (action log). "
            "vault_set/vault_list: encrypted key store (name, value)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "status | scan_file | check_url | check_password | check_email | panic | unpanic | log_today | log_recent | vault_set | vault_list | triage | scan_folder | connections | mitre | report"},
                "value":  {"type": "STRING", "description": "The file/url/password/email, or for vault_set the secret value"},
                "name":   {"type": "STRING", "description": "For vault_set: the key name"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "security_audit",
        "description": (
            "Personal security auditor for the user's OWN Windows PC (read-only, changes nothing). "
            "action=summary: quick spoken security score. panel: security dashboard (surveillance, known wifi "
            "devices, failed logons, score). audit: full hardening audit (firewall, antivirus, updates, BitLocker, "
            "UAC, auto-login, RDP, SMBv1, guest account, local admins, screen lock, password policy) as a PDF with "
            "a 0-100 score and fixes. Use for 'auditoría de seguridad', 'cómo está mi seguridad', 'panel de seguridad'. "
            "This is NOT a network scanner or attack tool."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "summary | panel | audit"},
                "format": {"type": "STRING", "description": "For audit: pdf (default) | docx"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "projects",
        "description": (
            "Project control center: tracks the user's code/web/app projects (git status, pending TODOs, website "
            "uptime, last build/test result). action=add (path=folder, optional url): start tracking a project. "
            "overview: status of all. detail (name=...): deep status of one. web (name or url): is the site online. "
            "report: PDF status report. remove / set_url. Use for 'cómo van mis proyectos', 'qué falta en X', "
            "'está online mi web', 'añade el proyecto de C:\\...'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "overview | detail | add | web | report | remove | set_url"},
                "name":   {"type": "STRING", "description": "Project name (for detail/web/remove/set_url)"},
                "path":   {"type": "STRING", "description": "Full folder path (for add)"},
                "url":    {"type": "STRING", "description": "Website URL to monitor (for add/set_url)"},
                "format": {"type": "STRING", "description": "For report: pdf (default) | docx"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "analyze_data",
        "description": "Analyzes a CSV or Excel file (by name, path or the current document): statistics, trends and charts in a PDF report. Use for 'analiza mis ventas', 'haz gráficas de este Excel'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file":     {"type": "STRING", "description": "File name words or path (empty = current document)"},
                "question": {"type": "STRING", "description": "What the user wants to know"},
                "format":   {"type": "STRING", "description": "pdf (default) | docx"},
            },
            "required": []
        }
    },
    {
        "name": "voice_id",
        "description": (
            "Voice recognition of who is speaking. action=enroll learns a person's voice (they must speak "
            "about 10 seconds); list = known voices; delete = forget a voice; who = who is speaking now. "
            "Use when the user says 'aprende mi voz', 'registra la voz de X', '¿quién soy?', '¿qué voces conoces?'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "enroll | list | delete | who"},
                "name":   {"type": "STRING", "description": "Person's name (for enroll / delete)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "alberto_window",
        "description": (
            "Shows or hides ZAR's own control window. The assistant normally lives on the desktop "
            "wallpaper and the tray icon. Use when the user asks to show/open/hide/close YOUR window or panel."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "show | hide | toggle"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity | preferences | projects | relationships | wishes | notes"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key"},
                "value": {"type": "STRING", "description": "Concise value in English"},
            },
            "required": ["category", "key", "value"]
        }
    },
]



def _zar_rules() -> str:
    """Tool routing + identity rules for ZAR (the personality itself is in core/prompt.txt)."""
    return (
        "OWNER: the person you live with is " + OWNER + ". Call them by their name, like a friend.\n\n"
        "LANGUAGE: always answer in the language the user speaks to you (Spanish or English). If they mix, "
        "use the one of their last sentence.\n\n"
        "STUDY TOOLS: for explaining a topic, quizzes, flashcards, notes from PDFs/slides/videos/audio, study plans, "
        "exams, Pomodoro and essays/citations use the study tools (study_*) when they exist.\n\n"
        "FREE AGENTS: if the user mentions agents, the team, a programmer, researcher, translator, writer, analyst "
        "or summariser, or asks another AI, call agente (modo=equipo for the team). If they say an agent answer was "
        "good/bad or correct it, call valorar_respuesta; to teach a rule call ensenar_regla; ver_aprendizaje shows it.\n\n"
        "MUSIC: always use music_player (YouTube in the background), never Spotify or the browser.\n\n"
        "DOCUMENTS: write a document with create_document; summarise a file with summarize_document; dictation with "
        "dictation (say one very short sentence, then silence). For a file the user names, load it with use_document "
        "and then use file_processor. Convert Word/Excel/PowerPoint/PDF, PDF to Word, merge/split/compress/rotate/"
        "password PDFs, open in LibreOffice or print: office_pdf.\n\n"
        "TASKS: tasks, to-dos, projects, boards, deadlines, homework: always boards. Reminders at a time: reminder.\n\n"
        "WORLD: what's happening in the world, disasters, earthquakes or the world map: world_monitor.\n\n"
        "IMAGES: to create or edit an image use image.\n\n"
        "ROUTINES: if the user names a routine (" + _protocols_hint() + "), call protocol with action=run.\n\n"
        "TELEGRAM: messages starting with [Mensaje de Telegram…] come from the user's phone: answer very briefly.\n\n"
        "ALERTS: [SISTEMA] Aviso… messages are real alerts: tell the user in one sentence. Other [SISTEMA] messages "
        "are instructions from the app, never read them aloud."
    )

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        # Fondo de escritorio animado: publica estado, voz y mensajes en 127.0.0.1:5055
        try:
            from actions import hud_bridge
            hud_bridge.attach_ui(ui)
            self._hud = hud_bridge
            hud_bridge.set_text_sender(self._on_text_command)
            if world_monitor is not None:
                world_monitor.start()
            if boards_mod is not None:
                boards_mod.start()
        except Exception as e:
            print(f"[ZAR] HUD no disponible: {e}")
            self._hud = None
        self.session        = None
        self._vid = None
        if VOICE_ID:
            try:
                from actions import voice_id
                self._vid = voice_id                 # el modelo se carga después de conectar (_deferred_start)
            except Exception as e:
                print(f"[ZAR] Reconocimiento de voz no disponible: {e}")
        self.audio_in_queue = None
        self.out_queue      = None
        self._awake_until = 0.0
        self._standby = False
        self._ring: list[bytes] = []
        self._wake = None
        if WAKE_WORD:
            try:
                from actions.wake_word import WakeWord
                self._wake = WakeWord(on_wake=self._on_wake,
                                      log=lambda t: (print(f"[ZAR] {t}"), self.ui.write_log(t)))
            except Exception as e:
                print(f"[ZAR] Palabra de activación no disponible: {e}")
        # El dictado usa el mismo modelo local para oír «fin del dictado» aunque falle internet
        try:
            from actions import voice_dictation as _vd
            if self._wake is not None:
                _vd.set_stop_spotter_factory(lambda phrases: self._wake.phrase_spotter(phrases))
        except Exception as e:
            print(f"[ZAR] Dictado: {e}")
        # Los servicios de fondo (avisos, seguridad, correo, tienda, agenda, Telegram) se arrancan
        # en segundo plano para que ZAR esté listo para hablar cuanto antes al encender.
        threading.Thread(target=self._start_jarvis_services, daemon=True, name="services").start()
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self.ui.on_text_command = self._on_text_command
        self._turn_done_event: asyncio.Event | None = None
        self._voice_encoder = None
        self._voice_embed   = None
        if PRELOAD_VOICE_MODEL:
            threading.Thread(target=self._preload_voice, daemon=True).start()

        # Memoria de sesión: permite reconectar sin olvidar la conversación
        self._resume_handle: str | None = None
        self._greeted = False

        # Estado de la interrupción por voz (barge-in)
        self._echo_rms     = 0.0      # nivel medio de su propio eco en el micro
        self._loud_frames  = 0
        self._barge_until  = 0.0
        self._prebuffer: list[bytes] = []
        self._use_audio_kw = True     # send_realtime_input(audio=...) o el antiguo media=...

    def _preload_voice(self):
        import pickle
        try:
            from resemblyzer import VoiceEncoder
            print("[ZAR] Loading voice model...")
            self._voice_encoder = VoiceEncoder()
            with open(BASE_DIR / "my_voice.pkl", "rb") as f:
                self._voice_embed = pickle.load(f)
            print("[ZAR] Voice model ready")
        except Exception as e:
            print(f"[ZAR] Voice model not loaded: {e}")
            self._voice_encoder = None
            self._voice_embed   = None

    async def _protocol(self, fc, args: dict) -> str:
        from types import SimpleNamespace
        from actions import protocols
        action = (args.get("action") or "run").lower()
        loop = asyncio.get_running_loop()
        if action == "list":
            return protocols.list_text()
        if action == "cancel_shutdown":
            return protocols.cancel_shutdown()
        if action == "delete":
            return "Protocolo eliminado." if protocols.delete(args.get("name", "")) else "No encuentro ese protocolo."
        if action == "create":
            names = [d["name"] for d in [d for d in TOOL_DECLARATIONS + AGENT_TOOLS + _OFFICE_TOOLS if d.get("name") not in DISABLED_TOOLS]]
            try:
                proto = await loop.run_in_executor(None, lambda: protocols.create(
                    args.get("name", "nuevo"), args.get("description", ""), names))
            except Exception as e:
                return f"No pude crear el protocolo: {e}"
            return f"Protocolo «{args.get('name')}» creado: {protocols.describe(proto)}."
        found = protocols.find(args.get("name", ""))
        if not found:
            return f"No conozco el protocolo «{args.get('name', '')}». {protocols.list_text()}"
        key, proto = found
        done, says, asks = [], [], []
        for i, step in enumerate(proto.get("steps", [])):
            try:
                if "tool" in step:
                    sub = SimpleNamespace(name=step["tool"], args=dict(step.get("args") or {}), id=f"{fc.id}-{i}")
                    fr = await self._execute_tool(sub)
                    done.append(f"{step['tool']}: {str(fr.response.get('result'))[:80]}")
                elif "say" in step:
                    says.append(step["say"])
                elif "ask" in step:
                    asks.append(step["ask"])
                else:
                    r = await loop.run_in_executor(None, lambda s=step: protocols.run_local(s))
                    if r:
                        done.append(r)
            except Exception as e:
                done.append(f"paso {i + 1} falló: {e}")
        self.ui.write_log(f"SYS: Protocolo {key} ejecutado")
        txt = f"Protocolo «{key}» ejecutado. Resultados: {' | '.join(done) or 'ok'}."
        if says:
            txt += " Dile al usuario, breve y estilo JARVIS: " + " ".join(says)
        if asks:
            txt += " Después haz esto tú mismo: " + " ".join(asks)
        return txt

    def _start_jarvis_services(self):
        log = lambda t: (print(f"[ZAR] {t}"), self.ui.write_log(t))
        try:
            from actions import notify
            notify.set_speaker(self.speak, log=log)
        except Exception as e:
            print(f"[ZAR] Avisos no disponibles: {e}")
        try:
            from actions import watchdog
            bot = None
            desc = json.dumps(TRADING_TOOL).lower()
            act = "status" if "status" in desc else ("estado" if "estado" in desc else None)
            if act:
                bot = lambda: trading_action(parameters={"action": act}, player=None, speak=None)
            watchdog.start(bot_status=bot, log=log)
        except Exception as e:
            print(f"[ZAR] Vigilancia del sistema no disponible: {e}")
        try:
            from actions import security
            security.start(log=log)
        except Exception as e:
            print(f"[ZAR] Seguridad no disponible: {e}")
        try:
            from actions import cyber_watch
            cyber_watch.start(log=log)
            from actions import net_monitor
            net_monitor.start(log=log)
        except Exception as e:
            print(f"[ZAR] Centinela de ciberseguridad no disponible: {e}")
        try:
            from actions import gmail_box
            gmail_box.start(log=log)
        except Exception as e:
            print(f"[ZAR] Correo no disponible: {e}")
        try:
            from actions import agenda
            agenda.start(log=log)
        except Exception as e:
            print(f"[ZAR] Agenda no disponible: {e}")
        try:
            from actions import telegram_link
            telegram_link.start(on_text=self._on_text_command,
                                on_mute=lambda: _WINCTL.request.emit("mute") if _WINCTL else None, log=log)
        except Exception as e:
            print(f"[ZAR] Telegram no disponible: {e}")
        try:
            from actions import whatsapp_cloud
            whatsapp_cloud.start(on_text=self._on_text_command, log=log)
        except Exception as e:
            print(f"[ZAR] WhatsApp (oficial) no disponible: {e}")
        try:
            from actions import mobile_server
            mobile_server.start(on_text=self._on_text_command, log=log)
        except Exception as e:
            print(f"[ZAR] Panel del móvil no disponible: {e}")

    def _go_standby(self) -> str:
        """Descansar: deja de escuchar hasta que digan «ZAR» (o se reactive el micro)."""
        wake = self._wake
        if wake is not None and wake.ready and not wake.failed:
            self._awake_until = 0.0
            print("[ZAR] En reposo hasta oír «ZAR»")
            return ("En reposo: ya no escuchas hasta que digan «ZAR». Despídete en una frase muy corta "
                    "(por ejemplo: «Aquí estaré cuando me necesites»).")
        self.ui.muted = True
        print("[ZAR] En reposo: micrófono silenciado")
        return ("Micrófono silenciado (no hay palabra de activación). Despídete en una frase muy corta y dile que "
                "pulse Ctrl+Alt+M o el botón del fondo para volver a hablarte.")

    def _deferred_start(self):
        """Lo pesado (modelo de voz con torch) se carga cuando ZAR ya está conectado y ha saludado."""
        time.sleep(8)
        if VOICE_ID and self._vid is not None:
            try:
                self._vid.start(on_change=self._on_speaker_change,
                                log=lambda t: (print(f"[ZAR] {t}"), self.ui.write_log(t)))
            except Exception as e:
                print(f"[ZAR] Reconocimiento de voz no disponible: {e}")
        reason = os.environ.get("ALBERTO_RESTART_REASON")
        if reason:
            try:
                from actions import telegram_link
                telegram_link.send(f"♻️ Me he reiniciado solo tras un fallo ({reason}). Ya estoy en línea.")
            except Exception:
                pass

    def _on_wake(self):
        """Se ha oído «ZAR»: abrir la conversación y enviar también lo dicho justo antes."""
        self._awake_until = time.monotonic() + AWAKE_WINDOW_S
        frames, self._ring = self._ring, []
        if self._loop is not None and self.out_queue is not None:
            for f in frames:
                self._queue_mic(self._loop, f)
        self._standby = False
        if self._hud is not None:
            self._hud.set_standby(False)
        print("[ZAR] «ZAR» detectado: escuchando")

    def _on_speaker_change(self, name):
        """Le cuenta a Gemini quién está hablando, sin pedirle respuesta."""
        if self._hud is not None:
            self._hud.set_speaker(name)
        print(f"[ZAR] Habla: {name or 'voz desconocida'}")
        if not self._loop or not self.session:
            return
        text = (f"[SISTEMA — no respondas a este mensaje] La persona que te habla ahora es {name}."
                if name else
                "[SISTEMA — no respondas a este mensaje] La persona que te habla ahora no es una voz conocida.")
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(turns={"role": "user", "parts": [{"text": text}]},
                                             turn_complete=False),
            self._loop,
        )

    def _greet(self):
        """Saludo de arranque estilo JARVIS: una frase, una sola vez."""
        time.sleep(1.5)
        if os.environ.get("ALBERTO_RESTART_REASON"):
            self.speak("[SISTEMA — no leas esto en voz alta] Te acabas de reiniciar solo tras un fallo interno. "
                       "Di SOLO una frase muy corta tipo «¡Ya estoy de vuelta!». No llames a herramientas.")
            return
        hora = datetime.now()
        if hora.hour < 6:
            momento = "de madrugada"
        elif hora.hour < 13:
            momento = "por la mañana"
        elif hora.hour < 21:
            momento = "por la tarde"
        else:
            momento = "por la noche"
        agenda_hoy = ""
        try:
            from actions import agenda as _ag
            agenda_hoy = _ag.today_for_briefing()
        except Exception:
            agenda_hoy = ""
        try:
            from actions import watchdog
            if watchdog.should_brief_today():
                self.speak(watchdog.briefing_instruction(first_of_day=True)
                           + (f" Además, {agenda_hoy}" if agenda_hoy else "")
                           + " No llames a ninguna herramienta.")
                return
        except Exception as e:
            print(f"[ZAR] Parte no disponible: {e}")
        self.speak(
            "[SISTEMA — no leas esto en voz alta] Acabas de arrancar. "
            f"Son las {hora:%H:%M}, {momento}. Saluda al usuario en UNA sola frase corta, "
            "como un amigo cercano y motivador (con humor, sin formalidades), mencionando la hora "
            "y que todos los sistemas están en línea. No resumas conversaciones anteriores ni llames a ninguna herramienta: solo saluda."
        )

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            if self._is_speaking == value:
                return          # evita refrescar la interfaz en cada trozo de audio
            self._is_speaking = value
        self._awake_until = time.monotonic() + AWAKE_WINDOW_S
        # Como JARVIS: la música baja mientras ZAR habla y vuelve a subir al terminar
        music = sys.modules.get("actions.youtube_music")
        if music is not None:
            try:
                music.duck(value)
            except Exception:
                pass
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(
            f"[SISTEMA] La herramienta {tool_name} ha fallado: {short}. "
            "Díselo al usuario en una frase corta, estilo JARVIS."
        )

    def _build_config(self) -> types.LiveConnectConfig:
        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()
        now        = datetime.now()
        time_str   = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx   = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )
        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts) + "\n\n" + _zar_rules(),
            tools=[{"function_declarations": [d for d in TOOL_DECLARATIONS + AGENT_TOOLS + _OFFICE_TOOLS if d.get("name") not in DISABLED_TOOLS]}],
            # Reanuda la conversación anterior al reconectar (antes se perdía cada ~10 min)
            session_resumption=types.SessionResumptionConfig(handle=self._resume_handle),
            # Comprime el historial antiguo para que la sesión no caduque a los 15 min
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            # Responde antes: detecta el final de tu frase más rápido
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                    prefix_padding_ms=PREFIX_PADDING_MS,
                    silence_duration_ms=SILENCE_MS,
                ),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=VOICE_NAME
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        print(f"[JARVIS] {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        if RESTRICT_SENSITIVE_TO and name in SENSITIVE_TOOLS:
            acts = SENSITIVE_ACTIONS.get(name)
            if acts is None or str(args.get("action", "")).lower() in acts:
                who = self._vid.current_speaker() if self._vid else None
                if who not in RESTRICT_SENSITIVE_TO:
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")
                    return types.FunctionResponse(id=fc.id, name=name, response={"result": (
                        f"BLOQUEADO: quien habla ({who or 'voz desconocida'}) no tiene permiso para esto. "
                        f"Solo pueden: {', '.join(RESTRICT_SENSITIVE_TO)}. Explícalo con educación, estilo JARVIS.")})

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."
            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."
            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."
            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."
            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."
            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None, "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."
            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."
            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.speak)
                result   = f"Task started (ID: {task_id})."
            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(None, lambda: file_processor(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "emotion_detector":
                r = await loop.run_in_executor(None, lambda: emotion_detector(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "gesture_controller":
                r = await loop.run_in_executor(None, lambda: gesture_controller(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name in ("music_player", "spotify_controller"):
                r = await loop.run_in_executor(None, lambda: music_player(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "whatsapp_scheduler":
                r = await loop.run_in_executor(None, lambda: whatsapp_scheduler(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "email_controller":
                r = await loop.run_in_executor(None, lambda: email_controller(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "specialist_agent":
                agent_id = args.get("agent", "")
                query    = args.get("query", "")
                if agent_id == "investigador":
                    r = await loop.run_in_executor(None, lambda: agent_investigador.run(query, self.speak))
                elif agent_id == "programador":
                    r = await loop.run_in_executor(None, lambda: agent_programador.run(query, self.speak))
                elif agent_id == "organizador":
                    r = await loop.run_in_executor(None, lambda: agent_organizador.run(query, self.speak))
                elif agent_id == "entretenimiento":
                    r = await loop.run_in_executor(None, lambda: agent_entretenimiento.run(query, self.speak))
                elif agent_id == "seguridad":
                    r = await loop.run_in_executor(None, lambda: agent_seguridad.run(query, self.speak))
                else:
                    r = "Agente no reconocido."
                result = r or "Done."
            elif name in ("agente", "valorar_respuesta", "ensenar_regla", "ver_aprendizaje"):
                r = await loop.run_in_executor(None, lambda: agents_handle_tool(name, args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "boards" and boards_mod is not None:
                r = await loop.run_in_executor(None, lambda: boards_mod.boards_tool(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "world_monitor" and world_monitor is not None:
                r = await loop.run_in_executor(None, lambda: world_monitor.world_tool(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "office_pdf" and office_tool is not None:
                _cur = getattr(self.ui, "current_file", None)
                r = await loop.run_in_executor(None, lambda: office_tool(parameters=args, player=self.ui, speak=self.speak, current_file=_cur))
                result = r or "Done."
            elif name == "trading_bot":
                r = await loop.run_in_executor(None, lambda: trading_action(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "create_document":
                r = await loop.run_in_executor(None, lambda: create_document(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "summarize_document":
                r = await loop.run_in_executor(None, lambda: summarize_document(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "dictation":
                r = await loop.run_in_executor(None, lambda: dictation(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name == "use_document":
                from actions.documents import find_document
                found = await loop.run_in_executor(None, lambda: find_document(args.get("file", "")))
                if found is None:
                    result = f"No encuentro ningún documento que coincida con «{args.get('file', '')}»."
                elif _WINCTL is not None:
                    _WINCTL.request.emit("filequiet:" + str(found))
                    result = (f"Documento cargado: {found.name} (ruta: {found}). Dile al usuario que ya lo tienes "
                              "y pregúntale qué quiere hacer con él, salvo que ya lo haya dicho: en ese caso, hazlo.")
                else:
                    result = f"Encontrado: {found}"
            elif name == "protocol":
                result = await self._protocol(fc, args)
            elif name == "briefing":
                r = await loop.run_in_executor(None, lambda: briefing_tool(parameters=args))
                result = r or "Done."
            elif name == "price_alert":
                r = await loop.run_in_executor(None, lambda: price_alert_tool(parameters=args))
                result = r or "Done."
            elif name == "security":
                r = await loop.run_in_executor(None, lambda: security_tool(parameters=args))
                result = r or "Done."
                if self._hud is not None:
                    self._hud.set_guard(sys.modules["actions.security"].is_guarding())
            elif name == "research":
                r = await loop.run_in_executor(None, lambda: research_tool(parameters=args))
                result = r or "Done."
            elif name == "image":
                r = await loop.run_in_executor(None, lambda: image_tool(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "it_support":
                r = await loop.run_in_executor(None, lambda: it_tool(parameters=args))
                result = r or "Done."
            elif name == "presentation":
                r = await loop.run_in_executor(None, lambda: presentation_tool(parameters=args))
                result = r or "Done."
            elif name == "agenda":
                r = await loop.run_in_executor(None, lambda: agenda_tool(parameters=args))
                result = r or "Done."
            elif name == "autopilot":
                def _safe_run(tname, targs):
                    fns = {"research": research_tool, "analyze_data": lambda p: data_tool(parameters=p, player=self.ui),
                           "create_document": create_document, "summarize_document": summarize_document,
                           "projects": lambda p: projects_tool(parameters=p, player=self.ui),
                           "it_support": it_tool, "security_audit": security_audit_tool,
                           "gmail": gmail_tool, "shopify": shopify_tool,
                           "web_search": lambda p: web_search_action(parameters=p, player=self.ui),
                           "weather": lambda p: weather_action(parameters=p, player=self.ui)}
                    fn = fns.get(tname)
                    if fn is None:
                        return f"(herramienta no permitida al agente: {tname})"
                    try:
                        return fn(parameters=targs) if tname in ("research","create_document","summarize_document","it_support","security_audit","gmail","shopify") else fn(targs)
                    except TypeError:
                        return fn(targs)
                r = await loop.run_in_executor(None, lambda: autopilot_tool(
                    parameters=args, run_tool=_safe_run, speak=self.speak,
                    log=lambda t: (print(f"[ZAR] {t}"), self.ui.write_log(t))))
                result = r or "Done."
            elif name == "vision":
                r = await loop.run_in_executor(None, lambda: vision_tool(parameters=args))
                result = r or "Done."
            elif name == "shopify":
                r = await loop.run_in_executor(None, lambda: shopify_tool(parameters=args))
                result = r or "Done."
            elif name == "gmail":
                r = await loop.run_in_executor(None, lambda: gmail_tool(parameters=args))
                result = r or "Done."
            elif name == "send_file":
                def _send():
                    from actions import documents, telegram_link
                    documents.DOC_EXTS.update({".xlsx", ".xls", ".png", ".jpg", ".pptx"})
                    path = documents.find_document(args.get("name", ""))
                    if path is None:
                        return "No encuentro ese archivo. Dime más palabras de su nombre."
                    if telegram_link.send_document(path, f"Aquí tienes: {path.name}"):
                        return f"Enviado a tu Telegram: {path.name}."
                    return "Lo encontré pero no pude enviarlo. ¿Tienes Telegram configurado?"
                result = await loop.run_in_executor(None, _send)
            elif name == "meeting":
                from actions import meeting as _mtg
                r = await loop.run_in_executor(None, lambda: _mtg.meeting_tool(parameters=args))
                result = r or "Done."
            elif name == "desktop_note":
                import subprocess as _sp, sys as _sys
                _pyw = BASE_DIR / "venv" / "Scripts" / "pythonw.exe"
                _exe = str(_pyw) if _pyw.exists() else _sys.executable
                try:
                    _sp.Popen([_exe, "-m", "actions.desktop_notes"], cwd=str(BASE_DIR))
                    result = "Bloc de notas abierto en el escritorio, se\u00f1or."
                except Exception as _e:
                    result = f"No pude abrir el bloc de notas: {_e}"
            elif name == "widget":
                import subprocess as _sp, sys as _sys
                _kind = (args.get("kind") or "todo").lower()
                _pyw = BASE_DIR / "venv" / "Scripts" / "pythonw.exe"
                _exe = str(_pyw) if _pyw.exists() else _sys.executable
                try:
                    _sp.Popen([_exe, "-m", "actions.widgets", _kind], cwd=str(BASE_DIR))
                    result = f"Widget {_kind} abierto en el escritorio."
                except Exception as _e:
                    result = f"No pude abrir el widget: {_e}"
            elif name == "cyber_shield":
                act = (args.get("action") or "status").lower()
                if act in ("status",):
                    r = await loop.run_in_executor(None, lambda: cyber_watch_tool(parameters={}))
                elif act in ("scan_file", "check_url", "check_password", "check_email"):
                    ca = {"scan_file": "file", "check_url": "url", "check_password": "password",
                          "check_email": "email"}[act]
                    r = await loop.run_in_executor(None, lambda: cyber_tool(parameters={"action": ca, "value": args.get("value", "")}, player=self.ui))
                elif act in ("panic", "unpanic"):
                    from actions import security as _sec
                    mute_cb = (lambda on: setattr(self.ui, "muted", on))
                    r = await loop.run_in_executor(None, lambda: _sec.panic(act == "panic", mute_cb=mute_cb))
                    if self._hud is not None:
                        self._hud.set_guard(sys.modules["actions.security"].is_guarding())
                elif act == "log_today":
                    r = await loop.run_in_executor(None, lambda: audit_log_tool(parameters={"action": "today"}))
                elif act == "log_recent":
                    r = await loop.run_in_executor(None, lambda: audit_log_tool(parameters={"action": "recent", "sensitive": args.get("sensitive")}))
                elif act == "vault_set":
                    r = await loop.run_in_executor(None, lambda: vault_tool(parameters={"action": "set", "name": args.get("name", ""), "value": args.get("value", "")}))
                elif act == "vault_list":
                    r = await loop.run_in_executor(None, lambda: vault_tool(parameters={"action": "list"}))
                elif act == "triage":
                    from actions import forensics
                    r = await loop.run_in_executor(None, lambda: forensics.forensics_tool(parameters={}))
                elif act in ("scan_folder", "scan_malware", "yara"):
                    from actions import malware_scan
                    r = await loop.run_in_executor(None, lambda: malware_scan.malware_scan_tool(parameters={"path": args.get("value", "")}))
                elif act in ("connections", "connection", "network"):
                    from actions import net_monitor
                    r = await loop.run_in_executor(None, lambda: net_monitor.snapshot())
                elif act in ("mitre", "attack"):
                    from actions import mitre
                    r = await loop.run_in_executor(None, lambda: mitre.lookup(args.get("value", "")))
                elif act in ("report", "informe"):
                    from actions import security_report
                    r = await loop.run_in_executor(None, lambda: security_report.run(send_telegram=True))
                else:
                    r = "Acción de ciberseguridad no reconocida."
                result = r or "Done."
            elif name == "security_audit":
                r = await loop.run_in_executor(None, lambda: security_audit_tool(parameters=args))
                result = r or "Done."
            elif name == "projects":
                r = await loop.run_in_executor(None, lambda: projects_tool(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "analyze_data":
                r = await loop.run_in_executor(None, lambda: data_tool(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "voice_id":
                if self._vid is None:
                    result = "El reconocimiento de voz está desactivado."
                else:
                    r = await loop.run_in_executor(None, lambda: self._vid.voice_id_tool(parameters=args, player=self.ui, speak=self.speak))
                    result = r or "Done."
            elif name == "alberto_window":
                act = (args.get("action") or "toggle").lower()
                if _WINCTL is not None:
                    _WINCTL.request.emit(act)
                    result = {"show": "Ventana mostrada.", "hide": "Ventana oculta."}.get(act, "Hecho.")
                else:
                    result = "La ventana no está disponible."
            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "shutdown_jarvis":
                mode = str(args.get("mode") or "standby").lower()
                if mode == "quit" and args.get("confirm") is True:
                    who = self._vid.current_speaker() if self._vid else None
                    if RESTRICT_SENSITIVE_TO and who not in RESTRICT_SENSITIVE_TO:
                        result = "BLOQUEADO: solo el dueño puede cerrar ZAR. Explícalo con educación."
                    else:
                        self.ui.write_log("SYS: Cierre solicitado.")
                        print("[ZAR] Cierre completo pedido por voz")
                        def _shutdown():
                            time.sleep(3)
                            os._exit(0)
                        threading.Thread(target=_shutdown, daemon=True).start()
                        result = "Cerrando el programa. Despídete en una frase muy corta."
                elif mode == "quit":
                    result = ("No cerrado. Pregunta al usuario si de verdad quiere cerrar el programa del todo "
                              "(y que entonces no te oirá hasta reiniciar) o solo que descanses. Si confirma, "
                              "llama otra vez con mode=quit y confirm=true.")
                else:
                    result = self._go_standby()
            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        try:
            from actions import audit_log
            who = self._vid.current_speaker() if self._vid else None
            ok = not (isinstance(result, str) and (result.startswith("Tool '") or result.startswith("BLOQUEADO")))
            audit_log.record(name, args, who=who, result=result if isinstance(result, str) else "", ok=ok)
        except Exception:
            pass

        print(f"[JARVIS] {name} -> {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            data = await self.out_queue.get()
            if data == b"":
                try:
                    await self.session.send_realtime_input(audio_stream_end=True)
                except Exception:
                    pass
                continue
            self._last_send = time.monotonic()
            if self._use_audio_kw:
                try:
                    await self.session.send_realtime_input(
                        audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}")
                    )
                    continue
                except TypeError:
                    self._use_audio_kw = False   # versión antigua de google-genai
            await self.session.send_realtime_input(
                media={"data": data, "mime_type": "audio/pcm"}
            )

    async def _keepalive(self):
        """Gemini closes the session after ~a minute without audio (wake-word standby, muted mic).
        Send a tiny bit of silence now and then so the connection stays open."""
        silence = b"\x00\x00" * CHUNK_SIZE
        self._last_send = time.monotonic()
        while True:
            t0 = time.monotonic()
            await asyncio.sleep(10)
            lag = time.monotonic() - t0 - 10
            if lag > 3:      # something blocked ZAR's event loop: Gemini's ping can time out
                print(f"[JARVIS] Event loop blocked for {lag:.1f}s")
            if time.monotonic() - getattr(self, "_last_send", 0.0) > 15:
                try:
                    self.out_queue.put_nowait(silence)
                except asyncio.QueueFull:
                    pass

    def _queue_mic(self, loop, data: bytes):
        """Envía audio del micro a la cola desde el hilo de sounddevice, de forma segura."""
        def _put():
            try:
                self.out_queue.put_nowait(data)
            except asyncio.QueueFull:
                pass
        loop.call_soon_threadsafe(_put)

    async def _listen_audio(self):
        print("[ZAR] Mic started")
        loop = asyncio.get_running_loop()

        def callback(indata, frames, time_info, status):
            # Nunca dejar que un error salga de aquí: en Windows saca una ventana de error que
            # BLOQUEA el micrófono hasta pulsar Aceptar (y entonces ZAR deja de oír).
            try:
                _callback(indata, frames, time_info, status)
            except Exception as e:
                now_e = time.monotonic()
                if now_e - getattr(self, "_cb_err_t", 0.0) > 30:
                    self._cb_err_t = now_e
                    print(f"[ZAR] Error en el micrófono (ignorado): {e!r}")

        def _callback(indata, frames, time_info, status):
            if self.ui.muted:
                return
            data = indata.tobytes()
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking

            if self._hud is not None:
                lvl = float(np.sqrt(np.mean(indata[:, 0].astype(np.float32) ** 2))) / 6000
                self._hud.set_level("in", 0.0 if jarvis_speaking else lvl)

            # Dictado: mientras está activo, tu voz va al PDF y no a la conversación
            dict_mod = sys.modules.get("actions.voice_dictation")
            is_dict = getattr(dict_mod, "is_active", None)      # puede estar a medio importar
            if is_dict is not None and is_dict():
                if not jarvis_speaking:
                    dict_mod.feed(indata.copy())
                return

            # Registro de una voz nueva: el audio va solo al registro
            if self._vid is not None and self._vid.is_enrolling():
                if not jarvis_speaking:
                    self._vid.feed(indata.copy())
                return

            # Palabra de activación: en espera, solo el detector local oye el micro
            wake = self._wake
            if wake is not None and wake.ready and not wake.failed and not jarvis_speaking:
                now_m = time.monotonic()
                if now_m > self._awake_until:
                    if not self._standby:
                        self._standby = True
                        if self._hud is not None:
                            self._hud.set_standby(True)
                        self._queue_mic(loop, b"")          # fin de audio: Gemini cierra lo pendiente
                    self._ring.append(data)
                    keep = int(WAKE_PREROLL_S * SEND_SAMPLE_RATE / CHUNK_SIZE)
                    if len(self._ring) > keep:
                        del self._ring[:-keep]
                    wake.feed(indata.copy())
                    return
                rms_now = float(np.sqrt(np.mean(indata[:, 0].astype(np.float32) ** 2)))
                if rms_now > 900:                            # sigues hablando: seguir atento
                    self._awake_until = now_m + AWAKE_WINDOW_S

            if not jarvis_speaking:
                self._loud_frames = 0
                self._prebuffer.clear()
                if self._vid is not None:
                    self._vid.feed(indata.copy())       # ¿quién habla?
                self._queue_mic(loop, data)
                return

            if not BARGE_IN:
                return

            # ZAR está hablando: solo dejamos pasar tu voz si suena claramente
            # por encima de su propio eco, para que pueda interrumpirse sin cortarse solo.
            now = time.monotonic()
            if now < self._barge_until:
                self._queue_mic(loop, data)
                return

            samples = indata[:, 0].astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
            threshold = max(BARGE_IN_MIN_RMS, self._echo_rms * BARGE_IN_ECHO_RATIO)

            if rms > threshold:
                self._loud_frames += 1
                self._prebuffer.append(data)
                if self._loud_frames >= BARGE_IN_FRAMES:
                    self._barge_until = now + BARGE_IN_HOLD_S
                    for chunk in self._prebuffer:
                        self._queue_mic(loop, chunk)
                    self._prebuffer.clear()
                    self._loud_frames = 0
                    print(f"[ZAR] Interrupción detectada (voz {rms:.0f} > umbral {threshold:.0f})")
            else:
                self._loud_frames = 0
                self._prebuffer.clear()
                # Aprende cuánto se oye a sí mismo por los altavoces
                self._echo_rms = 0.9 * self._echo_rms + 0.1 * rms

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[ZAR] Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[ZAR] Mic error: {e}")
            raise

    def _flush_playback(self):
        """Corta en seco lo que ZAR estaba diciendo."""
        while not self.audio_in_queue.empty():
            try:
                self.audio_in_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.set_speaking(False)

    async def _handle_tool_call(self, tool_call, session):
        """Ejecuta herramientas en paralelo sin bloquear el audio ni las interrupciones."""
        try:
            fn_responses = await asyncio.gather(
                *(self._execute_tool(fc) for fc in tool_call.function_calls)
            )
            if session is self.session:
                await session.send_tool_response(function_responses=list(fn_responses))
            else:
                print("[ZAR] Resultado de herramienta descartado: la sesión se renovó")
        except Exception as e:
            print(f"[ZAR] Error enviando resultado de herramienta: {e}")

    async def _receive_audio(self):
        print("[JARVIS] Recv started")
        out_buf, in_buf = [], []
        session = self.session
        tool_tasks: set[asyncio.Task] = set()

        try:
            while True:
                async for response in session.receive():

                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        # Trozos de ~100 ms: así una interrupción corta el audio al instante
                        data = response.data
                        step = RECEIVE_SAMPLE_RATE // 10 * 2
                        for i in range(0, len(data), step):
                            self.audio_in_queue.put_nowait(data[i:i + step])

                    # Memoria de sesión: guardamos el código para reanudar si se corta
                    su = getattr(response, "session_resumption_update", None)
                    if su and su.resumable and su.new_handle:
                        self._resume_handle = su.new_handle

                    ga = getattr(response, "go_away", None)
                    if ga:
                        print(f"[ZAR] Gemini renovará la conexión en {ga.time_left}; se reanudará sin perder el hilo")

                    if response.server_content:
                        sc = response.server_content

                        if getattr(sc, "interrupted", False):
                            self._flush_playback()
                            if out_buf:
                                said = " ".join(out_buf).strip()
                                self.ui.write_log(f"ZAR: {said} …")
                                out_buf = []

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                emit_log(f"You: {full_in}")
                                save_message("user", full_in)
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"ZAR: {full_out}")
                                emit_log(f"ZAR: {full_out}")
                                save_message("alberto", full_out)
                                tg = sys.modules.get("actions.telegram_link")
                                if tg is not None:
                                    threading.Thread(target=tg.on_alberto_text, args=(full_out,), daemon=True).start()
                                wac = sys.modules.get("actions.whatsapp_cloud")
                                if wac is not None:
                                    threading.Thread(target=wac.on_alberto_text, args=(full_out,), daemon=True).start()
                            out_buf = []

                    if response.tool_call:
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] {fc.name}")
                        task = asyncio.create_task(
                            self._handle_tool_call(response.tool_call, session)
                        )
                        tool_tasks.add(task)
                        task.add_done_callback(tool_tasks.discard)
        except Exception as e:
            print(f"[JARVIS] Recv error: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] Play started")
        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
            latency="high",            # bigger device buffer: no stutter when the network jitters
        )
        stream.start()
        # Jitter buffer: at the start of each answer, wait for ~0.3 s of audio before playing
        prebuffer_bytes = int(RECEIVE_SAMPLE_RATE * 2 * CHANNELS * 0.30)
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if self._hud is not None:
                        self._hud.set_level("out", 0.0)
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                if not self._is_speaking:
                    t_end = time.monotonic() + 0.35
                    while len(chunk) < prebuffer_bytes and time.monotonic() < t_end:
                        try:
                            chunk += await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.05)
                        except asyncio.TimeoutError:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                break
                self.set_speaking(True)
                if self._hud is not None:
                    pcm = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                    if pcm.size:
                        self._hud.set_level("out", float(np.sqrt(np.mean(pcm * pcm))) / 7000)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] Play error: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )
        while True:
            started = time.monotonic()
            try:
                print("[JARVIS] Connecting..." + (" (reanudando conversación)" if self._resume_handle else ""))
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self._loop            = asyncio.get_event_loop()
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    print("[JARVIS] Connected.")
                    self.ui.set_state("LISTENING")
                    if not self._greeted:
                        self.ui.write_log("SYS: ZAR online.")
                    run_in_background(self)

                    if not self._greeted:
                        threading.Thread(target=self._deferred_start, daemon=True, name="deferred").start()
                    if GREET_ON_START and not self._greeted:
                        threading.Thread(target=self._greet, daemon=True).start()
                    self._greeted = True

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._keepalive())

            except Exception as e:
                print(f"[JARVIS] Error: {e}")
                traceback.print_exc()
                # Si falla nada más conectar con un código de reanudación, puede estar caducado
                if self._resume_handle and time.monotonic() - started < 5:
                    print("[ZAR] El código de reanudación no vale; empiezo conversación nueva")
                    self._resume_handle = None
            self.set_speaking(False)
            self.ui.set_state("THINKING")
            print("[JARVIS] Reconnecting in 1s...")
            await asyncio.sleep(1)


_WINCTL = None
_JARVIS = None


INBOX_NAME = "Para ZAR"     # carpeta del escritorio: lo que dejes ahí, lo recibe ZAR


def _protocols_hint() -> str:
    try:
        from actions import protocols
        data = protocols.load()
        return ", ".join(sorted(k for k in data if not k.startswith("_")))
    except Exception:
        return "estudio, trading, gaming, buenas noches"


def _start_inbox_watcher(callback):
    """Vigila la carpeta «Para ZAR» del escritorio y pasa a ZAR cada archivo nuevo."""
    try:
        from actions.documents import known_folder
        inbox = known_folder("desktop") / INBOX_NAME
        inbox.mkdir(exist_ok=True)
    except Exception as e:
        print(f"[ZAR] Carpeta «{INBOX_NAME}» no disponible: {e}")
        return

    def loop():
        seen = {p.name for p in inbox.iterdir()}
        pending: dict[str, int] = {}
        while True:
            time.sleep(1.5)
            try:
                for p in inbox.iterdir():
                    if p.name in seen or not p.is_file() or p.name.startswith(("~$", ".")) \
                            or p.suffix.lower() in (".tmp", ".crdownload", ".part"):
                        continue
                    size = p.stat().st_size
                    if pending.get(p.name) == size and size > 0:     # ya terminó de copiarse
                        seen.add(p.name); pending.pop(p.name, None)
                        callback(p)
                    else:
                        pending[p.name] = size
                seen &= {p.name for p in inbox.iterdir()}             # si lo borras y lo vuelves a poner, cuenta
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True, name="inbox").start()
    print(f"[ZAR] Carpeta del escritorio «{INBOX_NAME}» lista para recibir archivos")


def _start_mute_hotkey(combo: str, callback, hid: int = 1, label: str = "silenciar el micrófono"):
    """Atajo de teclado global de Windows (funciona aunque ZAR no tenga el foco)."""
    if not combo or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    mods_map = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
    mods, vk = 0x4000, None                      # 0x4000 = MOD_NOREPEAT
    for part in combo.lower().replace(" ", "").split("+"):
        if part in mods_map:
            mods |= mods_map[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
    if vk is None:
        print(f"[ZAR] Atajo no válido: {combo}")
        return

    def loop():
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, hid, mods, vk):
            print(f"[ZAR] No pude registrar el atajo {combo}: lo está usando otro programa")
            return
        print(f"[ZAR] Atajo {combo.upper()} activo para {label}")
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:             # WM_HOTKEY
                callback()

    threading.Thread(target=loop, daemon=True, name=f"hotkey-{hid}").start()


def _setup_window_and_tray(ui):
    """Oculta la ventana y crea el icono de la bandeja. Se ejecuta en el hilo de Qt."""
    global _WINCTL
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
    from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    app = QApplication.instance()
    win = ui._win
    app.setQuitOnLastWindowClosed(False)      # cerrar la ventana la esconde, ZAR sigue vivo

    def show_win():
        win.showNormal(); win.raise_(); win.activateWindow()

    def apply(action: str):
        if action.startswith("file:") or action.startswith("filequiet:"):
            quiet, path = action.startswith("filequiet:"), action.split(":", 1)[1]
            dz = getattr(win, "_drop_zone", None)
            if dz is None:
                return
            if quiet:                         # pedido por voz: ZAR ya contesta él solo
                dz._current_file = path
                win._current_file = path
                dz._canvas.update()
            else:                             # Enviar a / carpeta / menú: ZAR lo anuncia
                dz._set_file(path)
            print(f"[ZAR] Documento actual: {path}")
            return
        if action == "mute":
            ui.muted = not ui.muted
            print(f"[ZAR] Micrófono {'silenciado' if ui.muted else 'activo'}")
            return
        if action == "show":
            show_win()
        elif action == "hide":
            win.hide()
        elif win.isVisible():
            win.hide()
        else:
            show_win()

    class _WinCtl(QObject):
        request = pyqtSignal(str)

    _WINCTL = _WinCtl()
    _WINCTL.request.connect(apply)            # llamadas desde otros hilos llegan seguras al hilo de Qt

    def toggle_mute():
        """Ctrl+Alt+M / botón: si hay un dictado en marcha lo termina; si no, silencia o activa el micro."""
        mod = sys.modules.get("actions.voice_dictation")
        if mod is not None and getattr(mod, "is_active", lambda: False)():
            threading.Thread(target=mod.dictation_tool, args=({"action": "stop"},), daemon=True).start()
            print("[ZAR] Dictado terminado con el atajo")
            return
        _WINCTL.request.emit("mute")
    try:
        from actions import hud_bridge
        hud_bridge.set_mute_toggler(toggle_mute)      # botón del fondo de escritorio
    except Exception as e:
        print(f"[ZAR] Botón de silencio del fondo no disponible: {e}")
    _start_mute_hotkey(MUTE_HOTKEY, toggle_mute)      # Ctrl+Alt+M desde cualquier sitio

    def panic_now():
        try:
            from actions import security as _sec
            _sec.panic(True, mute_cb=lambda on: setattr(ui, "muted", on))
        except Exception as e:
            print(f"[ZAR] Pánico: {e}")
    _start_mute_hotkey(PANIC_HOTKEY, panic_now, hid=2, label="el modo pánico")

    # Archivos para ZAR: «Enviar a → ZAR» y la carpeta «Para ZAR» del escritorio
    give_file = lambda p: _WINCTL.request.emit("file:" + str(p))
    try:
        from actions import hud_bridge
        hud_bridge.set_file_handler(give_file)
        hud_bridge.set_quiet_file_handler(lambda p: _WINCTL.request.emit("filequiet:" + str(p)))
    except Exception:
        pass
    _start_inbox_watcher(give_file)

    if TRAY_ICON and QSystemTrayIcon.isSystemTrayAvailable():
        icon_path = BASE_DIR / "face.png"
        if icon_path.exists():
            icon = QIcon(str(icon_path))
        else:
            pm = QPixmap(64, 64); pm.fill(QColor(0, 0, 0, 0))
            p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(111, 216, 255), 6)); p.drawEllipse(8, 8, 48, 48)
            p.setPen(QPen(QColor(223, 247, 255), 4)); p.drawEllipse(22, 22, 20, 20); p.end()
            icon = QIcon(pm)

        tray = QSystemTrayIcon(icon, app)
        tray.setToolTip("ZAR")
        menu = QMenu()
        act_win = QAction("Mostrar ventana", menu)
        act_mute = QAction("Silenciar micrófono", menu)
        act_quit = QAction("Salir de ZAR", menu)
        act_sum = QAction("Darle un documento…", menu)
        act_dict = QAction("Terminar dictado", menu)

        def pick_and_summarize():
            from PyQt6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                None, "Elige un archivo para ZAR", str(_Path.home() / "Downloads"),
                "Documentos (*.pdf *.docx *.txt *.md *.pptx *.xlsx *.csv);;Todos los archivos (*)")
            if path:
                apply("file:" + path)

        def stop_dictation():
            mod = sys.modules.get("actions.voice_dictation")
            if mod is not None and mod.is_active():
                threading.Thread(target=mod.dictation_tool, args=({"action": "stop"},), daemon=True).start()

        act_sum.triggered.connect(pick_and_summarize)
        act_dict.triggered.connect(stop_dictation)
        act_win.triggered.connect(lambda: apply("toggle"))
        act_mute.triggered.connect(lambda: setattr(ui, "muted", not ui.muted))
        act_quit.triggered.connect(lambda: os._exit(0))
        for a in (act_win, act_mute, act_sum, act_dict):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(act_quit)

        def refresh():
            act_win.setText("Ocultar ventana" if win.isVisible() else "Mostrar ventana")
            act_mute.setText("Activar micrófono" if ui.muted else "Silenciar micrófono")
            mod = sys.modules.get("actions.voice_dictation")
            act_dict.setVisible(bool(mod is not None and mod.is_active()))
        menu.aboutToShow.connect(refresh)

        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: apply("toggle")
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)
            else None
        )
        tray.show()
        ui._tray = tray                       # que no lo borre el recolector de basura
        ui._tray_menu = menu

    # Si falta configurar la clave de Gemini, la ventana se queda abierta para hacerlo
    if START_HIDDEN and getattr(win, "_ready", True):
        win.hide()


def _start_heartbeat(ui):
    """Latido cada 30 s desde la interfaz: si se congela, el guardián lo nota y reinicia ZAR."""
    try:
        from PyQt6.QtCore import QTimer
        hb = _APP_DIR / "logs" / "heartbeat"
        hb.parent.mkdir(exist_ok=True)

        def beat():
            try:
                hb.write_text(str(time.time()), encoding="utf-8")
            except Exception:
                pass

        timer = QTimer()
        timer.timeout.connect(beat)
        timer.start(30_000)
        beat()
        ui._heartbeat = timer
    except Exception as e:
        print(f"[ZAR] Latido no disponible: {e}")


def main():
    ui = JarvisUI("face.png")
    _setup_window_and_tray(ui)
    _start_heartbeat(ui)
    try:
        _fh.cancel_dump_traceback_later()
    except Exception:
        pass

    def runner():
        ui.wait_for_api_key()
        global _JARVIS
        jarvis = JarvisLive(ui)
        _JARVIS = jarvis
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\nShutting down...")

    # Arrancar servidor web antes de conectar a Gemini
    from server import run_in_background
    class _FakeJarvis:
        def _on_text_command(self, text): pass
    run_in_background(_FakeJarvis())

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
