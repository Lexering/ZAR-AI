"""
it_helper.py — ZAR, tu técnico informático

Diagnostica TU propio ordenador y te ayuda a arreglarlo. Nada de atacar a nadie:
solo mira tu equipo, con tu permiso.

  · "ZAR, ¿por qué va lento el ordenador?"  → qué está comiendo CPU/RAM/disco y por qué.
  · "Revisa los errores de Windows"             → lee el registro de sucesos y te lo explica.
  · "Diagnóstico completo"                      → informe en PDF: hardware, servicios, arranque,
                                                   actualizaciones, errores recientes y consejos.
  · "¿Qué es este error: ...?"                  → te lo traduce y te dice cómo resolverlo.
  · Preguntas de Linux, comandos, redes propias → te responde como un técnico.

Las acciones solo LEEN información. No cambia nada del sistema sin que tú lo pidas y confirmes.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from actions import documents

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _run(cmd, timeout=25, shell=False) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore",
                           timeout=timeout, shell=shell, creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    except Exception as e:
        return f"(no se pudo ejecutar: {e})"


def _ps(cmd: str, timeout=30) -> str:
    """Ejecuta un comando de PowerShell (solo lectura) y devuelve su salida."""
    if os.name != "nt":
        return ""
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout=timeout)


# ═══════════════════════════ HARDWARE Y RECURSOS ═══════════════════════════
def _bytes(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.0f} PB"


def hardware() -> list[str]:
    out = [f"Equipo: {platform.node()} — {platform.system()} {platform.release()}"]
    try:
        import psutil
        out.append(f"CPU: {psutil.cpu_count(logical=False)} núcleos "
                   f"({psutil.cpu_count()} hilos), uso ahora {psutil.cpu_percent(interval=0.5):.0f} %")
        vm = psutil.virtual_memory()
        out.append(f"RAM: {_bytes(vm.total)} total, {_bytes(vm.available)} libres ({vm.percent:.0f} % en uso)")
        for p in psutil.disk_partitions():
            if "cdrom" in p.opts or not p.fstype:
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
                out.append(f"Disco {p.device}: {_bytes(u.free)} libres de {_bytes(u.total)} ({u.percent:.0f} % lleno)")
            except Exception:
                pass
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if bat:
            out.append(f"Batería: {bat.percent:.0f} %" + (" (enchufado)" if bat.power_plugged else ""))
    except ImportError:
        out.append("(instala psutil para el detalle de recursos: pip install psutil)")
    return out


def top_processes(n: int = 8) -> list[str]:
    try:
        import psutil
    except ImportError:
        return []
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(0.6)
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            cpu = p.cpu_percent(None)
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            procs.append((cpu, mem, p.info["name"] or "?"))
        except Exception:
            pass
    heavy_cpu = sorted(procs, reverse=True)[:n]
    heavy_mem = sorted(procs, key=lambda t: t[1], reverse=True)[:n]
    lines = ["Lo que más CPU consume: " + ", ".join(f"{nm} ({c:.0f} %)" for c, m, nm in heavy_cpu if c > 0.5)]
    lines.append("Lo que más memoria consume: " + ", ".join(f"{nm} ({_bytes(m)})" for c, m, nm in heavy_mem))
    return lines


# ═══════════════════════════ REGISTRO DE SUCESOS (WINDOWS) ═══════════════════════════
def event_errors(hours: int = 24, maximum: int = 40) -> list[str]:
    """Errores y avisos del sistema y de las aplicaciones en las últimas horas."""
    if os.name != "nt":
        # Linux: journalctl si está
        if shutil.which("journalctl"):
            out = _run(["journalctl", "-p", "3", "-x", "--no-pager", "--since", f"-{hours}h"], timeout=20)
            noise = ("no entries", "no journal", "-- journal begins", "-- no entries --")
            return [l.strip() for l in out.splitlines()
                    if l.strip() and not any(x in l.lower() for x in noise)][-maximum:]
        return []
    ps = (f"$t=(Get-Date).AddHours(-{hours}); "
          "Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;StartTime=$t} "
          "-ErrorAction SilentlyContinue | "
          f"Select-Object -First {maximum} TimeCreated,LevelDisplayName,ProviderName,Id,Message | "
          "ForEach-Object { \"{0:HH:mm} [{1}] {2} (Id {3}): {4}\" -f $_.TimeCreated,$_.LevelDisplayName,"
          "$_.ProviderName,$_.Id,($_.Message -replace '\\s+',' ').Substring(0,[Math]::Min(180,($_.Message).Length)) }")
    out = _ps(ps, timeout=40)
    return [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("(")]


def failed_logons(hours: int = 48, maximum: int = 15) -> list[str]:
    """Intentos de inicio de sesión fallidos (seguridad). Necesita permisos de administrador."""
    if os.name != "nt":
        return []
    ps = (f"$t=(Get-Date).AddHours(-{hours}); "
          "Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625;StartTime=$t} "
          f"-ErrorAction SilentlyContinue | Select-Object -First {maximum} TimeCreated | "
          "ForEach-Object { \"{0:dd/MM HH:mm}\" -f $_.TimeCreated }")
    out = _ps(ps, timeout=30)
    return [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("(")]


# ═══════════════════════════ ARRANQUE, SERVICIOS, RED ═══════════════════════════
def startup_items() -> list[str]:
    if os.name != "nt":
        return []
    out = _ps("Get-CimInstance Win32_StartupCommand | Select-Object Name,Command | "
              "ForEach-Object { \"$($_.Name): $($_.Command)\" }", timeout=25)
    return [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("(")][:30]


def stopped_services() -> list[str]:
    if os.name != "nt":
        return []
    out = _ps("Get-Service | Where-Object {$_.StartType -eq 'Automatic' -and $_.Status -ne 'Running'} | "
              "Select-Object -ExpandProperty DisplayName", timeout=25)
    return [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("(")][:25]


def pending_updates() -> str:
    if os.name != "nt":
        return ""
    out = _ps("(New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()."
              "Search('IsInstalled=0').Updates.Count", timeout=40)
    m = re.search(r"\d+", out)
    return f"{m.group()} actualizaciones de Windows pendientes." if m else ""


def network_health() -> list[str]:
    lines = []
    try:
        socket.setdefaulttimeout(3)
        t0 = time.time()
        socket.create_connection(("1.1.1.1", 53)).close()
        lines.append(f"Internet: OK (respuesta en {(time.time() - t0) * 1000:.0f} ms)")
    except Exception:
        lines.append("Internet: SIN conexión a 1.1.1.1")
    finally:
        socket.setdefaulttimeout(None)
    try:
        import psutil
        st = psutil.net_if_stats()
        up = [n for n, s in st.items() if s.isup and n.lower() != "loopback pseudo-interface 1"]
        lines.append("Interfaces activas: " + ", ".join(up[:6]))
    except Exception:
        pass
    return lines


def disk_health() -> list[str]:
    """SMART básico: ¿algún disco avisa de fallo?"""
    if os.name != "nt":
        return []
    out = _ps("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus | "
              "ForEach-Object { \"$($_.FriendlyName) [$($_.MediaType)]: $($_.HealthStatus)\" }", timeout=25)
    lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("(")]
    return lines


# ═══════════════════════════ INFORME Y EXPLICACIONES ═══════════════════════════
def quick_status() -> str:
    """Resumen hablado del estado del equipo."""
    parts = hardware()[:4]
    errs = event_errors(24, 5)
    net = network_health()[:1]
    txt = " ".join(parts) + " " + " ".join(net)
    if errs:
        txt += f" He visto {len(event_errors(24, 40))} errores en el registro en las últimas 24 h."
    else:
        txt += " Sin errores recientes en el registro."
    return txt


DIAG_PROMPT = """Eres un técnico informático experto y cercano. Con estos datos REALES del ordenador del usuario,
escribe un diagnóstico en español, claro y práctico, para alguien que no es experto.
Explica qué está bien, qué puede dar problemas y qué conviene hacer (pasos concretos y seguros).
No inventes datos que no estén aquí. Si algo necesita permisos de administrador o reinicio, dilo.

