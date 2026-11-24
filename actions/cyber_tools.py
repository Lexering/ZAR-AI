"""
cyber_tools.py — Análisis de seguridad sin riesgo (ZAR)

  · Comprobar archivo:  calcula el hash y lo consulta en VirusTotal. NUNCA ejecuta el archivo.
        "ZAR, analiza este archivo"  (pásaselo con «Enviar a → ZAR»)
  · Comprobar enlace:   mira si una URL es peligrosa, sin abrirla (VirusTotal).
        "¿es seguro este enlace: ...?"
  · Contraseña filtrada: comprueba si una contraseña aparece en filtraciones (sin enviarla entera).
        Usa Pwned Passwords (gratis, no hace falta clave).
  · Correo filtrado:    si tienes clave de Have I Been Pwned, mira si tu correo salió en filtraciones.

Claves (opcionales) en config/api_keys.json:
    "virustotal_api_key": "..."   (gratis en virustotal.com, 500 consultas/día)
    "hibp_api_key": "..."         (de pago, solo para lo del correo)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _keys() -> dict:
    data = {}
    try:
        data = json.loads((_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8"))
    except Exception:
        data = {}
    try:
        from actions import vault
        for k in ("virustotal_api_key", "hibp_api_key"):
            v = vault.get(k)
            if v:
                data[k] = v
    except Exception:
        pass
    return data


# ═══════════════════════════ ARCHIVO ═══════════════════════════
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def virustotal_file(sha256: str) -> dict:
    import requests
    key = _keys().get("virustotal_api_key")
    if not key:
        return {"error": "sin_clave"}
    r = requests.get(f"https://www.virustotal.com/api/v3/files/{sha256}",
                     headers={"x-apikey": key}, timeout=30)
    if r.status_code == 404:
        return {"unknown": True}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    stats = r.json()["data"]["attributes"]["last_analysis_stats"]
    names = r.json()["data"]["attributes"].get("meaningful_name", "")
    return {"malicious": stats.get("malicious", 0), "suspicious": stats.get("suspicious", 0),
            "harmless": stats.get("harmless", 0), "undetected": stats.get("undetected", 0), "name": names}


def check_file(path: Path) -> str:
    if not path.exists():
        return f"No encuentro el archivo: {path}"
    try:
        digest = sha256_file(path)
    except Exception as e:
        return f"No pude leer el archivo: {e}"
    res = virustotal_file(digest)
    head = f"«{path.name}»\nSHA-256: {digest}"
    if res.get("error") == "sin_clave":
        return (head + f"\n\nPara comprobarlo en VirusTotal necesito una clave gratuita. "
                "Mientras, puedes pegar este hash en virustotal.com a mano.")
    if res.get("error"):
        return head + f"\n\nNo pude consultar VirusTotal ({res['error']}). Prueba el hash a mano en virustotal.com."
    if res.get("unknown"):
        return head + "\n\nVirusTotal no conoce este archivo (nadie lo ha subido). Trátalo con cuidado: si no sabes de dónde viene, no lo abras."
    mal = res["malicious"] + res["suspicious"]
    if mal == 0:
        return head + f"\n\n✅ Limpio: 0 antivirus lo marcan como peligroso (de más de {res['harmless'] + res['undetected']} analizados)."
    verdict = "❌ PELIGROSO" if mal >= 3 else "⚠️ Sospechoso"
    return head + f"\n\n{verdict}: {mal} motores de antivirus lo detectan como malware. NO lo abras. Bórralo o ponlo en cuarentena."


# ═══════════════════════════ ENLACE ═══════════════════════════
def virustotal_url(url: str) -> dict:
    import base64
    import requests
    key = _keys().get("virustotal_api_key")
    if not key:
        return {"error": "sin_clave"}
    url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
    r = requests.get(f"https://www.virustotal.com/api/v3/urls/{url_id}",
                     headers={"x-apikey": key}, timeout=30)
    if r.status_code == 404:
        return {"unknown": True}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    stats = r.json()["data"]["attributes"]["last_analysis_stats"]
    return {"malicious": stats.get("malicious", 0), "suspicious": stats.get("suspicious", 0)}


SUSPICIOUS_TLD = (".zip", ".mov", ".xyz", ".top", ".tk", ".gq", ".cf", ".ml", ".ru", ".su")


def url_heuristics(url: str) -> list[str]:
    import re
    flags = []
    low = url.lower()
    if re.search(r"https?://\d+\.\d+\.\d+\.\d+", low):
        flags.append("usa una dirección IP en vez de un nombre (típico de phishing)")
    if low.startswith("http://"):
        flags.append("no es cifrado (http, sin la s)")
    if any(low.split("/")[2:3] and low.split("/")[2].endswith(t) for t in SUSPICIOUS_TLD):
        flags.append("termina en un dominio poco fiable")
    if low.count("@") and "@" in low.split("//")[-1].split("/")[0]:
        flags.append("tiene una @ en la dirección (puede ocultar el destino real)")
    if any(w in low for w in ("verify", "login", "secure", "account", "banco", "update", "confirm")) and \
            any(t in low for t in SUSPICIOUS_TLD + (".click", ".link")):
        flags.append("imita una página de acceso en un dominio raro")
    if len(low) > 120:
        flags.append("es muy larga (puede esconder el destino)")
    return flags


def check_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    flags = url_heuristics(url)
    res = virustotal_url(url)
    out = [f"Enlace: {url}"]
    if res.get("malicious", 0) + res.get("suspicious", 0) > 0:
        out.append(f"❌ VirusTotal: {res['malicious'] + res['suspicious']} motores lo marcan como peligroso. NO lo abras.")
    elif res.get("unknown"):
        out.append("VirusTotal no lo tiene analizado todavía.")
    elif res.get("error") == "sin_clave":
        out.append("(Sin clave de VirusTotal; análisis solo por señales.)")
    elif not res.get("error"):
        out.append("✅ VirusTotal no lo marca como peligroso.")
    if flags:
        out.append("Señales sospechosas: " + "; ".join(flags) + ".")
    elif "❌" not in " ".join(out):
        out.append("No veo señales obvias de engaño, pero ante la duda no metas tus datos.")
    return "\n".join(out)


# ═══════════════════════════ CONTRASEÑA / CORREO FILTRADOS ═══════════════════════════
def password_pwned(password: str) -> int:
    """Cuántas veces ha aparecido la contraseña en filtraciones (0 = nunca). No envía la contraseña entera."""
    import requests
    sha1 = hashlib.sha1(password.encode()).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    r = requests.get(f"https://api.pwnedpasswords.com/range/{prefix}", timeout=20,
                     headers={"User-Agent": "ZAR"})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    for line in r.text.splitlines():
        h, _, count = line.partition(":")
        if h.strip() == suffix:
            return int(count)
    return 0


def email_breaches(email: str) -> dict:
    import requests
    key = _keys().get("hibp_api_key")
    if not key:
        return {"error": "sin_clave"}
    r = requests.get(f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
                     headers={"hibp-api-key": key, "User-Agent": "ZAR"},
                     params={"truncateResponse": "true"}, timeout=30)
    if r.status_code == 404:
        return {"breaches": []}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    return {"breaches": [b["Name"] for b in r.json()]}


def check_password(password: str) -> str:
    try:
        n = password_pwned(password)
    except Exception as e:
        return f"No pude comprobarlo ({e})."
    if n == 0:
        return "✅ Esa contraseña no aparece en ninguna filtración conocida. Aun así, úsala solo en un sitio."
    return (f"❌ Esa contraseña ha aparecido {n:,} veces en filtraciones. Está quemada: cámbiala en cualquier "
            "sitio donde la uses y no la reutilices.")


def check_email(email: str) -> str:
    res = email_breaches(email)
    if res.get("error") == "sin_clave":
        return ("Para comprobar un correo necesito una clave de Have I Been Pwned (de pago). Sin ella, puedes "
                "mirarlo gratis a mano en haveibeenpwned.com.")
    if res.get("error"):
        return f"No pude comprobarlo ({res['error']})."
    br = res["breaches"]
    if not br:
        return f"✅ {email} no aparece en las filtraciones conocidas."
    return (f"⚠️ {email} ha aparecido en {len(br)} filtraciones: {', '.join(br[:10])}. "
            "Cambia la contraseña en esos servicios y activa la verificación en dos pasos.")


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def cyber_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "").lower()
    value = (p.get("value") or "").strip()

    if action in ("file", "archivo", "scan_file"):
        from actions import documents
        documents.DOC_EXTS.update({".exe", ".zip", ".msi", ".dll", ".bat", ".js", ".scr", ".apk", ".pdf", ".docx"})
        q = value or (getattr(player, "current_file", None) if player else None) or ""
        path = documents.find_document(q) if q else None
        if path is None and q and Path(q).expanduser().exists():
            path = Path(q).expanduser()
        if path is None:
            return "Dime qué archivo analizar o pásamelo con «Enviar a → ZAR»."
        return check_file(path)

    if action in ("url", "enlace", "link"):
        if not value:
            return "Dame el enlace que quieres comprobar."
        return check_url(value)

    if action in ("password", "contrasena", "clave"):
        if not value:
            return "Dime la contraseña que quieres comprobar (no la guardo)."
        return check_password(value)

    if action in ("email", "correo"):
        if not value:
            return "Dime el correo que quieres comprobar."
        return check_email(value)

    return "Acciones: file (archivo), url (enlace), password (contraseña), email (correo)."
