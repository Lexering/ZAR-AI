"""
documents.py — Documentos para ZAR

  · Crear documentos (Word o PDF) redactados por Gemini a partir de lo que le pidas.
  · Resumir documentos (PDF, Word, TXT, Markdown, PowerPoint) que le indiques por nombre,
    por ruta, o "el último que he descargado".
  · Utilidades compartidas con el dictado: carpeta de documentos y guardar PDF.

Necesita:  pip install -U python-docx reportlab python-pptx
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

TEXT_MODELS = ["gemini-2.5-flash", "gemini-flash-latest"]   # se prueba en orden
SUMMARY_MAX_SPOKEN = 900                                     # caracteres que se devuelven para decir en voz alta
DOC_EXTS = {".pdf", ".docx", ".txt", ".md", ".pptx", ".rtf", ".csv", ".xlsx", ".odt", ".html", ".json",
            ".jpg", ".jpeg", ".png", ".webp", ".heic"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
INLINE_LIMIT = 18 * 1024 * 1024                              # PDFs más grandes se suben como archivo


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


# ═══════════════════════════ CARPETAS DE WINDOWS ═══════════════════════════
_KNOWN = {
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "desktop":   "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
}


def known_folder(name: str) -> Path:
    """Ruta real de Documentos / Descargas / Escritorio (aunque estén en OneDrive)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            import uuid

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

            u = uuid.UUID(_KNOWN[name])
            g = GUID(u.fields[0], u.fields[1], u.fields[2],
                     (wintypes.BYTE * 8).from_buffer_copy(u.bytes[8:]))
            ptr = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ptr)) == 0:
                path = Path(ptr.value)
                ctypes.windll.ole32.CoTaskMemFree(ptr)
                return path
        except Exception:
            pass
    return Path.home() / {"documents": "Documents", "downloads": "Downloads", "desktop": "Desktop"}[name]


def output_dir() -> Path:
    d = known_folder("documents") / "ZAR"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(text: str, default: str = "documento") -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip()
    text = re.sub(r"\s+", "_", text)[:60]
    return text or default


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    p = folder / f"{stem}{ext}"
    i = 2
    while p.exists():
        p = folder / f"{stem}_{i}{ext}"
        i += 1
    return p


def open_file(path: Path):
    try:
        if os.name == "nt":
            os.startfile(str(path))          # abre con el programa predeterminado
    except Exception as e:
        print(f"[ZAR] No pude abrir {path}: {e}")


# ═══════════════════════════ GEMINI (texto) ═══════════════════════════
_client = None


