"""
autopilot.py — El "piloto automático" de ZAR (agente autónomo)

Le das un objetivo y ZAR:
  1. Lo divide en pasos.
  2. Hace ÉL SOLO los pasos seguros (investigar, analizar datos, crear documentos, informes,
     mirar tu PC o tu tienda…).
  3. Para cualquier cosa IRREVERSIBLE (borrar, enviar correos, mover dinero, apagar) NO la hace:
     la deja anotada como "pendiente de tu permiso".
  4. Comprueba lo hecho y te entrega un informe con el resultado y lo que falta.

Ejemplos:
  "ZAR, organiza la información del proyecto X y hazme un informe."
  "Investiga qué necesito para montar un servidor en casa y compáralo, y guárdalo en PDF."
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from actions import documents

# Herramientas que el agente PUEDE usar solo (todas seguras: leen o crean, no destruyen).
SAFE_TOOLS = {
    "research":        "Investiga un tema en internet y escribe un informe. args: {topic, depth?, focus?}",
    "analyze_data":    "Analiza un CSV/Excel con gráficas. args: {file, question?}",
    "create_document": "Escribe un documento. args: {request, kind?, length?, format?}",
    "summarize_document": "Resume un archivo del PC. args: {file, focus?}",
    "projects":        "Estado de proyectos de código/web. args: {action: overview|detail, name?}",
    "it_support":      "Diagnóstico del PC. args: {action: status|errors|report}",
    "security_audit":  "Auditoría de seguridad del PC. args: {action: summary|audit}",
    "gmail":           "Lee el correo (solo lectura). args: {action: digest|unread}",
    "shopify":         "Estado de la tienda (solo lectura). args: {action: summary|report}",
    "web_search":      "Busca algo en internet. args: {query}",
    "weather":         "El tiempo. args: {city?}",
}

PLAN_PROMPT = """Eres ZAR en modo agente. Divide este OBJETIVO del usuario en pasos concretos.
OBJETIVO: «{goal}»

Puedes usar SOLO estas herramientas (para los pasos automáticos):
{tools}

Reglas:
- Usa el mínimo de pasos necesarios, en orden lógico.
- Cada paso automático: {{"tool": "<nombre>", "args": {{...}}, "why": "para qué"}}.
- Si un paso necesita una acción IRREVERSIBLE o que tú no puedes hacer con esas herramientas
  (borrar, enviar, pagar, comprar, publicar, instalar, llamar a alguien...), NO uses tool:
  ponlo como {{"manual": "qué tendría que hacer el usuario y por qué"}}.
- El último paso puede ser {{"tool": "create_document", ...}} si conviene dejar un informe.

Devuelve SOLO JSON:
{{"plan": "resumen en una frase de cómo lo vas a hacer",
  "steps": [ ... ]}}"""

REPORT_PROMPT = """Eres ZAR. Acabas de ejecutar un plan para este objetivo: «{goal}».
Esto es lo que hiciste y lo que obtuviste en cada paso:
{log}

Escribe en español un informe final para el usuario: qué has conseguido, lo más importante de los
resultados, y qué queda PENDIENTE de su permiso (los pasos manuales). Sé claro y honesto; no inventes.

Devuelve SOLO JSON: {{"title": "Resultado: ...", "spoken": "2-3 frases con lo esencial y lo que falta",
 "sections": [{{"heading": "Lo que he hecho", "paragraphs": [], "bullets": ["..."]}},
              {{"heading": "Resultados", "paragraphs": ["..."], "bullets": []}},
              {{"heading": "Pendiente de ti", "paragraphs": [], "bullets": ["..."]}}]}}"""


def plan(goal: str) -> dict:
    tools = "\n".join(f"- {k}: {v}" for k, v in SAFE_TOOLS.items())
    data = documents._parse_json(documents.generate(
        PLAN_PROMPT.format(goal=goal, tools=tools), json_mode=True))
    steps = [s for s in data.get("steps", []) if isinstance(s, dict) and ("tool" in s or "manual" in s)]
    steps = [s for s in steps if "tool" not in s or s["tool"] in SAFE_TOOLS]
    data["steps"] = steps[:12]
    return data


def run(goal: str, run_tool, speak=None, log=print, make_report: bool = True) -> tuple[Path | None, dict]:
    """
    run_tool(name, args) -> str  ejecuta una herramienta segura y devuelve su resultado.
    Devuelve (ruta_informe, doc).
    """
    p = plan(goal)
    steps = p.get("steps", [])
    if not steps:
        return None, {"spoken": "No he sabido convertir eso en pasos. Dímelo de otra forma."}
    if speak:
        speak(f"[SISTEMA] Di en una frase que vas a {p.get('plan', 'trabajar en ello')} y que avisarás al terminar.")

    results = []
    pending = []
    for i, step in enumerate(steps, 1):
        if "manual" in step:
            pending.append(step["manual"])
            results.append(f"PASO {i} (pendiente de ti): {step['manual']}")
            continue
        name, args = step["tool"], step.get("args", {})
        log(f"AGENTE: paso {i}/{len(steps)} → {name}({args})")
        try:
            out = run_tool(name, args)
        except Exception as e:
            out = f"(error: {e})"
        results.append(f"PASO {i} — {name}: {str(out)[:600]}")

    if not make_report:
        txt = " ".join(results)
        return None, {"spoken": f"Hecho. {txt[:300]}", "pending": pending}

    try:
        doc = documents._parse_json(documents.generate(
            REPORT_PROMPT.format(goal=goal, log="\n".join(results)[:120_000]), json_mode=True))
    except Exception:
        doc = {"title": "Resultado", "spoken": "He terminado el plan.",
               "sections": [{"heading": "Pasos", "paragraphs": [], "bullets": results}]}
    doc["subtitle"] = f"ZAR · Agente · {datetime.now():%d/%m/%Y %H:%M}"
    if pending:
        doc.setdefault("sections", []).append(
            {"heading": "Necesito tu permiso para", "paragraphs": [], "bullets": pending})
    path = None
    try:
        path = documents.save_document(doc, "pdf", name=f"Agente {goal[:40]}")
    except Exception:
        pass
    return path, doc


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def autopilot_tool(parameters: dict, run_tool=None, speak=None, log=print) -> str:
    p = parameters or {}
    goal = (p.get("goal") or "").strip()
    if not goal:
        return "Dime el objetivo. Por ejemplo: «organiza el proyecto X y hazme un informe»."
    if run_tool is None:
        return "El agente no está disponible ahora mismo."
    try:
        path, doc = run(goal, run_tool, speak=speak, log=log)
    except Exception as e:
        return f"No pude completar el plan: {e}"
    if path:
        try:
            documents.open_file(path)
        except Exception:
            pass
    extra = f" Informe abierto: {path.name}." if path else ""
    return f"Plan terminado. {doc.get('spoken', '')}{extra}"
