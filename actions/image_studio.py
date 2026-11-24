"""
image_studio.py — ZAR crea y edita imágenes (Gemini «Nano Banana»)

  · Crear:  "ZAR, hazme una imagen de un Lamborghini en Aberdeen de noche"
  · Editar: "quítale el fondo", "ponla en estilo anime", "añade un sombrero"…
            sobre la última imagen, una foto tuya, lo que hay en pantalla o la webcam.
  · Guarda en  Imágenes\\ZAR , la abre y, si quieres, te la manda a Telegram.

Usa tu misma clave de Gemini (config/api_keys.json). La generación de imágenes se cobra
aparte en Gemini (unos céntimos por imagen).
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path

from actions import documents

# Orden de preferencia. Solo se usan los que tu clave tiene de verdad (se consulta a Google).
IMAGE_MODELS = ["gemini-nano-banana-2.1", "gemini-3.1-flash-image", "gemini-3.1-flash-lite-image",
                "gemini-2.5-flash-image", "gemini-3-pro-image", "gemini-3.1-flash-image-preview",
                "gemini-3-pro-image-preview"]
_available: list[str] | None = None
_no_quota_until = 0.0


_img_client = None


def _image_client():
    """A separate Gemini key just for images (config/api_keys.json -> gemini_image_key), else the normal one."""
    global _img_client
    if _img_client is None:
        try:
            cfg = json.loads((documents._base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8-sig"))
            k = (cfg.get("gemini_image_key") or "").strip()
            if k:
                from google import genai
                _img_client = genai.Client(api_key=k)
        except Exception:
            _img_client = None
    return _img_client or documents._gemini()


def _models(client) -> list[str]:
    """Modelos de imagen disponibles para tu clave, en orden de preferencia."""
    global _available
    if _available is None:
        try:
            names = [m.name.split("/")[-1] for m in client.models.list() if "image" in m.name]
            pref = [m for m in IMAGE_MODELS if m in names]
            extra = [m for m in names if m not in pref and "gemini" in m]
            _available = pref + extra or list(IMAGE_MODELS)
        except Exception:
            _available = list(IMAGE_MODELS)
    return _available


def _explain(errors: list[tuple[str, str]]) -> str:
    """Convierte los errores de Google en algo que se entienda."""
    joined = " ".join(e for _, e in errors).lower()
    if "resource_exhausted" in joined or "429" in joined or "quota" in joined:
        import re as _re
        m = _re.search(r"retrydelay'?\"?:\s*'?\"?(\d+)s", joined)
        wait = ""
        if m:
            h = int(m.group(1)) / 3600
            wait = f" Google dice que reintentes en {h:.0f} horas." if h >= 1 else " Reintenta en unos minutos."
        limit0 = "limit: 0" in joined or "limit\": 0" in joined or "free_tier" in joined
        return ("Tu clave de Gemini no tiene cupo para generar imágenes"
                + (" (en el plan gratuito de la API el límite de imágenes es 0)" if limit0 else "")
                + "." + wait + " Para usarlo hay que activar la facturación de la API en aistudio.google.com "
                "(la suscripción de la app de Gemini no cuenta para la API).")
    if "permission" in joined or "403" in joined or "billing" in joined:
        return "Tu clave no tiene permiso para los modelos de imagen: activa la facturación en aistudio.google.com."
    return "Ningún modelo de imagen respondió: " + " | ".join(f"{m}: {e[:150]}" for m, e in errors[-3:])
ASPECTS = {"1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}
ASPECT_WORDS = {"cuadrada": "1:1", "cuadrado": "1:1", "horizontal": "16:9", "panoramica": "21:9",
                "vertical": "9:16", "movil": "9:16", "story": "9:16", "historia": "9:16",
                "fondo de pantalla": "16:9", "wallpaper": "16:9", "retrato": "3:4", "instagram": "4:5"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

_last: Path | None = None          # última imagen creada o editada (para "cámbiale…")


# ═══════════════════════════ CARPETAS ═══════════════════════════
def pictures_dir() -> Path:
    base = None
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                base = Path(os.path.expandvars(winreg.QueryValueEx(k, "My Pictures")[0]))
        except Exception:
            base = None
    base = base or (Path.home() / "Pictures")
    d = base / "ZAR"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_image(query: str = "") -> Path | None:
    """Ruta exacta, 'la última', o búsqueda por palabras en Imágenes, Escritorio y Descargas."""
    q = (query or "").strip().strip('"')
    if q and Path(q).expanduser().exists():
        return Path(q).expanduser()
    roots = [pictures_dir(), pictures_dir().parent, documents.known_folder("desktop"),
             documents.known_folder("downloads")]
    files = []
    for r in roots:
        try:
            files += [f for f in r.iterdir() if f.is_file() and f.suffix.lower() in IMG_EXTS]
        except Exception:
            pass
    if not files:
        return None
    words = [w for w in documents._norm(q).split() if len(w) > 2 and w not in
             {"imagen", "foto", "la", "el", "de", "mi", "que", "esta", "esa", "ultima", "ultimo", "reciente"}]
    if not words:
        return max(files, key=lambda f: f.stat().st_mtime)
    scored = [(sum(w in documents._norm(f.stem) for w in words), f.stat().st_mtime, f) for f in files]
    scored = [s for s in scored if s[0]]
    return max(scored)[2] if scored else None


# ═══════════════════════════ GEMINI ═══════════════════════════
def _aspect(value: str, prompt: str = "") -> str:
    v = (value or "").strip()
    if v in ASPECTS:
        return v
    text = documents._norm(f"{value} {prompt}")
    for word, ar in ASPECT_WORDS.items():
        if word in text:
            return ar
    return "1:1"


def _config(aspect: str | None):
    from google.genai import types
    if not aspect:                                      # editar: conservar el formato de la original
        return types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]), True
    try:
        return types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"],
                                           image_config=types.ImageConfig(aspect_ratio=aspect)), True
    except Exception:                                   # SDK antiguo sin image_config
        return types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]), False


def _extract(resp) -> tuple[bytes | None, str]:
    import base64
    img, text = None, []
    try:
        for part in resp.candidates[0].content.parts or []:
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data and img is None:
                img = base64.b64decode(data) if isinstance(data, str) else bytes(data)
            elif getattr(part, "text", None):
                text.append(part.text.strip())
    except Exception:
        pass
    return img, " ".join(t for t in text if t)


def generate_image(prompt: str, aspect: str | None = "1:1", source: bytes | None = None,
                   source_mime: str = "image/png", client=None) -> tuple[bytes, str, str]:
    """Devuelve (imagen, comentario del modelo, modelo usado)."""
    from google.genai import types
    client = client or _image_client()
    cfg, has_ar = _config(aspect)
    text = prompt if has_ar or aspect in (None, "1:1") else f"{prompt}\n(Formato {aspect}.)"
    contents = [types.Part.from_bytes(data=source, mime_type=source_mime), text] if source else [text]
    global _no_quota_until
    if not source and time.time() < _no_quota_until:
        raise RuntimeError("Gemini sin cupo de imágenes (recordado).")
    errors, refusal = [], ""
    for model in _models(client):
        try:
            r = client.models.generate_content(model=model, contents=contents, config=cfg)
        except Exception as e:
            errors.append((model, str(e)))
            continue
        img, comment = _extract(r)
        if img:
            return img, comment, model
        refusal = comment or "no devolvió ninguna imagen"
        break                                           # el modelo respondió pero sin imagen: no insistir
    if refusal:
        raise RuntimeError(f"Gemini no ha generado la imagen ({refusal[:200]}).")
    msg = _explain(errors)
    if "cupo" in msg or "permiso" in msg:
        _no_quota_until = time.time() + 3600          # no volver a intentarlo durante una hora
    print(f"[ZAR] Imagen: Gemini falló -> {msg[:300]}")
    raise RuntimeError(msg)


# ═══════════════════════════ PLAN B GRATIS (Pollinations) ═══════════════════════════
FREE_FALLBACK = True      # si Gemini no puede (sin cupo), crear con Pollinations.ai (gratis, sin clave)
_SIZES = {"1:1": (1024, 1024), "16:9": (1344, 768), "9:16": (768, 1344), "4:3": (1152, 864),
          "3:4": (864, 1152), "3:2": (1216, 816), "2:3": (816, 1216), "4:5": (896, 1120),
          "5:4": (1120, 896), "21:9": (1536, 656)}


def _english(prompt: str) -> str:
    """Los modelos gratuitos entienden mejor el inglés: se traduce con Gemini (texto, gratis)."""
    try:
        out = documents.generate("Translate this image description to English. Keep every detail. "
                                 "Reply ONLY with the translation:\n" + prompt)
        return out.strip().strip('"') or prompt
    except Exception:
        return prompt


def _hf_token() -> str:
    try:
        cfg = json.loads((documents._base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8"))
        return (cfg.get("huggingface_token") or cfg.get("hf_token") or "").strip()
    except Exception:
        return ""


def _huggingface(prompt: str, w: int, h: int) -> bytes:
    """FLUX.1-schnell on Hugging Face (free account token in config/api_keys.json -> huggingface_token)."""
    import requests
    tok = _hf_token()
    if not tok:
        raise RuntimeError("sin token de Hugging Face")
    url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
    r = requests.post(url, headers={"Authorization": f"Bearer {tok}", "Accept": "image/png"},
                      json={"inputs": prompt, "parameters": {"width": w, "height": h}}, timeout=150)
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
        return r.content
    raise RuntimeError(f"Hugging Face HTTP {r.status_code}: {r.text[:150]}")


def _pollinations(prompt: str, w: int, h: int) -> bytes:
    """Pollinations.ai: free, no key. Anonymous limit: one image every ~45 s per connection."""
    import random
    import urllib.parse
    import requests
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
    last = ""
    for attempt in range(3):
        params = {"width": w, "height": h, "seed": random.randint(1, 10**9), "nologo": "true"}
        try:
            r = requests.get(url, params=params, timeout=150, headers={"User-Agent": "ZAR/1.0"})
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 5000:
                return r.content
            last = f"HTTP {r.status_code}"
            if r.status_code in (402, 429):                     # free limit: wait and retry
                wait = min(int(r.headers.get("Retry-After", "45") or 45), 60)
                time.sleep(wait)
                continue
        except Exception as e:
            last = str(e)
        time.sleep(3)
    raise RuntimeError(f"Pollinations no respondió ({last})")


def free_image(prompt: str, aspect: str | None = "1:1") -> bytes:
    w, h = _SIZES.get(aspect or "1:1", (1024, 1024))
    en = _english(prompt)
    errors = []
    for name, fn in (("Hugging Face", _huggingface), ("Pollinations", _pollinations)):
        try:
            return fn(en, w, h)
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"[ZAR] Imagen: {name} falló -> {e}")
    raise RuntimeError("los servicios gratuitos tampoco respondieron (" + " | ".join(errors) + ")")


def _to_png(data: bytes) -> tuple[bytes, str]:
    """Normaliza a PNG (o JPG si es muy grande) para guardar y reenviar."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue(), ".png"
    except Exception:
        return data, ".png"