def _gemini():
    global _client
    if _client is None:
        from google import genai
        key = None
        try:
            from actions import vault
            key = vault.get("gemini_api_key")
        except Exception:
            key = None
        if not key:
            cfg = json.loads((_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8"))
            key = cfg["gemini_api_key"]
        _client = genai.Client(api_key=key)
    return _client


def generate(contents, json_mode: bool = False, client=None) -> str:
    from google.genai import types
    client = client or _gemini()
    cfg = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    last = None
    for model in TEXT_MODELS:
        try:
            r = client.models.generate_content(model=model, contents=contents, config=cfg)
            return (r.text or "").strip()
        except Exception as e:            # modelo no disponible → probar el siguiente
            last = e
    raise RuntimeError(f"Gemini no respondió: {last}")


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?|```$", "", text).strip()
    data = json.loads(text)
    return data[0] if isinstance(data, list) and data else data


# ═══════════════════════════ ESCRIBIR WORD Y PDF ═══════════════════════════
# Estructura común:  {"title": str, "subtitle": str, "sections": [{"heading": str,
#                     "paragraphs": [str], "bullets": [str]}]}

def write_docx(doc: dict, path: Path) -> Path:
    from docx import Document
    from docx.shared import Pt, RGBColor

    d = Document()
    st = d.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(11)
    d.add_heading(doc.get("title") or "Documento", level=0)
    if doc.get("subtitle"):
        p = d.add_paragraph(doc["subtitle"])
        p.runs[0].italic = True
        p.runs[0].font.color.rgb = RGBColor(0x55, 0x5F, 0x6D)
    for sec in doc.get("sections") or []:
        if sec.get("heading"):
            d.add_heading(sec["heading"], level=1)
        for para in sec.get("paragraphs") or []:
            d.add_paragraph(para)
        for b in sec.get("bullets") or []:
            d.add_paragraph(b, style="List Bullet")
        if sec.get("image") and Path(sec["image"]).exists():
            from docx.shared import Cm
            d.add_picture(str(sec["image"]), width=Cm(16))
    d.core_properties.author = "ZAR"
    d.save(str(path))
    return path


def _pdf_fonts():
    """Usa Segoe UI / Arial de Windows si están (mejor acentos y aspecto); si no, Helvetica."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for regular, bold in (("segoeui.ttf", "segoeuib.ttf"), ("arial.ttf", "arialbd.ttf")):
        if (win / regular).exists() and (win / bold).exists():
            try:
                pdfmetrics.registerFont(TTFont("DocFont", str(win / regular)))
                pdfmetrics.registerFont(TTFont("DocFont-Bold", str(win / bold)))
                return "DocFont", "DocFont-Bold"
            except Exception:
                pass
    return "Helvetica", "Helvetica-Bold"


def write_pdf(doc: dict, path: Path, footer: str = "") -> Path:
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

    font, bold = _pdf_fonts()
    ink, muted, accent = colors.HexColor("#1d2430"), colors.HexColor("#5b6572"), colors.HexColor("#0e6e8c")
    title_st = ParagraphStyle("t", fontName=bold, fontSize=22, leading=27, textColor=ink, spaceAfter=4)
    sub_st = ParagraphStyle("s", fontName=font, fontSize=11.5, leading=16, textColor=muted, spaceAfter=14)
    h_st = ParagraphStyle("h", fontName=bold, fontSize=14, leading=18, textColor=accent, spaceBefore=12, spaceAfter=5)
    body = ParagraphStyle("b", fontName=font, fontSize=11, leading=16.5, textColor=ink, spaceAfter=7)

    story = [Paragraph(escape(doc.get("title") or "Documento"), title_st)]
    if doc.get("subtitle"):
        story.append(Paragraph(escape(doc["subtitle"]), sub_st))
    else:
        story.append(Spacer(1, 8))
    for sec in doc.get("sections") or []:
        if sec.get("heading"):
            story.append(Paragraph(escape(sec["heading"]), h_st))
        for para in sec.get("paragraphs") or []:
            story.append(Paragraph(escape(para).replace("\n", "<br/>"), body))
        bullets = [b for b in sec.get("bullets") or [] if b]
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(escape(b), body), leftIndent=12) for b in bullets],
                                      bulletType="bullet", start="•", leftIndent=14, bulletColor=accent))
        if sec.get("image") and Path(sec["image"]).exists():
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(str(sec["image"])).getSize()
            w = A4[0] - 44 * mm
            story.append(Spacer(1, 4))
            story.append(Image(str(sec["image"]), width=w, height=w * ih / iw))
            story.append(Spacer(1, 8))

    foot = footer or f"Generado por ZAR · {datetime.now():%d/%m/%Y %H:%M}"

    def decorate(c, d):
        c.saveState()
        c.setFont(font, 8.5)
        c.setFillColor(muted)
        c.drawString(20 * mm, 12 * mm, foot)
        c.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Página {d.page}")
        c.restoreState()

    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm,
                      topMargin=22 * mm, bottomMargin=22 * mm,
                      title=doc.get("title") or "Documento", author="ZAR"
                      ).build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path


def save_document(doc: dict, fmt: str = "docx", name: str | None = None, folder: Path | None = None) -> Path:
    fmt = "pdf" if str(fmt).lower().strip(".") == "pdf" else "docx"
    folder = folder or output_dir()
    path = unique_path(folder, safe_name(name or doc.get("title", "")), "." + fmt)
    return write_pdf(doc, path) if fmt == "pdf" else write_docx(doc, path)


# ═══════════════════════════ CREAR DOCUMENTOS ═══════════════════════════
CREATE_PROMPT = """Eres un redactor profesional. Escribe un documento completo en español según esta petición:

«{request}»

Tipo de documento: {kind}. Extensión aproximada: {length}.
Escribe contenido real y útil (no plantillas con huecos), bien organizado en secciones.
Devuelve SOLO JSON con esta forma exacta:
{{"title": "...", "subtitle": "una línea que describa el documento",
  "sections": [{{"heading": "...", "paragraphs": ["..."], "bullets": ["..."]}}]}}
Usa "bullets" solo cuando una lista aporte claridad (puede ir vacío)."""

LENGTHS = {"corto": "1 página", "medio": "2-3 páginas", "largo": "4-6 páginas"}


def create_document(request: str, fmt: str = "docx", kind: str = "", length: str = "medio",
                    title: str = "", client=None) -> tuple[Path, dict]:
    prompt = CREATE_PROMPT.format(request=request, kind=kind or "el que mejor encaje con la petición",
                                  length=LENGTHS.get(length, length or "2-3 páginas"))
    doc = _parse_json(generate(prompt, json_mode=True, client=client))
    if title:
        doc["title"] = title
    path = save_document(doc, fmt, name=doc.get("title"))
    return path, doc


# ═══════════════════════════ BUSCAR Y LEER DOCUMENTOS ═══════════════════════════
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _candidates(max_depth: int = 3):
    seen = set()
    roots = [output_dir() / "Uploads"] + [known_folder(k) for k in ("downloads", "desktop", "documents")]
    od = Path(os.environ.get("OneDrive", "")) if os.environ.get("OneDrive") else None
    if od and od.exists():
        roots.append(od)
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in ("node_modules", "venv")]
            for f in filenames:
                p = Path(dirpath) / f
                if p.suffix.lower() in DOC_EXTS and p not in seen:
                    seen.add(p)
                    yield p


LATEST_WORDS = {"ultimo", "ultima", "reciente", "descargado", "descargada", "latest", "last"}


def find_document(query: str = "") -> Path | None:
    """Ruta exacta, 'el último descargado', o búsqueda por palabras del nombre."""
    q = (query or "").strip().strip('"')
    if q and Path(q).expanduser().exists():
        return Path(q).expanduser()
    words = [w for w in _norm(q).split() if len(w) > 1]
    ext_hint = next((e for e in DOC_EXTS if e.strip(".") in words), None)
    words = [w for w in words if w not in {e.strip(".") for e in DOC_EXTS}
             and w not in {"documento", "archivo", "fichero", "el", "la", "del", "de", "que", "he", "mi", "word"}]
    files = list(_candidates())
    if ext_hint:
        files = [f for f in files if f.suffix.lower() == ext_hint] or files
    if not files:
        return None
    if not words or set(words) & LATEST_WORDS:
        return max(files, key=lambda f: f.stat().st_mtime)
    scored = []
    for f in files:
        name = _norm(f.stem)
        score = sum(1 for w in words if w in name)
        if score:
            scored.append((score, f.stat().st_mtime, f))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]


def read_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        from docx import Document
        d = Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(parts)
    if ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            out.append(f"[Hoja {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    out.append(" | ".join("" if v is None else str(v) for v in row))
                if len(out) > 4000:
                    break
        return "\n".join(out)
    if ext == ".odt":
        import zipfile
        xml = zipfile.ZipFile(str(path)).read("content.xml").decode("utf-8", "ignore")
        return re.sub(r"<[^>]+>", " ", xml)
    if ext == ".pptx":
        from pptx import Presentation
        out = []
        for i, slide in enumerate(Presentation(str(path)).slides, 1):
            txt = [sh.text_frame.text for sh in slide.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
            if txt:
                out.append(f"[Diapositiva {i}]\n" + "\n".join(txt))
        return "\n\n".join(out)
    return path.read_text(encoding="utf-8", errors="ignore")


# ═══════════════════════════ RESUMIR ═══════════════════════════
SUMMARY_PROMPT = """Resume este documento en español.
Nivel de detalle: {detail}. {focus}
Devuelve SOLO JSON:
{{"title": "Resumen: <título del documento>",
  "spoken": "resumen hablado de 2 a 4 frases, natural, sin listas",
  "sections": [{{"heading": "Idea principal", "paragraphs": ["..."], "bullets": []}},
               {{"heading": "Puntos clave", "paragraphs": [], "bullets": ["...", "..."]}},
               {{"heading": "Datos y fechas importantes", "paragraphs": [], "bullets": ["..."]}}]}}
Omite una sección si el documento no tiene ese tipo de información."""


def summarize_document(path: Path, detail: str = "medio", focus: str = "", client=None) -> dict:
    from google.genai import types
    prompt = SUMMARY_PROMPT.format(detail=detail, focus=f"Céntrate en: {focus}." if focus else "")
    if path.suffix.lower() == ".pdf":
        data = path.read_bytes()
        if len(data) <= INLINE_LIMIT:
            part = types.Part.from_bytes(data=data, mime_type="application/pdf")
        else:
            client = client or _gemini()
            part = client.files.upload(file=str(path))
            for _ in range(60):
                if str(getattr(part, "state", "ACTIVE")).endswith("ACTIVE"):
                    break
                time.sleep(2)
                part = client.files.get(name=part.name)
        contents = [part, prompt]
    else:
        text = read_text(path)
        if not text.strip():
            raise RuntimeError("El documento no tiene texto que pueda leer.")
        contents = [f"DOCUMENTO «{path.name}»:\n\n{text[:900_000]}", prompt]
    data = _parse_json(generate(contents, json_mode=True, client=client))
    data.setdefault("title", f"Resumen: {path.stem}")
    data["subtitle"] = f"Documento original: {path.name}"
    return data


# ═══════════════════════════ ANALIZAR / EXPLICAR ═══════════════════════════
ANALYZE_PROMPT = """Eres ZAR y el usuario te ha pasado este archivo. Haz exactamente lo que pide:
«{task}»
- Si pide explicarlo: explícalo claro y fácil, como a un amigo, con ejemplos si ayudan; empieza por la idea general
  y luego lo importante, en orden.
- Si hace una pregunta concreta, respóndela usando el archivo (y di si el archivo no lo dice).
- Si son ejercicios o un examen: explica cómo se resuelven paso a paso.
- Si es una foto (apuntes, pizarra, ejercicio, captura), léela y trabaja con lo que pone.
Escribe en {lang}, en texto corrido pensado para decirlo en voz alta (sin tablas ni markdown, sin asteriscos),
máximo unas 350 palabras salvo que pida más detalle."""


def _file_part(path: Path, client=None):
    """The file as something Gemini can read: PDF/images directly, the rest as text."""
    ext = path.suffix.lower()
    if ext == ".pdf" or ext in IMG_EXTS:
        from google.genai import types
        mime = "application/pdf" if ext == ".pdf" else {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                                                        ".webp": "image/webp", ".heic": "image/heic"}[ext]
        data = path.read_bytes()
        if len(data) <= INLINE_LIMIT:
            return types.Part.from_bytes(data=data, mime_type=mime)
        client = client or _gemini()
        part = client.files.upload(file=str(path))
        for _ in range(60):
            if str(getattr(part, "state", "ACTIVE")).endswith("ACTIVE"):
                break
            time.sleep(2)
            part = client.files.get(name=part.name)
        return part
    if ext == ".doc":
        raise RuntimeError("Los .doc antiguos no los leo: conviértelo a Word (.docx) o PDF con office_pdf.")
    text = read_text(path)
    if not text.strip():
        raise RuntimeError("El archivo no tiene texto que pueda leer.")
    return f"ARCHIVO «{path.name}»:\n\n{text[:900_000]}"


def analyze_document(path: Path, task: str = "", lang: str = "español") -> str:
    task = task.strip() or "Explícame este documento"
    return generate([_file_part(path), ANALYZE_PROMPT.format(task=task, lang=lang)])


def analyze_document_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    query = (p.get("file") or "").strip()
    if not query and player is not None and getattr(player, "current_file", None):
        query = str(player.current_file)
    path = find_document(query)
    if path is None:
        return (f"No encuentro ningún archivo que coincida con «{query}» (busco en Mis archivos, Descargas, Escritorio, "
                "Documentos y OneDrive). Pide al usuario el nombre exacto o que lo suba con la carpeta Mis archivos.")
    try:
        answer = analyze_document(path, p.get("task") or p.get("question") or "", p.get("lang") or "español")
    except Exception as e:
        return f"No pude analizar «{path.name}»: {e}"
    if player is not None and hasattr(player, "write_log"):
        try:
            player.write_log(f"Archivo analizado: {path.name}")
        except Exception:
            pass
    return (f"Archivo: {path.name}. Lo que he sacado (cuéntaselo con tus palabras y tu estilo, sin leerlo como un robot):\n"
            + answer[:4500])


# ═══════════════════════════ HERRAMIENTAS PARA ZAR ═══════════════════════════
def create_document_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    request = (p.get("request") or p.get("topic") or "").strip()
    if not request:
        return "Dime de qué quieres el documento."
    try:
        path, doc = create_document(request, p.get("format", "docx"), p.get("kind", ""),
                                    p.get("length", "medio"), p.get("title", ""))
    except Exception as e:
        return f"No pude crear el documento: {e}"
    if p.get("open", True) is not False:
        open_file(path)
    n = len(doc.get("sections") or [])
    return f"Documento «{doc.get('title')}» creado ({n} secciones) y guardado en {path}. Ya está abierto."


def summarize_document_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    query = (p.get("file") or p.get("query") or "").strip()
    if not query and player is not None and getattr(player, "current_file", None):
        query = player.current_file
    path = find_document(query)
    if path is None:
        return (f"No encuentro ningún documento que coincida con «{query}» en Descargas, Escritorio o Documentos."
                if query else "No encuentro documentos en Descargas, Escritorio ni Documentos.")
    try:
        data = summarize_document(path, p.get("detail", "medio"), p.get("focus", ""))
    except Exception as e:
        return f"No pude resumir «{path.name}»: {e}"
    saved = ""
    if p.get("save"):
        out = save_document(data, p.get("format", "pdf"), name=f"Resumen {path.stem}")
        open_file(out)
        saved = f" Resumen guardado en {out} y abierto."
    spoken = (data.get("spoken") or "")[:SUMMARY_MAX_SPOKEN]
    bullets = []
    for s in data.get("sections") or []:
        bullets += s.get("bullets") or []
    extra = (" Puntos clave: " + "; ".join(bullets[:6]) + ".") if bullets else ""
    return f"Documento: {path.name}. Resumen: {spoken}{extra}{saved}"