DATOS DEL EQUIPO:
{data}

Devuelve SOLO JSON:
{{"title": "Diagnóstico del equipo", "spoken": "2-3 frases con lo más importante y si hay algo urgente",
 "sections": [
   {{"heading": "Estado general", "paragraphs": ["..."], "bullets": ["..."]}},
   {{"heading": "Posibles problemas", "paragraphs": [], "bullets": ["..."]}},
   {{"heading": "Qué te recomiendo hacer", "paragraphs": [], "bullets": ["..."]}}
 ]}}"""


def full_report(fmt: str = "pdf") -> tuple[Path, dict]:
    data = []
    data.append("## HARDWARE Y RECURSOS\n" + "\n".join(hardware()))
    data.append("## PROCESOS\n" + "\n".join(top_processes()))
    data.append("## RED\n" + "\n".join(network_health()))
    dh = disk_health()
    if dh:
        data.append("## SALUD DE DISCOS (SMART)\n" + "\n".join(dh))
    svc = stopped_services()
    if svc:
        data.append("## SERVICIOS AUTOMÁTICOS PARADOS\n" + "\n".join(svc))
    up = pending_updates()
    if up:
        data.append("## ACTUALIZACIONES\n" + up)
    st = startup_items()
    if st:
        data.append("## PROGRAMAS AL ARRANCAR\n" + "\n".join(st[:20]))
    errs = event_errors(48, 40)
    if errs:
        data.append("## ERRORES RECIENTES DEL REGISTRO (48 h)\n" + "\n".join(errs))
    fl = failed_logons()
    if fl:
        data.append(f"## INICIOS DE SESIÓN FALLIDOS (48 h): {len(fl)}\n" + "\n".join(fl))

    doc = documents._parse_json(documents.generate(
        DIAG_PROMPT.format(data="\n\n".join(data)[:120_000]), json_mode=True))
    doc["subtitle"] = f"ZAR · Diagnóstico técnico · {datetime.now():%d/%m/%Y %H:%M}"
    # Anexo con los datos crudos, por si quieres mirarlos
    doc.setdefault("sections", []).append(
        {"heading": "Datos técnicos (detalle)", "paragraphs": [], "bullets": [l for d in data for l in d.splitlines()][:120]})
    path = documents.save_document(doc, fmt, name="Diagnostico del equipo")
    return path, doc


EXPLAIN_PROMPT = """Eres un técnico informático. El usuario te enseña un error o mensaje del ordenador.
Explícale en español, sencillo: qué significa, por qué suele pasar y cómo solucionarlo paso a paso.
Si es peligroso o conviene hacer copia de seguridad antes, avísale. Sé concreto.

