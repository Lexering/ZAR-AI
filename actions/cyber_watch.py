"""
cyber_watch.py — Centinela de ZAR (vigilancia defensiva en vivo)

Vigila tu propio equipo en segundo plano y te avisa (voz + Telegram) si pasa algo raro:

  · Defensas bajadas:   alguien apaga el antivirus (Defender) o el cortafuegos.
  · Persistencia:       un programa nuevo se añade al arranque de Windows (truco de virus).
  · USB:                se conecta un pendrive o disco nuevo.
  · Fuerza bruta:       muchos intentos de inicio de sesión fallidos seguidos.

Solo observa y avisa. No bloquea nada por su cuenta (salvo que tú actives el modo pánico).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

CHECK_EVERY_S     = 45
LOGON_SPIKE       = 5           # intentos fallidos nuevos en una vuelta → aviso
CREATE_NO_WINDOW  = 0x08000000 if os.name == "nt" else 0


def _base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STATE = _base_dir() / "config" / "cyber_state.json"


def _ps(cmd: str, timeout=20) -> str:
    if os.name != "nt":
        return ""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, errors="ignore", timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(d: dict):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ═══════════════════════════ LECTORES (solo Windows) ═══════════════════════════
def defenses() -> dict:
    """Estado de antivirus y cortafuegos."""
    out = _ps("$s=Get-MpComputerStatus -ErrorAction SilentlyContinue; "
              "$fw=(Get-NetFirewallProfile | Where-Object {$_.Enabled -eq $false}).Name -join ','; "
              "\"$($s.RealTimeProtectionEnabled)|$($s.AntivirusEnabled)|$fw\"")
    if "|" not in out:
        return {}
    rt, av, fw_off = (out.split("|") + ["", "", ""])[:3]
    return {"realtime": rt.lower() == "true", "antivirus": av.lower() == "true",
            "fw_off": [x for x in fw_off.split(",") if x]}


def startup_set() -> set[str]:
    out = _ps("Get-CimInstance Win32_StartupCommand | ForEach-Object { \"$($_.Name)=$($_.Command)\" }")
    return {l.strip() for l in out.splitlines() if l.strip()}


def removable_drives() -> set[str]:
    if os.name != "nt":
        return set()
    out = _ps("Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=2' | "
              "ForEach-Object { \"$($_.DeviceID) $($_.VolumeName)\" }")
    return {l.strip() for l in out.splitlines() if l.strip()}


def failed_logon_count(hours: int = 1) -> int:
    out = _ps(f"$t=(Get-Date).AddHours(-{hours}); (Get-WinEvent -FilterHashtable "
              f"@{{LogName='Security';Id=4625;StartTime=$t}} -ErrorAction SilentlyContinue).Count", timeout=25)
    try:
        return int("".join(c for c in out if c.isdigit()) or 0)
    except Exception:
        return 0


# ═══════════════════════════ MONITOR ═══════════════════════════
class CyberMonitor:
    def __init__(self, log=print):
        self.log = log
        st = _load_state()
        self._startup = set(st.get("startup", []))
        self._drives = set()
        self._logons = st.get("logons", 0)
        self._first = True

    def _alert(self, text, key, important=True, cooldown=600):
        from actions import notify
        notify.alert(text, important=important, key=key, cooldown_s=cooldown)
        self.log("CIBER: " + text)

    def check_once(self):
        if os.name != "nt":
            return
        # Defensas
        d = defenses()
        if d:
            if d.get("realtime") is False:
                self._alert("¡Atención! La protección en tiempo real del antivirus está APAGADA.",
                            "av-off", cooldown=900)
            if d.get("fw_off"):
                self._alert(f"El cortafuegos se ha apagado en: {', '.join(d['fw_off'])}.",
                            "fw-off", cooldown=900)
        # Persistencia (arranque)
        cur = startup_set()
        if cur:
            if self._first:
                self._startup = self._startup or cur
            new = cur - self._startup
            for item in list(new)[:5]:
                name = item.split("=")[0]
                self._alert(f"Un programa nuevo se ha añadido al arranque de Windows: {name}. "
                            "Si no has instalado nada, revísalo.", f"startup-{name}", cooldown=86400)
            self._startup = cur
        # USB
        drives = removable_drives()
        if not self._first:
            for dv in drives - self._drives:
                self._alert(f"Se ha conectado un dispositivo USB: {dv}.", f"usb-{dv}",
                            important=False, cooldown=60)
        self._drives = drives
        # Fuerza bruta
        total = failed_logon_count(1)
        if not self._first and total - self._logons >= LOGON_SPIKE:
            self._alert(f"Muchos intentos de inicio de sesión fallidos ({total - self._logons} nuevos). "
                        "Puede que alguien esté probando contraseñas.", "logon-spike", cooldown=1800)
        self._logons = total

        self._first = False
        _save_state({"startup": sorted(self._startup), "logons": self._logons})

    def run_forever(self):
        time.sleep(30)
        while True:
            try:
                self.check_once()
            except Exception as e:
                self.log(f"CIBER: error en la vigilancia ({e})")
            time.sleep(CHECK_EVERY_S)


_monitor: CyberMonitor | None = None


def start(log=print):
    global _monitor
    if _monitor is None and os.name == "nt":
        _monitor = CyberMonitor(log)
        threading.Thread(target=_monitor.run_forever, daemon=True, name="cyber-watch").start()
        log("CIBER: centinela activo (defensas, arranque, USB, accesos)")
    return _monitor


def status() -> str:
    if os.name != "nt":
        return "El centinela de ciberseguridad solo funciona en Windows."
    d = defenses()
    parts = []
    if d:
        parts.append("Antivirus en tiempo real: " + ("activo" if d.get("realtime") else "APAGADO"))
        parts.append("Cortafuegos: " + ("todo activo" if not d.get("fw_off") else "APAGADO en " + ", ".join(d["fw_off"])))
    parts.append(f"Intentos de acceso fallidos (última hora): {failed_logon_count(1)}")
    drv = removable_drives()
    if drv:
        parts.append("USB conectados: " + ", ".join(drv))
    return "Centinela: " + ". ".join(parts) + "."


def cyber_watch_tool(parameters: dict, player=None, speak=None) -> str:
    return status()
