"""
security_audit.py — Auditor de seguridad personal y panel de ZAR

Revisa la seguridad de TU propio ordenador (solo lee la configuración, no cambia nada)
y te dice qué está bien, qué conviene mejorar y cómo hacerlo.

  · "ZAR, haz una auditoría de seguridad"  → nota del 0 al 100 + informe en PDF.
  · "¿Cómo está mi seguridad?"                 → resumen hablado rápido.
  · "Panel de seguridad"                       → vigilancia + wifi + accesos fallidos + nota.

Comprueba: cortafuegos, antivirus (Defender), actualizaciones, cifrado (BitLocker), UAC,
inicio de sesión automático, Escritorio remoto (RDP), SMBv1, cuenta de invitado,
administradores locales, bloqueo de pantalla y política de contraseñas.

NO es un escáner de redes ni una herramienta de ataque: mira tu equipo, con tu permiso.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from actions import documents

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

OK, WARN, BAD, UNK = "ok", "warn", "bad", "unknown"
ICON = {OK: "✅", WARN: "⚠️", BAD: "❌", UNK: "❔"}
WEIGHT = {OK: 0, WARN: 1, BAD: 2, UNK: 0}


def _ps(cmd: str, timeout=25) -> str:
    if os.name != "nt":
        return ""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, errors="ignore", timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _reg(path: str, name: str) -> str | None:
    out = _ps(f"(Get-ItemProperty -Path '{path}' -Name '{name}' -ErrorAction SilentlyContinue).'{name}'")
    return out.strip() if out.strip() != "" else None


class Check:
    __slots__ = ("name", "status", "detail", "fix")

    def __init__(self, name, status, detail, fix=""):
        self.name, self.status, self.detail, self.fix = name, status, detail, fix


# ═══════════════════════════ COMPROBACIONES ═══════════════════════════
def check_firewall() -> Check:
    out = _ps("Get-NetFirewallProfile | ForEach-Object { \"$($_.Name)=$($_.Enabled)\" }")
    if not out:
        return Check("Cortafuegos de Windows", UNK, "No pude comprobarlo.")
    off = [l.split("=")[0] for l in out.splitlines() if "false" in l.lower()]
    if off:
        return Check("Cortafuegos de Windows", BAD, f"Apagado en: {', '.join(off)}.",
                     "Actívalo en Seguridad de Windows → Firewall y protección de red.")
    return Check("Cortafuegos de Windows", OK, "Activado en todos los perfiles.")


def check_defender() -> Check:
    out = _ps("$s=Get-MpComputerStatus -ErrorAction SilentlyContinue; "
              "\"$($s.AntivirusEnabled)|$($s.RealTimeProtectionEnabled)|$($s.AntivirusSignatureAge)\"")
    if not out or "|" not in out:
        return Check("Antivirus (Defender)", UNK, "No pude comprobarlo (¿usas otro antivirus?).")
    av, rt, age = (out.split("|") + ["", "", ""])[:3]
    if av.lower() != "true":
        return Check("Antivirus (Defender)", WARN, "Defender no está activo. Si usas otro antivirus, ignóralo.",
                     "Revisa que tu antivirus esté encendido y actualizado.")
    try:
        old = int(age) > 3
    except Exception:
        old = False
    if rt.lower() != "true":
        return Check("Antivirus (Defender)", BAD, "La protección en tiempo real está apagada.",
                     "Actívala en Seguridad de Windows → Protección antivirus y contra amenazas.")
    if old:
        return Check("Antivirus (Defender)", WARN, f"Las firmas tienen {age} días.",
                     "Actualiza las definiciones del antivirus.")
    return Check("Antivirus (Defender)", OK, "Activo, en tiempo real y al día.")


def check_updates() -> Check:
    out = _ps("(New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()."
              "Search('IsInstalled=0 and Type=''Software''').Updates.Count", timeout=45)
    m = re.search(r"\d+", out)
    if not m:
        return Check("Actualizaciones de Windows", UNK, "No pude comprobarlo.")
    n = int(m.group())
    if n == 0:
        return Check("Actualizaciones de Windows", OK, "Windows está al día.")
    if n <= 5:
        return Check("Actualizaciones de Windows", WARN, f"{n} actualizaciones pendientes.",
                     "Instálalas en Windows Update.")
    return Check("Actualizaciones de Windows", BAD, f"{n} actualizaciones pendientes.",
                 "Hay muchas pendientes: ve a Windows Update e instálalas.")


def check_bitlocker() -> Check:
    out = _ps("(Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction SilentlyContinue).ProtectionStatus")
    if out == "":
        return Check("Cifrado del disco (BitLocker)", UNK, "No disponible (quizá tu edición de Windows no lo trae).")
    if "on" in out.lower() or out.strip() == "1":
        return Check("Cifrado del disco (BitLocker)", OK, "El disco del sistema está cifrado.")
    return Check("Cifrado del disco (BitLocker)", WARN,
                 "El disco no está cifrado: si te roban el portátil, podrían leer tus archivos.",
                 "Activa BitLocker (o el cifrado de dispositivo) en Configuración → Privacidad y seguridad.")


def check_uac() -> Check:
    v = _reg("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "EnableLUA")
    if v is None:
        return Check("Control de cuentas (UAC)", UNK, "No pude comprobarlo.")
    if v.strip() == "1":
        return Check("Control de cuentas (UAC)", OK, "Activado (te pide permiso para cambios importantes).")
    return Check("Control de cuentas (UAC)", BAD, "UAC desactivado: los programas pueden hacer cambios sin avisar.",
                 "Actívalo: busca «Control de cuentas de usuario» y súbelo al menos al tercer nivel.")


def check_autologin() -> Check:
    v = _reg("HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon", "AutoAdminLogon")
    if v and v.strip() == "1":
        return Check("Inicio de sesión automático", BAD,
                     "El PC entra solo sin pedir contraseña: cualquiera que lo encienda está dentro.",
                     "Desactívalo (netplwiz → marca «Los usuarios deben escribir su contraseña»).")
    return Check("Inicio de sesión automático", OK, "Pide iniciar sesión al arrancar.")


def check_rdp() -> Check:
    v = _reg("HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server", "fDenyTSConnections")
    if v is None:
        return Check("Escritorio remoto (RDP)", UNK, "No pude comprobarlo.")
    if v.strip() == "0":
        nla = _reg("HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server\\WinStations\\RDP-Tcp",
                   "UserAuthentication")
        extra = "" if nla == "1" else " Además, la autenticación a nivel de red (NLA) no está forzada."
        return Check("Escritorio remoto (RDP)", WARN,
                     "El Escritorio remoto está activado." + extra,
                     "Si no lo usas, apágalo. Si lo usas, no lo expongas a internet y usa una contraseña fuerte y NLA.")
    return Check("Escritorio remoto (RDP)", OK, "Desactivado.")


def check_smb1() -> Check:
    out = _ps("(Get-SmbServerConfiguration -ErrorAction SilentlyContinue).EnableSMB1Protocol")
    if out == "":
        return Check("Protocolo antiguo SMBv1", UNK, "No pude comprobarlo.")
    if "true" in out.lower():
        return Check("Protocolo antiguo SMBv1", BAD,
                     "SMBv1 activo: es muy inseguro (lo usó el ataque WannaCry).",
                     "Desactívalo: Características de Windows → desmarca «Compatibilidad con SMB 1.0».")
    return Check("Protocolo antiguo SMBv1", OK, "Desactivado (bien).")


def check_guest() -> Check:
    out = _ps("(Get-LocalUser -Name 'Guest' -ErrorAction SilentlyContinue).Enabled")
    if out == "":
        out = _ps("(Get-LocalUser -Name 'Invitado' -ErrorAction SilentlyContinue).Enabled")
    if out == "":
        return Check("Cuenta de invitado", UNK, "No pude comprobarlo.")
    if "true" in out.lower():
        return Check("Cuenta de invitado", WARN, "La cuenta de invitado está activa.",
                     "Desactívala si no la necesitas.")
    return Check("Cuenta de invitado", OK, "Desactivada.")


def check_admins() -> Check:
    out = _ps("Get-LocalGroupMember -Group 'Administrators' -ErrorAction SilentlyContinue | "
              "Select-Object -ExpandProperty Name")
    if not out:
        out = _ps("Get-LocalGroupMember -Group 'Administradores' -ErrorAction SilentlyContinue | "
                  "Select-Object -ExpandProperty Name")
    admins = [l.strip() for l in out.splitlines() if l.strip()]
    if not admins:
        return Check("Administradores del equipo", UNK, "No pude comprobarlo.")
    n = len(admins)
    names = ", ".join(a.split("\\")[-1] for a in admins)
    if n > 3:
        return Check("Administradores del equipo", WARN, f"{n} cuentas con permisos de administrador: {names}.",
                     "Deja como administrador solo las cuentas que de verdad lo necesiten.")
    return Check("Administradores del equipo", OK, f"{n} administrador(es): {names}.")


def check_lock() -> Check:
    secure = _reg("HKCU:\\Control Panel\\Desktop", "ScreenSaverIsSecure")
    timeout = _reg("HKCU:\\Control Panel\\Desktop", "ScreenSaveTimeOut")
    try:
        mins = int(timeout) // 60 if timeout else 0
    except Exception:
        mins = 0
    if secure == "1" and 0 < mins <= 15:
        return Check("Bloqueo de pantalla", OK, f"Se bloquea solo tras {mins} min de inactividad.")
    return Check("Bloqueo de pantalla", WARN,
                 "No se bloquea solo (o tarda demasiado) al dejar el PC.",
                 "Pon un salvapantallas con «Mostrar la pantalla de inicio de sesión» a los 10 min, "
                 "o usa Win+L al levantarte. ZAR también puede bloquearlo por ausencia.")


def check_password_policy() -> Check:
    out = _ps("net accounts")
    if not out:
        return Check("Política de contraseñas", UNK, "No pude comprobarlo.")
    m = re.search(r"(Minimum password length|Longitud mínima de la contraseña)\D+(\d+)", out)
    length = int(m.group(2)) if m else 0
    if length >= 8:
        return Check("Política de contraseñas", OK, f"Longitud mínima de contraseña: {length}.")
    if length == 0:
        return Check("Política de contraseñas", WARN, "No hay longitud mínima de contraseña definida.",
                     "Usa una contraseña de al menos 12 caracteres para tu cuenta.")
    return Check("Política de contraseñas", WARN, f"Longitud mínima baja ({length}).",
                 "Sube la longitud mínima o usa una contraseña larga.")


ALL_CHECKS = [check_firewall, check_defender, check_updates, check_bitlocker, check_uac,
              check_autologin, check_rdp, check_smb1, check_guest, check_admins, check_lock,
              check_password_policy]


def run_all() -> list[Check]:
    results = []
    for fn in ALL_CHECKS:
        try:
            results.append(fn())
        except Exception as e:
            results.append(Check(fn.__name__, UNK, f"No se pudo comprobar ({e})."))
    return results


def score(checks: list[Check]) -> int:
    graded = [c for c in checks if c.status != UNK]
    if not graded:
        return 0
    penalty = sum(WEIGHT[c.status] for c in graded)
    worst = 2 * len(graded)
    return round(100 * (1 - penalty / worst))


# ═══════════════════════════ SALIDAS ═══════════════════════════
def quick_summary() -> str:
    if os.name != "nt":
        return "La auditoría de seguridad solo funciona en Windows."
    checks = run_all()
    sc = score(checks)
    bad = [c for c in checks if c.status == BAD]
    warn = [c for c in checks if c.status == WARN]
    verdict = ("muy buena" if sc >= 90 else "buena" if sc >= 75 else "mejorable" if sc >= 50 else "floja")
    txt = f"Seguridad {sc} de 100 ({verdict})."
    if bad:
        txt += " Importante: " + "; ".join(c.name.lower() + " — " + c.detail for c in bad[:3])
    elif warn:
        txt += " A mejorar: " + ", ".join(c.name.lower() for c in warn[:4]) + "."
    else:
        txt += " Todo en orden."
    return txt


def audit_report(fmt: str = "pdf") -> tuple[Path, dict, int]:
    checks = run_all()
    sc = score(checks)
    def block(status):
        return [f"{ICON[c.status]} {c.name}: {c.detail}" + (f" → {c.fix}" if c.fix else "")
                for c in checks if c.status == status]
    sections = []
    for status, title in ((BAD, "Problemas que arreglar ya"), (WARN, "Mejoras recomendadas"),
                          (OK, "Lo que está bien"), (UNK, "No se pudo comprobar")):
        items = block(status)
        if items:
            sections.append({"heading": title, "paragraphs": [], "bullets": items})
    verdict = ("Muy buena" if sc >= 90 else "Buena" if sc >= 75 else "Mejorable" if sc >= 50 else "Floja")
    doc = {
        "title": f"Auditoría de seguridad — {sc}/100 ({verdict})",
        "subtitle": f"ZAR · {datetime.now():%d/%m/%Y %H:%M} · solo lectura, no se ha cambiado nada",
        "spoken": quick_summary(),
        "sections": sections or [{"heading": "Resultado", "paragraphs": ["No se pudo comprobar nada."], "bullets": []}],
    }
    path = documents.save_document(doc, fmt, name="Auditoria de seguridad")
    return path, doc, sc


# ═══════════════════════════ PANEL ═══════════════════════════
def panel() -> str:
    lines = ["PANEL DE SEGURIDAD DE ZAR", ""]
    try:
        from actions import security
        lines.append(f"Vigilancia: {'ARMADA' if security.guard.armed else 'apagada'}.")
        lines.append(f"Bloqueo por ausencia: {'activo' if security.presence.active else 'apagado'}.")
        lines.append(security.network.describe())
    except Exception as e:
        lines.append(f"(estado de vigilancia no disponible: {e})")
    try:
        from actions import it_helper
        fl = it_helper.failed_logons(48)
        lines.append(f"Inicios de sesión fallidos (48 h): {len(fl)}." if fl is not None else "")
    except Exception:
        pass
    try:
        lines.append(quick_summary())
    except Exception:
        pass
    return "\n".join(l for l in lines if l)


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def security_audit_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "summary").lower()
    if os.name != "nt" and action in ("audit", "report", "summary"):
        return "La auditoría de seguridad solo funciona en Windows."

    if action in ("summary", "status", "quick"):
        return quick_summary()

    if action == "panel":
        return panel()

    if action in ("audit", "report", "full"):
        try:
            path, doc, sc = audit_report(p.get("format", "pdf"))
        except Exception as e:
            return f"No pude completar la auditoría: {e}"
        documents.open_file(path)
        return f"Auditoría terminada: {sc}/100. {doc.get('spoken', '')} Informe abierto: {path.name}."

    return "Acciones: summary, panel, audit."