MENSAJE / ERROR:
{error}"""


def explain(text: str) -> str:
    return documents.generate(EXPLAIN_PROMPT.format(error=text[:8000]))


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def it_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "status").lower()

    if action == "status":
        return quick_status()

    if action in ("errors", "logs"):
        hours = int(p.get("hours") or 24)
        errs = event_errors(hours, 30)
        if not errs:
            return f"No hay errores en el registro de Windows en las últimas {hours} horas. Todo tranquilo."
        head = "\n".join(f"• {e}" for e in errs[:12])
        try:
            summary = explain("Resume y agrupa estos errores del registro de Windows, di cuáles importan y "
                              "qué hacer:\n" + "\n".join(errs))
        except Exception:
            summary = ""
        return f"{len(errs)} errores en las últimas {hours} h.\n{head}\n\n{summary}"

    if action == "explain":
        q = (p.get("text") or "").strip()
        if not q:
            return "Dime qué error o mensaje quieres que te explique."
        return explain(q)

    if action in ("report", "diagnose", "full"):
        try:
            path, doc = full_report(p.get("format", "pdf"))
        except Exception as e:
            return f"No pude completar el diagnóstico: {e}"
        documents.open_file(path)
        return f"Diagnóstico terminado. {doc.get('spoken', '')} Informe guardado y abierto: {path.name}."

    if action == "processes":
        return " ".join(top_processes())

    if action == "network":
        return " ".join(network_health())

    if action == "disk":
        dh = disk_health()
        return " ".join(dh) if dh else "No pude leer el estado de los discos (prueba como administrador)."

    return "Acciones: status, errors, explain, report, processes, network, disk."