def save_image(data: bytes, prompt: str) -> Path:
    global _last
    data, ext = _to_png(data)
    stem = documents.safe_name(" ".join(prompt.split()[:8]), "imagen")
    path = documents.unique_path(pictures_dir(), f"{time.strftime('%Y%m%d')}_{stem}", ext)
    path.write_bytes(data)
    _last = path
    return path


# ═══════════════════════════ FUENTES PARA EDITAR ═══════════════════════════
def _source(which: str, current_file: str | None = None) -> tuple[bytes, str, str] | None:
    """(bytes, mime, descripción) de la imagen a editar."""
    w = documents._norm(which or "")
    if w in ("", "ultima", "last", "anterior", "esa", "esta"):
        if _last and _last.exists():
            return _last.read_bytes(), "image/png", _last.name
        if current_file and Path(current_file).suffix.lower() in IMG_EXTS and Path(current_file).exists():
            p = Path(current_file)
            return p.read_bytes(), _mime(p), p.name
        return None
    if w in ("pantalla", "screen", "captura"):
        from actions import security
        shot = security.screenshot_jpeg()
        return (shot, "image/jpeg", "la pantalla") if shot else None
    if w in ("webcam", "camara", "foto mia", "selfie", "yo"):
        from actions import security
        cam = security.Camera()
        try:
            f = cam.frame()
            return (cam.jpeg(f), "image/jpeg", "la webcam") if f is not None else None
        finally:
            cam.close()
    if w in ("documento", "actual", "current") and current_file:
        p = Path(current_file)
        return (p.read_bytes(), _mime(p), p.name) if p.exists() and p.suffix.lower() in IMG_EXTS else None
    p = find_image(which)
    return (p.read_bytes(), _mime(p), p.name) if p else None


