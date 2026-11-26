"""
voice_dictation.py — Dictado por voz a PDF para ZAR

Mientras el dictado está activo, el micrófono deja de ir a la conversación y todo lo que
dices se transcribe (con Gemini) y se va escribiendo en un PDF. Lo ves en directo en el
panel del escritorio.

Comandos de voz durante el dictado:
  «coma», «punto», «dos puntos», «abre/cierra interrogación»…  → signos de puntuación
  «punto y aparte» / «nuevo párrafo»                           → párrafo nuevo
  «borra eso» / «borra la última frase»                        → quita lo último dictado
  «fin del dictado» / «termina el dictado»                     → guarda el PDF y lo abre
                         (lo detecta también sin internet si está la palabra «ZAR» activa)
  Ctrl+Alt+M o el menú del icono → «Terminar dictado»          → termina siempre
  5 minutos sin hablar                                         → se guarda y termina solo
"""

from __future__ import annotations

import io
import queue
import re
import threading
import time
import unicodedata
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000

# Detección de frases por silencio (bloques de 1024 muestras ≈ 64 ms)
MIN_RMS          = 140      # volumen mínimo para considerar que hablas (bajo: portátiles con micro flojo)
NOISE_RATIO      = 2.6      # o este múltiplo del ruido de fondo, lo que sea mayor
START_FRAMES     = 3        # bloques seguidos con voz para empezar una frase
END_SILENCE_S    = 1.1      # silencio que cierra una frase
MAX_UTTERANCE_S  = 25       # corta frases muy largas para no esperar demasiado
MIN_SPEECH_S     = 0.3
PREROLL_FRAMES   = 5

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

TRANSCRIBE_PROMPT = """Transcribe exactamente lo que dice esta persona en español, para un dictado.
Reglas:
- Escribe el texto con ortografía, mayúsculas y tildes correctas.
- Convierte los signos dictados: «coma» → , «punto» → . «dos puntos» → : «punto y coma» → ;
  «abre interrogación»/«cierra interrogación» → ¿ ? «abre exclamación»/«cierra exclamación» → ¡ !
  «abre paréntesis»/«cierra paréntesis» → ( ) «comillas» → «».
- Si dice «punto y aparte» o «nuevo párrafo», escribe un punto (si toca) y después [PARRAFO].
- Si dice «borra eso», «borra la última frase» o similar, escribe solo [BORRAR].
- Si dice «fin del dictado», «termina el dictado», «para el dictado» o similar, escribe [FIN]
  (y antes, el texto que haya dicho, si dijo algo más).
- Si no se entiende nada o solo hay ruido, devuelve una respuesta vacía.
- No añadas comentarios ni comillas alrededor. Solo el texto.
Texto anterior del documento (para mantener la continuidad): «{context}»"""

STOP_PATTERNS = ["fin del dictado", "fin de dictado", "termina el dictado", "terminar el dictado",
                 "terminar dictado", "para el dictado", "parar el dictado", "acaba el dictado",
                 "detener el dictado", "detén el dictado", "finaliza el dictado"]
STOP_RE = re.compile(r"\b(fin|termin[ae]r?|par[ae]r?|acaba|det[eé]n|detener|finaliza)\s+(el\s+|de\s+|del\s+)?dictado",
                     re.IGNORECASE)
UNDO_PATTERNS = ["borra eso", "borrar eso", "borra la ultima frase", "elimina eso", "borra lo ultimo"]
PARA_PATTERNS = ["punto y aparte", "nuevo parrafo"]

IDLE_STOP_S = 5 * 60        # sin oírte hablar este tiempo → se guarda y termina solo
# Frases que un detector LOCAL (Vosk, sin internet) reconoce para terminar aunque falle Gemini
LOCAL_STOP_PHRASES = ["fin del dictado", "fin de dictado", "termina el dictado", "terminar el dictado",
                      "para el dictado", "parar el dictado", "acaba el dictado", "zar para",
                      "zar termina", "zar fin", "zar stop"]
_stop_spotter_factory = None     # lo pone main.py si hay palabra de activación (Vosk)


