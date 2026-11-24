"""
voice_id.py — Reconocimiento de quién habla para ZAR

Cada persona registra su voz una vez (unos 10 segundos hablando). Después, en cada frase,
ZAR compara la voz con las que conoce y sabe quién le habla.

Usa resemblyzer (ya instalado en ZAR). Las "huellas de voz" se guardan en la carpeta voices/.
El antiguo my_voice.pkl se importa automáticamente como la voz del dueño.
"""

from __future__ import annotations

import json
import pickle
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np
try:
    from actions.owner_profile import OWNER
except Exception:
    OWNER = "the user"


# ═══════════════════════════ AJUSTES ═══════════════════════════
OWNER_NAME        = OWNER      # a quién pertenece el my_voice.pkl antiguo
MATCH_THRESHOLD   = 0.74       # parecido mínimo para reconocer a alguien (0..1). Súbelo si confunde personas
MARGIN            = 0.04       # ventaja mínima sobre la segunda persona más parecida
FIRST_GUESS_S     = 1.6        # segundos de voz para el primer reconocimiento de cada frase
ENROLL_SPEECH_S   = 10         # segundos de voz para registrar a alguien
ENROLL_TIMEOUT_S  = 40
SAMPLE_RATE       = 16000

MIN_RMS, NOISE_RATIO, END_SILENCE_S = 450, 3.0, 0.8


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


VOICES_DIR = _base_dir() / "voices"


# ═══════════════════════════ CODIFICADOR DE VOZ ═══════════════════════════
class ResemblyzerEncoder:
    """Convierte audio (float32, 16 kHz) en una 'huella' de 256 números."""

    def __init__(self):
        from resemblyzer import VoiceEncoder, preprocess_wav
        self._pre = preprocess_wav
        self._enc = VoiceEncoder("cpu", verbose=False)

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        wav = self._pre(audio.astype(np.float32))
        if len(wav) < SAMPLE_RATE * 0.8:          # casi todo era silencio
            return None
        return self._enc.embed_utterance(wav)


# ═══════════════════════════ PERFILES ═══════════════════════════
class VoiceBook:
    def __init__(self, folder: Path = VOICES_DIR):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.meta_path = folder / "voices.json"
        self.voices: dict[str, np.ndarray] = {}
        self.samples: dict[str, int] = {}
        self._load()

    def _load(self):
        meta = {}
        if self.meta_path.exists():
            try:
                meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        for npy in self.folder.glob("*.npy"):
            name = meta.get(npy.stem, {}).get("name", npy.stem)
            self.voices[name] = np.load(npy)
            self.samples[name] = meta.get(npy.stem, {}).get("samples", 1)
        legacy = _base_dir() / "my_voice.pkl"
        if not self.voices and legacy.exists():   # la voz que ya tenía ZAR
            try:
                emb = np.asarray(pickle.load(open(legacy, "rb")), dtype=np.float32).ravel()
                if emb.size == 256:
                    self.add(OWNER_NAME, emb)
                    print(f"[ZAR] Voz de {OWNER_NAME} importada de my_voice.pkl")
            except Exception as e:
                print(f"[ZAR] No pude leer my_voice.pkl: {e}")

    def _key(self, name: str) -> str:
        import re, unicodedata
        k = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z0-9]+", "_", k).strip("_") or "voz"

    def _save_meta(self):
        meta = {self._key(n): {"name": n, "samples": self.samples.get(n, 1)} for n in self.voices}
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, name: str, emb: np.ndarray):
        """Registra o mejora la voz de alguien (si ya existía, promedia con lo anterior)."""
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        if name in self.voices:
            n = self.samples.get(name, 1)
            emb = self.voices[name] * n + emb
            emb = emb / (np.linalg.norm(emb) + 1e-9)
            self.samples[name] = n + 1
        else:
            self.samples[name] = 1
        self.voices[name] = emb.astype(np.float32)
        np.save(self.folder / f"{self._key(name)}.npy", self.voices[name])
        self._save_meta()

    def remove(self, name: str) -> bool:
        match = next((n for n in self.voices if n.lower() == name.lower()), None)
        if not match:
            return False
        self.voices.pop(match); self.samples.pop(match, None)
        (self.folder / f"{self._key(match)}.npy").unlink(missing_ok=True)
        self._save_meta()
        return True

    def identify(self, emb: np.ndarray) -> tuple[str | None, float]:
        if not self.voices:
            return None, 0.0
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        scores = sorted(((float(np.dot(emb, v)), n) for n, v in self.voices.items()), reverse=True)
        best, name = scores[0]
        second = scores[1][0] if len(scores) > 1 else 0.0
        if best >= MATCH_THRESHOLD and best - second >= MARGIN:
            return name, best
        return None, best