def _mime(p: Path) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".bmp": "image/bmp"}.get(
        p.suffix.lower(), "image/png")


# ═══════════════════════════ HERRAMIENTA ═══════════════════════════
def image_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "create").lower()
    prompt = (p.get("prompt") or "").strip()
    current = getattr(player, "current_file", None) if player is not None else None

    if action == "send":
        if not (_last and _last.exists()):
            return "Todavía no he creado ninguna imagen en esta sesión."
        from actions import telegram_link
        return ("Enviada a tu Telegram." if telegram_link.send(f"🖼 {_last.name}", photo=_last.read_bytes())
                else "No pude enviarla: Telegram no está configurado.")

    if action == "open":
        if _last and _last.exists():
            documents.open_file(_last)
            return f"Abierta: {_last.name}."
        return "No hay ninguna imagen reciente."

    if not prompt:
        return "Dime qué imagen quieres." if action == "create" else "Dime qué cambio quieres hacerle."

    src = None
    if action == "edit":
        src = _source(p.get("source", ""), current)
        if src is None:
            return ("No encuentro la imagen que quieres editar. Dime su nombre, pásamela con «Enviar a → ZAR», "
                    "o di «la pantalla» o «la webcam».")

    aspect = _aspect(p.get("aspect_ratio", ""), prompt) if action == "create" else (p.get("aspect_ratio") if p.get("aspect_ratio") in ASPECTS else None)
    free_note = ""
    try:
        img, comment, model = generate_image(prompt, aspect, src[0] if src else None, src[1] if src else "image/png")
    except Exception as e:
        if src is not None or not FREE_FALLBACK or "no ha generado" in str(e):
            extra = (" (Editar fotos solo funciona con Gemini; crear imágenes nuevas sí funciona gratis.)"
                     if src is not None else "")
            return f"No pude crear la imagen: {e}{extra}"
        try:
            img, comment, model = free_image(prompt, aspect), "", "pollinations"
            free_note = " (Hecha con el servicio gratuito porque Gemini no tiene cupo de imágenes.)"
        except Exception as e2:
            return f"No pude crear la imagen: {e} Y {e2}."
    path = save_image(img, prompt)
    if p.get("open", True):
        documents.open_file(path)
    sent = ""
    if p.get("telegram"):
        from actions import telegram_link
        sent = " Y te la he mandado a Telegram." if telegram_link.send(f"🖼 {prompt[:200]}", photo=path.read_bytes()) else ""
    what = f"Editada «{src[2]}»" if src else "Imagen creada"
    return f"{what} y guardada en Imágenes\\ZAR como {path.name}.{sent}{free_note}" + (f" Nota del modelo: {comment[:200]}" if comment else "")


# Para Telegram: crear/editar y devolver los bytes
def make_for_telegram(prompt: str, source: bytes | None = None) -> tuple[bytes, Path]:
    aspect = None if source else _aspect("", prompt)
    try:
        img, _, _ = generate_image(prompt, aspect, source, "image/jpeg")
    except Exception:
        if source is not None or not FREE_FALLBACK:
            raise
        img = free_image(prompt, aspect)
    path = save_image(img, prompt)
    return path.read_bytes(), path
