"""
opencode_bridge.py — Módulo de acción de ZAR para usar OpenCode.

OpenCode (https://opencode.ai) da acceso a +75 proveedores / cientos de modelos.
Este módulo:
  1. Arranca `opencode serve` en segundo plano si no está ya corriendo.
  2. Detecta qué modelos tienes disponibles (según las API keys configuradas en OpenCode).
  3. Clasifica la petición (código, razonamiento, rápido, creativo, traducción, visión)
     y elige automáticamente el mejor modelo disponible para esa tarea.
  4. Envía el prompt y devuelve el texto de la respuesta.
  5. Si el servidor falla, usa `opencode run` por CLI como respaldo.

Requisitos:
  - OpenCode instalado:  npm i -g opencode-ai   (o el instalador de opencode.ai)
  - Haber hecho `opencode auth login` con al menos un proveedor.
  - pip install requests
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

# ─────────────────────────── Configuración ───────────────────────────

HOST = os.getenv("OPENCODE_HOST", "127.0.0.1")
PORT = int(os.getenv("OPENCODE_PORT", "4096"))
BASE_URL = f"http://{HOST}:{PORT}"
PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD")  # opcional
USERNAME = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
TIMEOUT = int(os.getenv("OPENCODE_TIMEOUT", "300"))
DEBUG = os.getenv("OPENCODE_DEBUG") == "1"

# Carpeta de trabajo donde OpenCode puede leer/escribir archivos (proyectos de código).
WORKDIR = os.getenv("OPENCODE_WORKDIR", str(Path.home() / "AlbertoWorkspace"))

CONFIG_FILE = Path(__file__).with_name("opencode_models.json")
# Resultado de probar cada modelo con tus cuentas reales (se crea con --probe)
STATUS_FILE = Path(WORKDIR) / "opencode_status.json"

# Errores que indican que el modelo NO está disponible para tu cuenta (no es un fallo puntual)
PERMANENT_ERRORS = ["not supported", "blocked", "not found", "permission", "unauthorized",
                    "forbidden", "invalid api key", "api key not valid", "does not exist",
                    "no access", "not available"]

# Preferencias por tipo de tarea: se prueban en orden y se usa el primer modelo
# que esté disponible en tu OpenCode. Son fragmentos del ID ("proveedor/modelo").
# Puedes sobrescribirlas en opencode_models.json.
#
# GPT-6: Astra = el más potente, Sol = intermedio (buen razonamiento, más barato),
# Luna = el más rápido y barato. Las variantes "-fast" se penalizan salvo en tareas rápidas.
DEFAULT_PREFERENCES = {
    "codigo":       ["gpt-6-astra", "claude-opus", "gemini-3.1-pro", "gpt-6-sol", "claude-sonnet",
                     "gpt-5.3-codex", "gpt-5", "gemini-2.5-pro"],
    "razonamiento": ["gpt-6-astra", "claude-opus", "gemini-3.1-pro", "gpt-6-sol", "gpt-5", "gemini-2.5-pro"],
    "rapido":       ["gpt-6-luna", "claude-haiku", "gemini-3.8-flash", "gemini-3", "gpt-5.6-luna", "gpt-5.4-mini",
                     "gemini-2.5-flash"],
    "creativo":     ["gpt-6-astra", "claude-opus", "gemini-3.1-pro", "gpt-6-sol", "claude-sonnet", "gpt-5"],
    "traduccion":   ["gemini-3.8-flash", "gpt-6-luna", "gemini-3", "claude-haiku", "gemini-2.5-flash"],
    "vision":       ["gemini-3.1-pro", "gpt-6-astra", "gpt-6-sol", "gemini-2.5-pro", "gemini-3.8-flash"],
    "general":      ["gpt-6-sol", "claude-sonnet", "gemini-3.8-flash", "gpt-5.6-terra", "gpt-5.6-sol",
                     "gpt-5.5", "gpt-5", "gemini-2.5-flash", "opencode/"],
}

# Palabras clave para clasificar la tarea (español + inglés).
TASK_KEYWORDS = {
    "codigo": [
        "código", "codigo", "code", "script", "python", "javascript", "html", "css", "función",
        "funcion", "bug", "error", "debug", "programa", "api", "clase", "refactor", "repo",
        "compila", "traceback", "sql", "bot", "endpoint", "fastapi", "node",
    ],
    "razonamiento": [
        "analiza", "analiza", "estrategia", "por qué", "porque", "razona", "compara", "decide",
        "plan", "matemática", "matematica", "calcula", "demuestra", "evalúa", "evalua",
        "arquitectura", "optimiza", "riesgo",
    ],
    "traduccion": ["traduce", "traducir", "translate", "traducción", "traduccion", "en inglés", "en árabe", "en francés"],
    "creativo": ["escribe", "redacta", "historia", "poema", "email", "correo", "post", "slogan", "guion", "marketing"],
    "vision": ["imagen", "foto", "captura", "screenshot", "image", "picture"],
    "rapido": ["rápido", "rapido", "resume", "resumen", "breve", "corto", "sí o no", "quick"],
}

# ─────────────────────────── Estado interno ───────────────────────────

_server_proc = None
_session_id = None
_models_cache = None
_models_info = {}   # "proveedor/modelo" -> datos del modelo (coste, nombre…)


def _auth():
    return (USERNAME, PASSWORD) if PASSWORD else None


def _load_preferences():
    prefs = dict(DEFAULT_PREFERENCES)
    if CONFIG_FILE.exists():
        try:
            prefs.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("preferences", {}))
        except Exception as e:
            print(f"[OpenCode] No se pudo leer {CONFIG_FILE.name}: {e}")
    return prefs


# ─────────────────────────── Servidor ───────────────────────────

def _opencode_bin():
    return shutil.which("opencode") or shutil.which("opencode.cmd") or shutil.which("opencode.exe")


def server_alive() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/config", auth=_auth(), timeout=3)
        return r.status_code < 500
    except requests.RequestException:
        return False


def ensure_server() -> bool:
    """Arranca `opencode serve` si no está corriendo. Devuelve True si queda disponible."""
    global _server_proc
    if server_alive():
        return True

    binary = _opencode_bin()
    if not binary:
        print("[OpenCode] No encuentro 'opencode' en el PATH. Instálalo con: npm i -g opencode-ai")
        return False

    Path(WORKDIR).mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    _server_proc = subprocess.Popen(
        [binary, "serve", "--port", str(PORT), "--hostname", HOST],
        cwd=WORKDIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )

    for _ in range(40):  # hasta ~20 s
        if server_alive():
            print(f"[OpenCode] Servidor listo en {BASE_URL}")
            return True
        time.sleep(0.5)

    print("[OpenCode] El servidor no respondió a tiempo.")
    return False


def stop_server():
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        _server_proc.terminate()
    _server_proc = None


# ─────────────────────────── Modelos y router ───────────────────────────

def available_models(refresh: bool = False) -> list[str]:
    """Devuelve la lista de modelos disponibles como 'proveedor/modelo'."""
    global _models_cache
    if _models_cache is not None and not refresh:
        return _models_cache

    models = []
    try:
        r = requests.get(f"{BASE_URL}/config/providers", auth=_auth(), timeout=10)
        r.raise_for_status()
        data = r.json()
        for prov in data.get("providers", []):
            pid = prov.get("id")
            for mid, info in (prov.get("models") or {}).items():
                models.append(f"{pid}/{mid}")
                _models_info[f"{pid}/{mid}"] = info if isinstance(info, dict) else {}
    except Exception as e:
        print(f"[OpenCode] No pude listar modelos: {e}")

    _models_cache = models
    return models


def classify_task(prompt: str) -> str:
    text = prompt.lower()
    scores = {task: sum(1 for kw in kws if kw in text) for task, kws in TASK_KEYWORDS.items()}
    best, score = max(scores.items(), key=lambda kv: kv[1])
    if score == 0:
        return "rapido" if len(prompt) < 80 else "general"
    return best


# Modelos que NO sirven para chat de texto (voz, imagen, embeddings, tiempo real…)
EXCLUDE = ["tts", "audio", "speech", "transcribe", "whisper", "embedding", "embed",
           "image", "imagen", "dall-e", "realtime", "live", "moderation", "search-preview",
           "veo", "lyria", "sora", "computer-use", "deep-research", "translate"]

# Sufijos que penalizan (se prefieren versiones estables y completas)
PENALTY = {"preview": 3, "exp": 3, "experimental": 3, "beta": 3, "alpha": 4,
           "fast": 2, "lite": 2, "nano": 3, "mini": 2, "turbo": 1}


def _load_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_status(status: dict):
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[OpenCode] No pude guardar {STATUS_FILE}: {e}")


def mark_model(model: str, ok: bool, error: str = ""):
    status = _load_status()
    status[model] = {"ok": ok, "error": error[:300], "checked": time.strftime("%Y-%m-%d %H:%M")}
    _save_status(status)


def _is_permanent(error: str) -> bool:
    e = error.lower()
    return any(x in e for x in PERMANENT_ERRORS)


def _is_chat_model(model_id: str) -> bool:
    mid = model_id.lower()
    return not any(x in mid for x in EXCLUDE)


def _version_key(model_id: str) -> tuple:
    nums = re.findall(r"\d+(?:\.\d+)?", model_id.split("/", 1)[-1])
    return tuple(float(n) for n in nums[:3])


def _score(model_id: str, task: str) -> tuple:
    mid = model_id.split("/", 1)[-1].lower()
    penalty = sum(p for k, p in PENALTY.items() if re.search(rf"(^|[-_.]){k}($|[-_.])", mid))
    if task in ("rapido", "traduccion"):
        # Para tareas rápidas, 'mini/flash/fast' es bueno, no malo
        penalty -= sum(PENALTY[k] for k in ("fast", "mini", "lite") if k in mid)
    # Menos penalización > versión más alta > nombre más corto (modelo base)
    return (-penalty, _version_key(model_id), -len(mid))


def candidate_models(task: str, models: list[str] | None = None) -> list[str]:
    """Lista ordenada de modelos candidatos para la tarea (el mejor primero)."""
    models = models if models is not None else available_models()
    status = _load_status()
    models = [m for m in models if _is_chat_model(m) and status.get(m, {}).get("ok", True)]
    prefs = _load_preferences()
    out = []
    for pattern in prefs.get(task, []) + prefs.get("general", []):
        matches = [m for m in models if pattern in m.lower()]
        if matches:
            best = max(matches, key=lambda m: _score(m, task))
            if best not in out:
                out.append(best)
    # Reserva final: cualquier modelo verificado que funcione, del mejor al peor
    verified = [m for m in models if status.get(m, {}).get("ok")]
    for m in sorted(verified, key=lambda m: _score(m, task), reverse=True):
        if m not in out:
            out.append(m)
    return out


def choose_model(task: str, models: list[str] | None = None) -> str | None:
    """Elige el mejor modelo disponible siguiendo la lista de preferencias de la tarea."""
    c = candidate_models(task, models)
    return c[0] if c else None


# ─────────────────────────── Envío de prompts ───────────────────────────

def _new_session(title: str = "ZAR") -> str:
    r = requests.post(f"{BASE_URL}/session", json={"title": title}, auth=_auth(), timeout=15)
    r.raise_for_status()
    return r.json()["id"]


class OpenCodeError(Exception):
    pass


def _extract_text(payload) -> str:
    if not isinstance(payload, dict):
        raise OpenCodeError(f"Respuesta inesperada: {str(payload)[:300]}")
    parts = payload.get("parts", []) or []
    texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    text = "\n".join(t for t in texts if t).strip()
    if text:
        return text
    err = (payload.get("info") or {}).get("error")
    if err:
        data = err.get("data") if isinstance(err, dict) else None
        msg = (data or {}).get("message") if isinstance(data, dict) else None
        raise OpenCodeError(f"{(err.get('name') if isinstance(err, dict) else '')}: {msg or err}")
    if DEBUG:
        print("[OpenCode][debug] respuesta sin texto:", json.dumps(payload, ensure_ascii=False)[:2000])
    raise OpenCodeError("el modelo no devolvió texto")


def model_info(model: str) -> dict:
    if not _models_info:
        available_models(refresh=True)
    return _models_info.get(model, {})


def ask_server(prompt: str, model: str | None, new_session: bool = False,
               system: str | None = None, session_id: str | None = None) -> str:
    """Envía un prompt. Si pasas session_id usa esa sesión (p. ej. la de un agente)."""
    global _session_id
    if session_id:
        return _send(session_id, prompt, model, system)
    if new_session or not _session_id:
        _session_id = _new_session()
    try:
        return _send(_session_id, prompt, model, system)
    except SessionGone:
        _session_id = _new_session()
        return _send(_session_id, prompt, model, system)


class SessionGone(Exception):
    pass


def _send(sid: str, prompt: str, model: str | None, system: str | None) -> str:
    body = {"parts": [{"type": "text", "text": prompt}]}
    if system:
        body["system"] = system
    if model and "/" in model:
        provider_id, model_id = model.split("/", 1)
        body["model"] = {"providerID": provider_id, "modelID": model_id}

    r = requests.post(f"{BASE_URL}/session/{sid}/message", json=body, auth=_auth(), timeout=TIMEOUT)
    if r.status_code == 404:  # sesión caducada
        raise SessionGone(sid)
    if r.status_code >= 400:
        raise OpenCodeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return _extract_text(r.json())


def ask_cli(prompt: str, model: str | None) -> str:
    """Respaldo: `opencode run` sin servidor."""
    binary = _opencode_bin()
    if not binary:
        return "OpenCode no está instalado."
    cmd = [binary, "run"]
    if model:
        cmd += ["-m", model]
    cmd.append(prompt)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    res = subprocess.run(
        cmd, cwd=WORKDIR, capture_output=True, text=True, encoding="utf-8",
        timeout=TIMEOUT, creationflags=flags,
    )
    out = re.sub(r"\x1b\[[0-9;]*m", "", res.stdout).strip()  # quitar colores ANSI
    return out or res.stderr.strip() or "(sin respuesta)"


def ask(prompt: str, task: str | None = None, model: str | None = None,
        new_session: bool = False, max_tries: int = 4) -> dict:
    """Punto de entrada principal. Devuelve {'text', 'model', 'task'}.
    Si un modelo falla (sin acceso, error, respuesta vacía) prueba el siguiente."""
    task = task or classify_task(prompt)

    if ensure_server():
        candidates = [model] if model else candidate_models(task)
        candidates = candidates[:max_tries] or [None]
        errors = []
        for i, m in enumerate(candidates):
            try:
                text = ask_server(prompt, m, new_session=new_session or i > 0)
                if m:
                    mark_model(m, True)
                return {"text": text, "model": m or "default", "task": task, "errors": errors}
            except Exception as e:
                errors.append(f"{m}: {e}")
                print(f"[OpenCode] {m} falló → {e}")
                if m and _is_permanent(str(e)):
                    mark_model(m, False, str(e))  # no lo volverá a intentar
        return {"text": "Ningún modelo respondió:\n" + "\n".join(errors),
                "model": "ninguno", "task": task, "errors": errors}

    return {"text": ask_cli(prompt, model), "model": model or "default", "task": task, "errors": []}


def probe_models(workers: int = 6) -> dict:
    """Prueba cada modelo de chat con un mensaje mínimo y guarda cuáles funcionan."""
    from concurrent.futures import ThreadPoolExecutor

    models = [m for m in available_models(refresh=True) if _is_chat_model(m)]
    print(f"Probando {len(models)} modelos (puede tardar 1-2 minutos)…")

    def test(m):
        try:
            sid = _new_session(f"probe {m}")
            pid, mid = m.split("/", 1)
            body = {"parts": [{"type": "text", "text": "Responde solo con: OK"}],
                    "model": {"providerID": pid, "modelID": mid}}
            r = requests.post(f"{BASE_URL}/session/{sid}/message", json=body, auth=_auth(), timeout=120)
            r.raise_for_status()
            _extract_text(r.json())
            try:
                requests.delete(f"{BASE_URL}/session/{sid}", auth=_auth(), timeout=10)
            except Exception:
                pass
            return m, True, ""
        except Exception as e:
            return m, False, str(e)

    status = _load_status()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for m, ok, err in ex.map(test, models):
            status[m] = {"ok": ok, "error": err[:300], "checked": time.strftime("%Y-%m-%d %H:%M")}
            print(("  OK   " if ok else "  x    ") + m + ("" if ok else f"  ← {err[:110]}"))
    _save_status(status)
    good = [m for m, v in status.items() if v.get("ok")]
    print(f"\n{len(good)} modelos funcionan. Guardado en {STATUS_FILE}")
    return status


# ─────────────────────────── Integración con ZAR ───────────────────────────

# Declaración para function-calling de Gemini (añádela a la lista de tools de ZAR).
TOOL_DECLARATION = {
    "name": "opencode",
    "description": (
        "Delegate a task to OpenCode, which can use many AI models (Claude, GPT, Gemini, DeepSeek, etc.) "
        "and automatically picks the best one. Use it for coding, writing or editing files in a project, "
        "deep analysis, or when the user asks for a specific model."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "The full task or question to send."},
            "task": {
                "type": "STRING",
                "description": "Optional task type: codigo, razonamiento, rapido, creativo, traduccion, vision, general.",
            },
            "model": {
                "type": "STRING",
                "description": "Optional explicit model as 'provider/model', e.g. 'anthropic/claude-sonnet-5'.",
            },
            "new_session": {"type": "BOOLEAN", "description": "Start a fresh conversation in OpenCode."},
        },
        "required": ["prompt"],
    },
}


def opencode_action(parameters: dict, player=None, **kwargs) -> str:
    """Función que ZAR llama cuando Gemini invoca la herramienta 'opencode'."""
    prompt = (parameters or {}).get("prompt", "").strip()
    if not prompt:
        return "No me has dicho qué quieres que haga OpenCode."

    result = ask(
        prompt,
        task=parameters.get("task"),
        model=parameters.get("model"),
        new_session=bool(parameters.get("new_session", False)),
    )
    msg = f"[{result['task']} → {result['model']}]\n{result['text']}"

    if player is not None and hasattr(player, "write_log"):
        try:
            player.write_log(f"OpenCode ({result['model']}): {result['text'][:300]}")
        except Exception:
            pass
    return msg


# ─────────────────────────── Prueba rápida ───────────────────────────

if __name__ == "__main__":
    import sys
    print("Comprobando OpenCode…")
    if "--reset" in sys.argv and STATUS_FILE.exists():
        STATUS_FILE.unlink()
        print("Estado de modelos borrado.")
    if ensure_server() and "--probe" in sys.argv:
        probe_models()
        print()
        for t in DEFAULT_PREFERENCES:
            c = candidate_models(t)
            print(f"  {t:13s} → {c[0] if c else 'ninguno'}   (reserva: {', '.join(c[1:3]) or '-'})")
        sys.exit(0)
    if server_alive() and "--list" in sys.argv:
        for m in sorted(available_models()):
            print(("   " if _is_chat_model(m) else " x ") + m)
        sys.exit(0)
    if server_alive():
        ms = available_models()
        print(f"{len(ms)} modelos disponibles.")
        for t in DEFAULT_PREFERENCES:
            c = candidate_models(t, ms)
            print(f"  {t:13s} → {c[0] if c else 'default'}   (reserva: {', '.join(c[1:3]) or '-'})")
        print()
        r = ask("Escribe una función en Python que invierta una cadena.")
        print(f"[{r['task']} → {r['model']}]\n{r['text']}")
    else:
        print("No se pudo arrancar OpenCode.")