# ═══════════════════════════ RECONOCEDOR ═══════════════════════════
class SpeakerID:
    """
    Recibe el audio del micro, detecta frases y dice quién habla.
    on_change(nombre | None) se llama cuando cambia la persona (None = desconocido).
    """

    def __init__(self, encoder=None, book: VoiceBook | None = None, on_change=None, log=print):
        self.book = book or VoiceBook()
        self.on_change = on_change
        self.log = log
        self.current: str | None = None
        self.current_score = 0.0
        self.ready = encoder is not None
        self.encoder = encoder
        self._q: queue.Queue = queue.Queue(maxsize=8)
        self._reset_vad()
        self._noise = 300.0
        self._enroll: dict | None = None
        if encoder is None:
            threading.Thread(target=self._load_encoder, daemon=True, name="voice-load").start()
        threading.Thread(target=self._work, daemon=True, name="voice-id").start()

    def _load_encoder(self):
        try:
            self.encoder = ResemblyzerEncoder()
            self.ready = True
            self.log(f"VOZ: reconocimiento listo · voces conocidas: {', '.join(self.book.voices) or 'ninguna'}")
        except Exception as e:
            self.log(f"VOZ: no pude cargar el modelo de voz ({e}). Instala: pip install resemblyzer")

    def _reset_vad(self):
        self._buf: list[np.ndarray] = []
        self._voiced = 0.0
        self._silent = 0.0
        self._guessed = False
        self._in_speech = False

    # ── audio ──
    def feed(self, frames: np.ndarray):
        x = (frames[:, 0] if frames.ndim == 2 else frames).astype(np.float32)
        if not x.size:
            return
        rms = float(np.sqrt(np.mean(x * x)))
        block = len(x) / SAMPLE_RATE
        thr = max(MIN_RMS, self._noise * NOISE_RATIO)
        loud = rms > thr
        if not loud and not self._in_speech:
            self._noise = 0.95 * self._noise + 0.05 * rms

        if self._enroll is not None:
            self._feed_enroll(x, loud, block)
            return

        if loud:
            self._in_speech = True
            self._voiced += block
            self._silent = 0.0
        elif self._in_speech:
            self._silent += block
        if self._in_speech:
            self._buf.append(x)

        if self._in_speech and not self._guessed and self._voiced >= FIRST_GUESS_S:
            self._guessed = True
            self._submit(np.concatenate(self._buf), "first")
        if self._in_speech and self._silent >= END_SILENCE_S:
            if self._voiced >= FIRST_GUESS_S * 2:            # frase larga: confirmar con más audio
                self._submit(np.concatenate(self._buf), "final")
            self._reset_vad()

    def _submit(self, audio: np.ndarray, kind: str):
        try:
            self._q.put_nowait((audio / 32768.0, kind))
        except queue.Full:
            pass

    def _work(self):
        while True:
            audio, kind = self._q.get()
            if not self.ready:
                continue
            try:
                emb = self.encoder.embed(audio)
            except Exception as e:
                self.log(f"VOZ: error analizando la voz ({e})")
                continue
            if emb is None:
                continue
            name, score = self.book.identify(emb)
            self._set_current(name, score)

    def _set_current(self, name, score: float = 1.0):
        changed = name != self.current
        self.current, self.current_score = name, score
        if changed and self.on_change:
            try:
                self.on_change(name)
            except Exception:
                pass

    # ── registro de una voz nueva ──
    def start_enroll(self, name: str, on_done=None):
        self._enroll = {"name": name, "audio": [], "voiced": 0.0, "start": time.monotonic(), "done": on_done}

    def is_enrolling(self) -> bool:
        return self._enroll is not None

    def _feed_enroll(self, x: np.ndarray, loud: bool, block: float):
        e = self._enroll
        if loud:
            e["voiced"] += block
        e["audio"].append(x)
        timeout = time.monotonic() - e["start"] > ENROLL_TIMEOUT_S
        if e["voiced"] >= ENROLL_SPEECH_S or timeout:
            self._enroll = None
            threading.Thread(target=self._finish_enroll, args=(e, timeout), daemon=True).start()

    def _finish_enroll(self, e: dict, timeout: bool):
        ok, msg = False, ""
        if e["voiced"] < 4:
            msg = "No te he oído lo suficiente. Vuelve a intentarlo hablando un poco más."
        else:
            for _ in range(60):                       # por si el modelo aún está cargando
                if self.ready:
                    break
                time.sleep(0.5)
            try:
                emb = self.encoder.embed(np.concatenate(e["audio"]) / 32768.0)
                if emb is None:
                    msg = "Casi todo era silencio. Repite el registro hablando de forma continua."
                else:
                    self.book.add(e["name"], emb)
                    ok, msg = True, f"Voz de {e['name']} registrada."
                    self._set_current(e["name"])
            except Exception as ex:
                msg = f"No pude registrar la voz: {ex}"
        self.log(f"VOZ: {msg}")
        if e.get("done"):
            e["done"](ok, e["name"], msg)


