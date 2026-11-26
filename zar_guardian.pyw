"""
alberto_guardian.pyw — El "ángel de la guarda" de ZAR

Arranca ZAR y, si se cae por un fallo, lo vuelve a levantar solo en unos segundos.
  · Si lo cierras tú (menú del icono → Salir, o "cierra ZAR del todo") NO lo reinicia.
  · Si se queda colgado (la ventana deja de responder más de 3 minutos), lo reinicia.
  · Si se cae muchas veces seguidas, espera un poco antes de reintentar.
Lo apunta todo en logs\\guardian.log.

Lo lanza la tarea programada "ZAR" al iniciar sesión (instalar_arranque.bat).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
LOGS = APP / "logs"
HEARTBEAT = LOGS / "heartbeat"
HANG_AFTER_S = 180            # sin latido este tiempo = colgado
MAX_FAST_CRASHES = 5          # caídas en 10 minutos antes de esperar más
BACKOFF = [3, 5, 10, 30, 60]

LOGS.mkdir(exist_ok=True)


def log(msg: str):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    try:
        p = LOGS / "guardian.log"
        if p.exists() and p.stat().st_size > 1024 * 1024:
            p.write_text("", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def single_instance() -> bool:
    if os.name != "nt":
        return True
    import ctypes
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ZAR_guardian")
    return ctypes.windll.kernel32.GetLastError() != 183


def python_exe() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        exe = exe.parent / "pythonw.exe"          # sin ventana negra
    return str(exe)


def describe(code: int) -> str:
    known = {3: "error interno de la interfaz", 0xC0000005: "fallo de memoria (acceso no válido)",
             0xC0000409: "fallo interno (desbordamiento)", 0xC00000FD: "desbordamiento de pila",
             -1: "colgado sin responder", 1: "error de Python"}
    c = code & 0xFFFFFFFF if code < 0 and code != -1 else code
    return known.get(c, f"código {hex(c) if c > 255 else c}")


def heartbeat_age() -> float | None:
    try:
        return time.time() - HEARTBEAT.stat().st_mtime
    except Exception:
        return None


def run_once(env: dict) -> int:
    try:
        HEARTBEAT.unlink()
    except Exception:
        pass
    flags = 0x08000000 if os.name == "nt" else 0           # CREATE_NO_WINDOW
    proc = subprocess.Popen([python_exe(), str(APP / "main.py")], cwd=str(APP), env=env, creationflags=flags)
    log(f"ZAR arrancado (pid {proc.pid})")
    started = time.time()
    last_tick = time.time()
    while True:
        try:
            return proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
        now = time.time()
        slept = now - last_tick > 60          # el PC estuvo suspendido: no confundir con cuelgue
        last_tick = now
        if slept:
            try:
                os.utime(HEARTBEAT, None)
            except Exception:
                pass
            continue
        age = heartbeat_age()
        if age is None:
            if now - started > HANG_AFTER_S + 120:            # nunca llegó a dar señales de vida
                log("No da señales de vida desde que arrancó: lo reinicio")
                proc.kill()
                return -1
            continue
        if age > HANG_AFTER_S:
            log(f"Colgado ({int(age)} s sin responder): lo reinicio")
            proc.kill()
            return -1


def main():
    if not single_instance():
        return
    log("Guardián en marcha")
    crashes: list[float] = []
    reason = ""
    while True:
        env = dict(os.environ)
        env.pop("ALBERTO_RESTART_REASON", None)
        if reason:
            env["ALBERTO_RESTART_REASON"] = reason
        code = run_once(env)
        if code == 75:           # ZAR updated itself: start the new version right away
            log("ZAR actualizado: arranco la versión nueva")
            reason = ""
            time.sleep(1)
            continue
        stop = LOGS / "stop.flag"
        if code == 0 and (os.name == "nt" or stop.exists()):
            stop.unlink(missing_ok=True)
            log("ZAR cerrado a propósito. Fin del guardián.")
            return
        if code == 0:            # Mac: closed from the Dock / Cmd+Q -> it's a background service, start it again
            log("ZAR se ha cerrado: lo vuelvo a abrir en segundo plano")
            time.sleep(2)
            reason = ""
            continue
        reason = describe(code)
        now = time.time()
        crashes = [t for t in crashes if now - t < 600] + [now]
        wait = BACKOFF[min(len(crashes) - 1, len(BACKOFF) - 1)]
        if len(crashes) > MAX_FAST_CRASHES:
            wait = 300
            log(f"Demasiadas caídas seguidas: espero {wait} s antes de reintentar")
        log(f"ZAR se ha caído ({reason}). Reinicio en {wait} s. Mira logs\\alberto.log para el detalle.")
        time.sleep(wait)


if __name__ == "__main__":
    main()
