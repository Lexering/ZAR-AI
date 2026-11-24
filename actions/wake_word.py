"""
wake_word.py — Palabra de activación «ZAR», como JARVIS.

Escucha en local (sin internet y gratis, con Vosk) y solo cuando oye «ZAR» abre la
conversación con Gemini. Después se queda atento unos segundos para que puedas seguir
hablando sin repetir su nombre.

Necesita:  pip install vosk     (el modelo en español, ~40 MB, se descarga solo la primera vez)
"""

from __future__ import annotations

import json
import queue
import threading
import time

import numpy as np

WAKE_WORDS = ["zar", "oye zar", "hola zar", "ey zar", "hey zar", "sar", "oye sar"]
SAMPLE_RATE = 16000


class VoskSpotter:
    """Reconocedor con gramática cerrada: solo entiende el nombre (lo demás es [unk])."""

    def __init__(self, words=WAKE_WORDS, model=None):
        import vosk
        vosk.SetLogLevel(-1)
        if model is None:
            model = vosk.Model(lang="es")         # descarga el modelo pequeño la primera vez
        self.model = model
        grammar = json.dumps(sorted(set(words)) + ["[unk]"], ensure_ascii=False)
        self.rec = vosk.KaldiRecognizer(model, SAMPLE_RATE, grammar)

    def accept(self, pcm: bytes) -> str:
        """Devuelve el texto reconocido hasta ahora (parcial o final)."""
        if self.rec.AcceptWaveform(pcm):
            return json.loads(self.rec.Result()).get("text", "")
        return json.loads(self.rec.PartialResult()).get("partial", "")

    def reset(self):
        try:
            self.rec.Reset()
        except Exception:
            pass


class WakeWord:
    def __init__(self, on_wake, spotter=None, log=print):
        self.on_wake = on_wake
        self.log = log
        self.spotter = spotter
        self.ready = spotter is not None
        self.failed = False
        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._cooldown_until = 0.0
        if spotter is None:
            threading.Thread(target=self._load, daemon=True, name="wake-load").start()
        threading.Thread(target=self._work, daemon=True, name="wake").start()

    def _load(self):
        try:
            self.spotter = VoskSpotter()
            self.ready = True
            self.log("ACTIVACIÓN: di «ZAR» para hablarme")
        except Exception as e:
            self.failed = True
            self.log(f"ACTIVACIÓN: no pude cargar Vosk ({e}). Sigo escuchando siempre. "
                     "Instala con: pip install vosk")

    def phrase_spotter(self, phrases: list[str]):
        """Otro detector local (mismo modelo) para frases concretas, p. ej. «fin del dictado»."""
        if not self.ready or self.spotter is None or not hasattr(self.spotter, "model"):
            return None
        try:
            return VoskSpotter(phrases, model=self.spotter.model)
        except Exception as e:
            self.log(f"ACTIVACIÓN: no pude crear el detector de frases ({e})")
            return None

    def feed(self, frames: np.ndarray):
        try:
            self._q.put_nowait(frames.astype(np.int16).tobytes())
        except queue.Full:
            pass

    def _work(self):
        while True:
            pcm = self._q.get()
            if not self.ready:
                continue
            try:
                text = self.spotter.accept(pcm)
            except Exception:
                continue
            if any(w in text.split() for w in ("zar", "sar")) and time.monotonic() > self._cooldown_until:
                self._cooldown_until = time.monotonic() + 2.0
                self.spotter.reset()
                while not self._q.empty():          # descartar audio ya procesado
                    try: self._q.get_nowait()
                    except queue.Empty: break
                try:
                    self.on_wake()
                except Exception as e:
                    self.log(f"ACTIVACIÓN: error al despertar ({e})")
