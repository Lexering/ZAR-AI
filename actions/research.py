"""
research.py — El "laboratorio" de ZAR

  · Investigación: busca en internet (Google, a través de Gemini), y escribe un informe con fuentes.
  · Análisis de datos: lee un CSV o Excel, calcula, hace gráficas y escribe las conclusiones en PDF.

Necesita:  pip install -U pandas matplotlib openpyxl
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from actions import documents

RESEARCH_PROMPT = """Investiga a fondo este tema buscando en internet información actual y fiable:
«{topic}»
{focus}
Escribe en español un informe {depth} con: contexto, datos y cifras concretas (con fechas), puntos clave,
opiniones o enfoques distintos si los hay, riesgos u oportunidades, y una conclusión práctica.
Cita datos concretos; no inventes nada que no hayas encontrado."""

STRUCTURE_PROMPT = """Convierte este informe en un documento estructurado en español. Mantén todos los datos.
Devuelve SOLO JSON: {{"title": "...", "subtitle": "...", "spoken": "resumen hablado de 2-3 frases",
 "sections": [{{"heading": "...", "paragraphs": ["..."], "bullets": ["..."]}}]}}

INFORME:
{text}"""

DEPTHS = {"breve": "breve (1 página)", "medio": "completo (2-3 páginas)", "profundo": "muy detallado (4-6 páginas)"}


# ═══════════════════════════ INVESTIGACIÓN ═══════════════════════════
def search_and_write(topic: str, depth: str = "medio", focus: str = "", client=None) -> tuple[str, list[dict]]:
    from google.genai import types
    client = client or documents._gemini()
    prompt = RESEARCH_PROMPT.format(topic=topic, depth=DEPTHS.get(depth, DEPTHS["medio"]),
                                    focus=f"Enfoque: {focus}." if focus else "")
    cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
    last = None
    for model in documents.TEXT_MODELS:
        try:
            r = client.models.generate_content(model=model, contents=prompt, config=cfg)
            break
        except Exception as e:
            last = e
    else:
        raise RuntimeError(f"Gemini no respondió: {last}")
    sources, seen = [], set()
    try:
        for ch in (r.candidates[0].grounding_metadata.grounding_chunks or []):
            web = getattr(ch, "web", None)
            if web and web.uri and web.uri not in seen:
                seen.add(web.uri)
                sources.append({"title": web.title or web.uri, "uri": web.uri})
    except Exception:
        pass
    return (r.text or "").strip(), sources


def research_report(topic: str, depth: str = "medio", fmt: str = "pdf", focus: str = "",
                    client=None) -> tuple[Path, dict]:
    text, sources = search_and_write(topic, depth, focus, client)
    if not text:
        raise RuntimeError("La búsqueda no devolvió resultados.")
    doc = documents._parse_json(documents.generate(STRUCTURE_PROMPT.format(text=text[:200_000]),
                                                   json_mode=True, client=client))
    doc["subtitle"] = doc.get("subtitle") or f"Informe de investigación · {datetime.now():%d/%m/%Y}"
    if sources:
        doc.setdefault("sections", []).append({
            "heading": "Fuentes", "paragraphs": [],
            "bullets": [f"{s['title']} — {s['uri']}" for s in sources[:25]]})
    path = documents.save_document(doc, fmt, name=f"Informe {doc.get('title') or topic}")
    return path, doc


# ═══════════════════════════ ANÁLISIS DE DATOS ═══════════════════════════
def load_table(path: Path):
    import pandas as pd
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path)
    for sep in (",", ";", "\t"):
        try:
            df = pd.read_csv(path, sep=sep, encoding="utf-8-sig")
            if df.shape[1] > 1:
                return df
        except Exception:
            continue
    return pd.read_csv(path, sep=None, engine="python")


def _detect_dates(df):
    import pandas as pd
    for c in df.columns:
        if "date" in str(df[c].dtype):
            return c
    for c in df.columns:
        if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
            parsed = pd.to_datetime(df[c], errors="coerce", dayfirst=True)
            if parsed.notna().mean() > 0.8:
                df[c] = parsed
                return c
    return None


def profile(df) -> dict:
    import pandas as pd
    date_col = _detect_dates(df)
    num = [c for c in df.select_dtypes("number").columns]
    cat = [c for c in df.columns if c not in num and c != date_col and df[c].nunique() <= max(30, len(df) // 5)]
    lines = [f"{len(df)} filas y {df.shape[1]} columnas: {', '.join(map(str, df.columns[:30]))}."]
    if date_col:
        lines.append(f"Fechas en «{date_col}» del {df[date_col].min():%d/%m/%Y} al {df[date_col].max():%d/%m/%Y}.")
    for c in num[:8]:
        s = df[c].dropna()
        if len(s):
            lines.append(f"«{c}»: total {s.sum():,.2f}, media {s.mean():,.2f}, mín {s.min():,.2f}, máx {s.max():,.2f}.")
    for c in cat[:4]:
        top = df[c].value_counts().head(5)
        lines.append(f"«{c}» más frecuentes: " + ", ".join(f"{k} ({v})" for k, v in top.items()) + ".")
    if date_col and num:
        m = df.set_index(date_col)[num[0]].resample("MS").sum()
        if len(m) >= 2:
            lines.append(f"«{num[0]}» por mes: " + ", ".join(f"{i:%m/%Y}: {v:,.0f}" for i, v in m.tail(12).items()) + ".")
    return {"text": "\n".join(lines), "date": date_col, "num": num, "cat": cat}


def charts(df, prof: dict, folder: Path) -> list[tuple[str, Path]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out = []
    style = dict(color="#0e6e8c")

    def save(fig, name, title):
        p = folder / f"{name}.png"
        fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
        out.append((title, p))

    num, cat, date = prof["num"], prof["cat"], prof["date"]
    if date and num:
        s = df.set_index(date)[num[0]].resample("MS" if (df[date].max() - df[date].min()).days > 90 else "D").sum()
        fig, ax = plt.subplots(figsize=(8, 3.6))
        ax.plot(s.index, s.values, marker="o", linewidth=2, **style)
        ax.set_title(f"{num[0]} en el tiempo"); ax.grid(alpha=.3)
        save(fig, "evolucion", f"Evolución de {num[0]}")
    if cat and num:
        g = df.groupby(cat[0])[num[0]].sum().sort_values(ascending=False).head(10)
        fig, ax = plt.subplots(figsize=(8, 3.8))
        ax.barh([str(i) for i in g.index[::-1]], g.values[::-1], **style)
        ax.set_title(f"{num[0]} por {cat[0]}"); ax.grid(axis="x", alpha=.3)
        save(fig, "categorias", f"{num[0]} por {cat[0]}")
    elif num and not date:
        fig, ax = plt.subplots(figsize=(8, 3.6))
        ax.hist(df[num[0]].dropna(), bins=20, **style)
        ax.set_title(f"Distribución de {num[0]}")
        save(fig, "distribucion", f"Distribución de {num[0]}")
    return out


ANALYSIS_PROMPT = """Eres analista de datos. Con este resumen de una tabla («{name}»), escribe un análisis en español.
{question}
RESUMEN:
{profile}
Devuelve SOLO JSON: {{"title": "Análisis de ...", "spoken": "2-3 frases con lo más importante",
 "sections": [{{"heading": "Lo más importante", "paragraphs": ["..."], "bullets": ["..."]}},
              {{"heading": "Tendencias y detalles", "paragraphs": ["..."], "bullets": []}},
              {{"heading": "Recomendaciones", "paragraphs": [], "bullets": ["..."]}}]}}
