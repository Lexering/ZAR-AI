"""
presentations.py — ZAR crea presentaciones (PowerPoint, editable y abrible en Canva)

  · "ZAR, hazme una presentación sobre X" / "una presentación de 8 diapositivas sobre Y para clientes"
  · Escribe el contenido con la IA (título, diapositivas con puntos y notas del ponente)
    y lo guarda como .pptx con un diseño oscuro estilo JARVIS.
  · Lo abre, y si quieres te lo manda por Telegram.

El .pptx se abre en PowerPoint y se IMPORTA a Canva (Crear → Importar archivo) para retocarlo allí.

Necesita:  pip install python-pptx
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from actions import documents

# Tema oscuro tipo JARVIS
BG = (10, 14, 20)
ACCENT = (14, 200, 230)
TEXT = (223, 247, 255)
MUTED = (150, 170, 185)

OUTLINE_PROMPT = """Eres un experto en presentaciones. Crea el contenido de una presentación en español.
Tema: «{topic}»
{extra}
Haz {n} diapositivas (incluida portada y cierre). Cada diapositiva: un título claro y de 3 a 5 puntos
breves (frases cortas, no párrafos). Añade una nota corta para el ponente en cada una.

Devuelve SOLO JSON:
{{"title": "Título de la presentación", "subtitle": "subtítulo o lema corto",
  "slides": [{{"heading": "Título diapositiva", "bullets": ["punto", "punto"], "notes": "nota del ponente"}}]}}"""


def _outline(topic: str, n: int, audience: str) -> dict:
    extra = f"Público: {audience}." if audience else ""
    data = documents._parse_json(documents.generate(
        OUTLINE_PROMPT.format(topic=topic, n=n, extra=extra), json_mode=True))
    data["slides"] = [s for s in data.get("slides", []) if isinstance(s, dict) and s.get("heading")]
    return data


# ═══════════════════════════ CONSTRUIR .PPTX ═══════════════════════════
def build_pptx(outline: dict, path: Path) -> Path:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    prs = Presentation()
    prs.slide_width = Inches(13.333)       # 16:9
    prs.slide_height = Inches(7.5)
    W, H = prs.slide_width, prs.slide_height
    blank = prs.slide_layouts[6]

    def bg(slide):
        f = slide.background.fill
        f.solid(); f.fore_color.rgb = RGBColor(*BG)

    def box(slide, l, t, w, h):
        return slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h)).text_frame

    def bar(slide):
        from pptx.enum.shapes import MSO_SHAPE
        s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.7), Inches(1.55), Inches(1.6), Pt(5))
        s.fill.solid(); s.fill.fore_color.rgb = RGBColor(*ACCENT); s.line.fill.background()

    # Portada
    s0 = prs.slides.add_slide(blank); bg(s0)
    tf = box(s0, 0.9, 2.6, 11.5, 2.2); tf.word_wrap = True
    p = tf.paragraphs[0]; p.text = outline.get("title", "Presentación")
    p.font.size = Pt(44); p.font.bold = True; p.font.color.rgb = RGBColor(*TEXT)
    if outline.get("subtitle"):
        p2 = tf.add_paragraph(); p2.text = outline["subtitle"]
        p2.font.size = Pt(20); p2.font.color.rgb = RGBColor(*ACCENT)
    foot = box(s0, 0.9, 6.7, 11.5, 0.5).paragraphs[0]
    foot.text = f"ZAR · {datetime.now():%d/%m/%Y}"
    foot.font.size = Pt(11); foot.font.color.rgb = RGBColor(*MUTED)

    # Diapositivas
    for sl in outline["slides"]:
        s = prs.slides.add_slide(blank); bg(s)
        ht = box(s, 0.7, 0.6, 12, 1.0).paragraphs[0]
        ht.text = sl["heading"]; ht.font.size = Pt(30); ht.font.bold = True
        ht.font.color.rgb = RGBColor(*TEXT); ht.alignment = PP_ALIGN.LEFT
        bar(s)
        body = box(s, 0.9, 1.9, 11.5, 5.0); body.word_wrap = True
        first = True
        for b in sl.get("bullets", [])[:7]:
            par = body.paragraphs[0] if first else body.add_paragraph()
            first = False
            par.text = "•  " + str(b)
            par.font.size = Pt(20); par.font.color.rgb = RGBColor(*TEXT); par.space_after = Pt(10)
            par.alignment = PP_ALIGN.LEFT
        if sl.get("notes"):
            s.notes_slide.notes_text_frame.text = str(sl["notes"])

    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))
    return path


def create(topic: str, slides: int = 8, audience: str = "") -> tuple[Path, dict]:
    n = max(3, min(int(slides or 8), 20))
    outline = _outline(topic, n, audience)
    stem = documents.safe_name(outline.get("title") or topic, "presentacion")
    path = documents.unique_path(documents.output_dir(), stem, ".pptx")
    build_pptx(outline, path)
    return path, outline


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def presentation_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    topic = (p.get("topic") or "").strip()
    if not topic:
        return "¿Sobre qué quieres la presentación?"
    try:
        path, outline = create(topic, p.get("slides", 8), p.get("audience", ""))
    except Exception as e:
        if "python-pptx" in str(e) or "No module named 'pptx'" in str(e):
            return "Me falta la librería de presentaciones. Instálala con: pip install python-pptx"
        return f"No pude crear la presentación: {e}"
    try:
        documents.open_file(path)
    except Exception:
        pass
    sent = ""
    if p.get("telegram"):
        from actions import telegram_link
        sent = " Te la he mandado por Telegram." if telegram_link.send_document(path, f"Presentación: {outline.get('title','')}") else ""
    n = len(outline.get("slides", [])) + 1
    return (f"Presentación lista: «{outline.get('title', topic)}», {n} diapositivas, guardada y abierta ({path.name}). "
            f"Se abre en PowerPoint y puedes importarla a Canva.{sent}")


def make_for_telegram(topic: str, slides: int = 8) -> tuple[Path, dict]:
    path, outline = create(topic, slides)
    return path, outline