def set_stop_spotter_factory(fn):
    global _stop_spotter_factory
    _stop_spotter_factory = fn


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def _to_wav(frames: list[np.ndarray]) -> bytes:
    a = np.concatenate(frames).astype(np.float32)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    if 0 < peak < 20000:                        # quiet mic: bring the phrase up so the transcription hears it well
        a *= min(12.0, 26000.0 / peak)
    pcm = np.clip(a, -32768, 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def gemini_transcribe(wav: bytes, context: str) -> str:
    from google.genai import types
    from actions import documents
    part = types.Part.from_bytes(data=wav, mime_type="audio/wav")
    return documents.generate([part, TRANSCRIBE_PROMPT.format(context=context[-300:])])


class Dictation:
    def __init__(self, transcribe=gemini_transcribe, log=print, on_finish=None, title: str = "",
                 folder: Path | None = None, open_when_done: bool = True, target: str = "pdf"):
        self.target = "type" if str(target).lower() in ("type", "escribir", "cursor", "aqui", "aquí") else "pdf"
        self._typed: list[str] = []
        from actions import documents
        self.documents = documents
        self.transcribe = transcribe
        self.log = log
        self.on_finish = on_finish
        self.open_when_done = open_when_done
        now = datetime.now()
        self.title = title or f"Dictado del {now.day} de {MESES[now.month - 1]} de {now.year}"
        folder = folder or documents.output_dir()
        self.path = documents.unique_path(folder, documents.safe_name(self.title, "dictado") +
                                          f"_{now:%H%M}", ".pdf")
        self.started = now
        self.paragraphs: list[list[str]] = [[]]
        self.history: list[int] = []          # para «borra eso»: índice de párrafo de cada frase
        self.active = True
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        # estado del detector de voz
        self._noise = 150.0
        self._buf: list[np.ndarray] = []
        self._pre: list[np.ndarray] = []
        self._loud = 0
        self._silent = 0
        self._in_speech = False
        self._worker = threading.Thread(target=self._work, daemon=True, name="dictation")
        self._worker.start()
        self.last_voice = time.monotonic()
        self._spot_q: queue.Queue = queue.Queue(maxsize=300)
        self._spotter = None
        if _stop_spotter_factory is not None:
            try:
                self._spotter = _stop_spotter_factory(LOCAL_STOP_PHRASES)
            except Exception:
                self._spotter = None
        if self._spotter is not None:
            threading.Thread(target=self._spot_work, daemon=True, name="dictation-stop").start()
        threading.Thread(target=self._idle_watch, daemon=True, name="dictation-idle").start()
        self._save()

    # ── formas de terminar aunque Gemini no entienda «fin del dictado» ──
    def _stop_async(self, why: str):
        if self.active:
            self.log(f"DICTADO: {why}")
            threading.Thread(target=self.stop, daemon=True, name="dictation-end").start()

    def _spot_work(self):
        while self.active:
            try:
                pcm = self._spot_q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                heard = _norm(self._spotter.accept(pcm))
            except Exception:
                continue
            if "dictado" in heard or heard.startswith("zar "):
                self._stop_async(f"oído «{heard}»: termino")
                return

    def _idle_watch(self):
        while self.active:
            time.sleep(5)
            if self.active and time.monotonic() - self.last_voice > IDLE_STOP_S:
                self._stop_async(f"{IDLE_STOP_S // 60} minutos sin oírte: guardo y termino")
                return

    # ── audio del micro (lo llama el hilo de sounddevice) ──
    def feed(self, frames: np.ndarray):
        if not self.active:
            return
        x = frames[:, 0] if frames.ndim == 2 else frames
        x = x.astype(np.int16)
        if self._spotter is not None:
            try:
                self._spot_q.put_nowait(x.tobytes())
            except queue.Full:
                pass
        rms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) if x.size else 0.0
        thr = max(MIN_RMS, self._noise * NOISE_RATIO)
        block_s = len(x) / SAMPLE_RATE

        if not self._in_speech:
            self._pre = (self._pre + [x])[-PREROLL_FRAMES:]
            if rms > thr:
                self._loud += 1
                if self._loud >= START_FRAMES:
                    self._in_speech, self._buf, self._silent = True, list(self._pre), 0
                    self.last_voice = time.monotonic()
            else:
                self._loud = 0
            # background noise: follows quickly down, slowly up (so speech doesn't raise it)
            self._noise = 0.9 * self._noise + 0.1 * rms if rms < self._noise else min(self._noise * 1.003, 3000.0)
            return

        self._buf.append(x)
        self._silent = self._silent + 1 if rms <= thr else 0
        if rms > thr:
            self.last_voice = time.monotonic()
        dur = len(self._buf) * block_s
        if self._silent * block_s >= END_SILENCE_S or dur >= MAX_UTTERANCE_S:
            self._flush_utterance(block_s)

    def _flush_utterance(self, block_s: float = 1024 / SAMPLE_RATE):
        speech_blocks = len(self._buf) - self._silent
        if self._buf and speech_blocks * block_s >= MIN_SPEECH_S:
            self._q.put(_to_wav(self._buf))
        self._buf, self._pre, self._in_speech, self._loud, self._silent = [], [], False, 0, 0

    # ── transcripción en orden ──
    def _context(self) -> str:
        return " ".join(" ".join(p) for p in self.paragraphs[-2:]).strip()

    def _work(self):
        while True:
            wav = self._q.get()
            if wav is None:
                self._q.task_done()
                return
            try:
                text = (self.transcribe(wav, self._context()) or "").strip()
            except Exception as e:
                self.log(f"DICTADO: no pude transcribir una frase ({e})")
                text = ""
            try:
                if text:
                    self._apply(text)
            finally:
                self._q.task_done()

    def _apply(self, text: str):
        text = text.strip().strip('"').strip("«»").strip()
        norm = _norm(text)
        stop = "[FIN]" in text or any(p in norm for p in STOP_PATTERNS)
        if stop:
            # quedarse solo con lo dicho antes del comando
            text = text.split("[FIN]")[0]
            m = STOP_RE.search(text)
            if m:
                text = text[:m.start()]
            text = text.strip(" ,")
            norm = _norm(text)

        if "[BORRAR]" in text or (norm and any(norm == p or norm.startswith(p) for p in UNDO_PATTERNS)):
            with self._lock:
                if self.history:
                    i = self.history.pop()
                    if self.paragraphs[i]:
                        removed = self.paragraphs[i].pop()
                        self.log(f"DICTADO: borrado «{removed[:60]}»")
                    # el párrafo vacío se conserva: así un «punto y aparte» anterior no se pierde
            if self.target == "type" and self._typed:
                _backspace(len(self._typed.pop()))
            self._save()
            text = ""

        if text and self.target == "type":
            for p in PARA_PATTERNS:
                text = re.sub(p.replace("parrafo", "p[aá]rrafo"), "[PARRAFO]", text, flags=re.IGNORECASE)
            for n, piece in enumerate(text.split("[PARRAFO]")):
                if n > 0:
                    _press_enter()
                piece = piece.strip(" ,")
                if piece:
                    chunk = piece + " "
                    _type_text(chunk)
                    self._typed.append(chunk)
            self.log(f"DICTADO: {text.replace('[PARRAFO]', ' ¶ ').strip()}")
            with self._lock:
                self.paragraphs[-1].append(text.replace("[PARRAFO]", " "))
            text = ""

        if text:
            for p in PARA_PATTERNS:      # por si Gemini lo dejó escrito en vez de marcarlo
                text = re.sub(p.replace("parrafo", "p[aá]rrafo"), "[PARRAFO]", text, flags=re.IGNORECASE)
            pieces = text.split("[PARRAFO]")
            with self._lock:
                for n, piece in enumerate(pieces):
                    piece = piece.strip(" ,")
                    if n > 0 and self.paragraphs[-1]:
                        self.paragraphs.append([])
                    if piece:
                        self.paragraphs[-1].append(piece)
                        self.history.append(len(self.paragraphs) - 1)
            shown = text.replace("[PARRAFO]", " ¶ ").strip()
            if shown and shown != "¶":
                self.log(f"DICTADO: {shown}")
            self._save()

        if stop:
            threading.Thread(target=self.stop, daemon=True).start()

    # ── PDF ──
    def text(self) -> str:
        return "\n\n".join(" ".join(p) for p in self.paragraphs if p)

    def word_count(self) -> int:
        return len(self.text().split())

    def _doc(self) -> dict:
        paras = [" ".join(p) for p in self.paragraphs if p]
        return {"title": self.title,
                "subtitle": f"Dictado por voz · {self.started:%d/%m/%Y %H:%M}",
                "sections": [{"heading": "", "paragraphs": paras or ["(Sin texto todavía)"], "bullets": []}]}

    def _save(self):
        if getattr(self, "target", "pdf") == "type":
            return
        try:
            with self._lock:
                doc = self._doc()
            self.documents.write_pdf(doc, self.path, footer=f"Dictado con ZAR · {self.started:%d/%m/%Y}")
        except PermissionError:
            pass            # el PDF está abierto en otro programa: se guardará en el siguiente intento
        except Exception as e:
            self.log(f"DICTADO: no pude guardar el PDF ({e})")

    def stop(self) -> Path:
        with self._lock:
            if not self.active:
                return self.path
            self.active = False
        if self._in_speech:
            self._flush_utterance()
        self._q.put(None)
        self._worker.join(timeout=30)
        self._save()
        words = self.word_count()
        self.log(f"DICTADO: terminado · {words} palabras · {self.path.name}")
        if self.open_when_done and self.target == "pdf":
            self.documents.open_file(self.path)
        if self.on_finish:
            try:
                self.on_finish(self.path, words)
            except Exception:
                pass
        return self.path