# ═══════════════════════════ INTEGRACIÓN CON ZAR ═══════════════════════════
_sid: SpeakerID | None = None
_listeners: list = []


def get() -> SpeakerID | None:
    return _sid


def start(on_change=None, log=print) -> SpeakerID:
    """Arranca el reconocimiento (carga el modelo en segundo plano)."""
    global _sid
    if _sid is None:
        _sid = SpeakerID(on_change=on_change, log=log)
    return _sid


def current_speaker() -> str | None:
    return _sid.current if _sid else None


def is_enrolling() -> bool:
    return _sid is not None and _sid.is_enrolling()


def feed(frames: np.ndarray):
    if _sid is not None:
        _sid.feed(frames)


def voice_id_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "who").lower()
    if _sid is None:
        return "El reconocimiento de voz no está activo."
    name = (p.get("name") or "").strip()

    if action == "enroll":
        if not name:
            return "Necesito el nombre de la persona para registrar su voz."
        if _sid.is_enrolling():
            return "Ya estoy registrando una voz."

        def done(ok, who, msg):
            if speak:
                speak(f"[SISTEMA] Registro de voz terminado: {msg} Díselo a {who} en una frase corta"
                      + (" y salúdale por su nombre." if ok else "."))

        _sid.start_enroll(name, done)
        return (f"Registro de voz de {name} en marcha. Pídele en UNA frase corta que hable de forma continua "
                f"unos 10 segundos (por ejemplo, que cuente qué ha hecho hoy) y luego quédate en silencio "
                f"hasta que te avise el sistema.")

    if action == "list":
        names = list(_sid.book.voices)
        return ("Voces que conozco: " + ", ".join(names) + ".") if names else "Todavía no conozco ninguna voz."

    if action == "delete":
        return f"Voz de {name} olvidada." if _sid.book.remove(name) else f"No conozco la voz de {name}."

    if action == "who":
        if not _sid.ready:
            return "El modelo de voz aún está cargando."
        if _sid.current:
            return f"Ahora mismo te habla {_sid.current} (parecido {int(_sid.current_score * 100)} %)."
        return "No reconozco la voz de quien está hablando."

    return "Acción no reconocida: enroll, list, delete o who."