Usa solo cifras del resumen. No inventes datos."""


def data_report(path: Path, question: str = "", fmt: str = "pdf", client=None) -> tuple[Path, dict]:
    df = load_table(path)
    prof = profile(df)
    doc = documents._parse_json(documents.generate(
        ANALYSIS_PROMPT.format(name=path.name, profile=prof["text"],
                               question=f"Responde especialmente a: {question}" if question else ""),
        json_mode=True, client=client))
    tmp = Path(tempfile.mkdtemp(prefix="alberto_graficas_"))
    for title, img in charts(df, prof, tmp):
        doc.setdefault("sections", []).append({"heading": title, "paragraphs": [], "bullets": [], "image": str(img)})
    doc["subtitle"] = f"Datos: {path.name} · {len(df)} filas · {datetime.now():%d/%m/%Y}"
    out = documents.save_document(doc, fmt, name=doc.get("title") or f"Analisis {path.stem}")
    return out, doc


# ═══════════════════════════ HERRAMIENTAS ═══════════════════════════
def research_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    topic = (p.get("topic") or "").strip()
    if not topic:
        return "Dime qué quieres que investigue."
    try:
        path, doc = research_report(topic, p.get("depth", "medio"), p.get("format", "pdf"), p.get("focus", ""))
    except Exception as e:
        return f"No pude completar la investigación: {e}"
    documents.open_file(path)
    return f"Investigación terminada. {doc.get('spoken', '')} Informe guardado y abierto: {path.name}."


def data_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    query = (p.get("file") or "").strip()
    if not query and player is not None and getattr(player, "current_file", None):
        query = player.current_file
    documents.DOC_EXTS.update({".xlsx", ".xls", ".csv"})
    path = documents.find_document(query)
    if path is None or path.suffix.lower() not in (".csv", ".xlsx", ".xls", ".xlsm"):
        return "No encuentro esa hoja de datos (CSV o Excel). Dime su nombre o pásamela con «Enviar a → ZAR»."
    try:
        out, doc = data_report(path, p.get("question", ""), p.get("format", "pdf"))
    except Exception as e:
        return f"No pude analizar «{path.name}»: {e}"
    documents.open_file(out)
    return f"Análisis de {path.name} listo. {doc.get('spoken', '')} Informe con gráficas abierto: {out.name}."