# ── escribir donde está el cursor (modo «type») ──
def _type_text(text: str):
    """Pastes the text in the active window (works with accents and ñ, unlike simulated typing)."""
    import pyautogui
    import pyperclip
    try:
        old = pyperclip.paste()
    except Exception:
        old = None
    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    if old is not None:
        try:
            pyperclip.copy(old)
        except Exception:
            pass


def _press_enter():
    import pyautogui
    pyautogui.press("enter")


def _backspace(n: int):
    import pyautogui
    pyautogui.press("backspace", presses=max(0, n), interval=0.005)


# ═══════════════════════════ INTEGRACIÓN CON ZAR ═══════════════════════════
_current: Dictation | None = None


def is_active() -> bool:
    return _current is not None and _current.active


def feed(frames: np.ndarray):
    d = _current
    if d is not None and d.active:
        d.feed(frames)


def _hud(state: bool):
    try:
        from actions import hud_bridge
        hud_bridge.set_dictating(state)
    except Exception:
        pass


def dictation_tool(parameters: dict, player=None, speak=None) -> str:
    global _current
    p = parameters or {}
    action = (p.get("action") or "start").lower()

    def log(t):
        print(f"[ZAR] {t}")
        if player is not None and hasattr(player, "write_log"):
            try: player.write_log(t)
            except Exception: pass

    if action == "start":
        if is_active():
            return f"Ya hay un dictado en marcha ({_current.word_count()} palabras)."

        target = p.get("target") or "pdf"

        def finished(path: Path, words: int):
            _hud(False)
            if speak:
                where = ("escrito donde estaba el cursor" if _current is not None and _current.target == "type"
                         else f"guardado como PDF en {path.name} y abierto en pantalla")
                speak(f"[SISTEMA] El dictado ha terminado: {words} palabras, {where}. Díselo al usuario en una frase corta.")

        try:
            _current = Dictation(log=log, on_finish=finished, title=(p.get("title") or "").strip(), target=target)
        except Exception as e:
            return f"No pude empezar el dictado: {e}"
        _hud(True)
        return ("Dictado iniciado. Di al usuario en UNA frase muy corta que empiece a hablar "
                "(por ejemplo «Adelante, te escucho») y que diga «fin del dictado» (o pulse Ctrl+Alt+M) para terminar. "
                + ("Lo que diga se escribirá donde está el cursor (que lo deje en el documento o chat). " if _current.target == "type" else "")
                + "Después quédate en silencio: lo que diga se escribe, no te lo dice a ti.")

    if action == "stop":
        if not is_active():
            return "No hay ningún dictado en marcha."
        d = _current
        path = d.stop()
        return f"Dictado terminado: {d.word_count()} palabras guardadas en {path}."

    if action == "status":
        if not is_active():
            return "No hay ningún dictado en marcha."
        return f"Dictado en marcha: {_current.word_count()} palabras. Último texto: {_current.text()[-200:]}"

    return "Acción no reconocida: usa start, stop o status."
