"""
live_vision.py — Los "ojos" de ZAR (visión en directo)

Apunta la webcam a algo y pregúntale, o pídele que mire la pantalla:
  · "ZAR, ¿qué estoy viendo?"            → describe lo que enfoca la webcam.
  · "¿Qué pieza es esta?" (apuntando)        → te lo explica (con aviso de verificar).
  · "Lee lo que pone aquí"                    → transcribe el texto que ve.
  · "Mira la pantalla, ¿qué significa este error?" → analiza lo que hay en pantalla.

Usa Gemini (tu misma clave) para entender la imagen. Hace una foto en el momento de preguntar.
Para cosas de riesgo (mecánica, electricidad, salud) te recuerda confirmar antes de actuar.
"""

from __future__ import annotations

LOOK_PROMPT = """Eres ZAR, un asistente estilo JARVIS que ve a través de la webcam del usuario.
Mira esta imagen y responde en español, en tono cercano y claro.
{question}
Sé concreto sobre lo que ves. Si no estás seguro de algo, dilo.
Si se trata de mecánica, electricidad, gas, salud o cualquier cosa con riesgo, da la información
pero recuerda en una frase que conviene verificarlo antes de manipular nada."""

READ_PROMPT = """Transcribe EXACTAMENTE todo el texto que aparezca en esta imagen, en español tal cual esté.
Respeta saltos de línea. Si hay poco texto o está borroso, pon lo que puedas leer. No añadas comentarios."""

SCREEN_PROMPT = """Eres ZAR. Esta es una captura de la pantalla del usuario.
{question}
Explica en español, claro y breve, qué se ve y, si hay un error o algo que resolver, cómo arreglarlo."""


def _gemini_vision(image: bytes, prompt: str, mime: str = "image/jpeg") -> str:
    from google.genai import types
    from actions import documents
    return documents.generate([types.Part.from_bytes(data=image, mime_type=mime), prompt]).strip()


# ═══════════════════════════ FUENTES DE IMAGEN ═══════════════════════════
def webcam_frame() -> bytes | None:
    from actions import security
    try:
        cam = security.Camera()
    except Exception:
        return None
    try:
        f = cam.frame()
        return cam.jpeg(f) if f is not None else None
    finally:
        cam.close()


def screen_frame() -> bytes | None:
    from actions import security
    shot = security.screenshot_jpeg()
    return shot or None


# ═══════════════════════════ ACCIONES ═══════════════════════════
def look(question: str = "") -> str:
    img = webcam_frame()
    if img is None:
        return ("No pude usar la webcam. ¿Está conectada y libre? Si el control por gestos la está usando, "
                "desactívalo un momento.")
    q = f"El usuario pregunta: «{question}»." if question else "El usuario quiere saber qué está viendo."
    try:
        return _gemini_vision(img, LOOK_PROMPT.format(question=q))
    except Exception as e:
        return f"No pude analizar la imagen: {e}"


def read_text() -> str:
    img = webcam_frame()
    if img is None:
        return "No pude usar la webcam para leer. Revisa que esté conectada y libre."
    try:
        text = _gemini_vision(img, READ_PROMPT)
    except Exception as e:
        return f"No pude leer el texto: {e}"
    return f"Dice:\n{text}" if text else "No veo texto legible. Acércalo o mejora la luz."


def look_screen(question: str = "") -> str:
    img = screen_frame()
    if img is None:
        return "No pude hacer la captura de pantalla."
    q = f"El usuario pregunta: «{question}»." if question else "El usuario quiere saber qué hay en pantalla."
    try:
        return _gemini_vision(img, SCREEN_PROMPT.format(question=q))
    except Exception as e:
        return f"No pude analizar la pantalla: {e}"


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def vision_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "look").lower()
    question = (p.get("question") or "").strip()
    if action in ("look", "webcam", "ver", "mirar"):
        return look(question)
    if action in ("read", "leer", "ocr", "texto"):
        return read_text()
    if action in ("screen", "pantalla"):
        return look_screen(question)
    return "Acciones: look (webcam), read (leer texto), screen (pantalla)."
