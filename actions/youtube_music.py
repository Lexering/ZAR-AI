"""
youtube_music.py — Música de YouTube en segundo plano para ZAR

Sin abrir el navegador: yt-dlp busca la canción y saca solo el audio, y VLC lo reproduce.
Al poner una canción se crea una cola "radio" con canciones parecidas (el Mix de YouTube).

Necesita:   VLC instalado (64 bits)          →  winget install VideoLAN.VLC
            Deno (o Node.js) para YouTube    →  winget install DenoLand.Deno
            pip install -U "yt-dlp[default]" python-vlc
"""

from __future__ import annotations

import os
import random
import sys
import threading
import time
from pathlib import Path

# ═══════════════════════════ AJUSTES ═══════════════════════════
DEFAULT_VOLUME = 60      # volumen inicial de la música (0-100)
DUCK_FACTOR    = 0.30    # mientras ZAR habla, la música baja a este porcentaje
RADIO_SIZE     = 25      # cuántas canciones parecidas añadir a la cola
VOLUME_STEP    = 10

VLC_PATHS = [r"C:\Program Files\VideoLAN\VLC", r"C:\Program Files (x86)\VideoLAN\VLC"]


# ═══════════════════════════ YOUTUBE (yt-dlp) ═══════════════════════════
class YouTube:
    """Busca canciones y obtiene la dirección del audio. Separado para poder probarlo."""

    def _ydl(self, **extra):
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "format": "bestaudio/best", "noplaylist": True,
                # YouTube exige un motor de JavaScript: usa Deno o, si no está, Node.js
                "js_runtimes": {"deno": {}, "node": {}}}
        opts.update(extra)
        return yt_dlp.YoutubeDL(opts)

    def search(self, query: str, n: int = 1) -> list[dict]:
        if query.startswith("http"):
            if "list=" in query:
                return self.playlist(query)
            with self._ydl() as y:
                info = y.extract_info(query, download=False)
            return [{"id": info["id"], "title": info.get("title") or query}]
        with self._ydl(extract_flat=True) as y:
            info = y.extract_info(f"ytsearch{n}:{query}", download=False)
        return [{"id": e["id"], "title": e.get("title") or ""} for e in info.get("entries") or [] if e.get("id")]

    def playlist(self, url: str, limit: int = 100) -> list[dict]:
        with self._ydl(extract_flat=True, noplaylist=False, playlistend=limit) as y:
            info = y.extract_info(url, download=False)
        return [{"id": e["id"], "title": e.get("title") or ""} for e in info.get("entries") or [] if e.get("id")]

    def radio(self, video_id: str) -> list[dict]:
        """El 'Mix' de YouTube: canciones parecidas a la que suena."""
        url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
        return [t for t in self.playlist(url, RADIO_SIZE + 1) if t["id"] != video_id]

    def stream_url(self, video_id: str) -> tuple[str, str]:
        with self._ydl() as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return info["url"], info.get("title") or ""


# ═══════════════════════════ AUDIO (VLC) ═══════════════════════════
class VlcBackend:
    def __init__(self):
        if os.name == "nt":
            for p in VLC_PATHS:
                if Path(p).exists():
                    os.add_dll_directory(p)
                    os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
                    break
        import vlc
        self.vlc = vlc
        self.inst = vlc.Instance("--no-video", "--quiet", "--network-caching=1500")
        self.mp = self.inst.media_player_new()
        self._on_end = None
        self.mp.event_manager().event_attach(vlc.EventType.MediaPlayerEndReached, lambda e: self._ended())
        self.mp.event_manager().event_attach(vlc.EventType.MediaPlayerEncounteredError, lambda e: self._ended())

    def _ended(self):
        if self._on_end:
            self._on_end()

    def on_end(self, cb):
        self._on_end = cb

    def play(self, url: str):
        self.mp.set_media(self.inst.media_new(url))
        self.mp.play()

    def pause(self):  self.mp.set_pause(1)
    def resume(self): self.mp.set_pause(0)
    def stop(self):   self.mp.stop()
    def set_volume(self, v: int): self.mp.audio_set_volume(int(v))
    def position(self) -> float:  return max(self.mp.get_time(), 0) / 1000


# ═══════════════════════════ REPRODUCTOR ═══════════════════════════
class MusicPlayer:
    def __init__(self, yt=None, backend=None, log=print):
        self.yt = yt or YouTube()
        self.backend = backend or VlcBackend()
        self.backend.on_end(self._ended_from_backend)
        self.log = log
        self.queue: list[dict] = []
        self.history: list[dict] = []
        self.current: dict | None = None
        self.state = "stopped"            # stopped | playing | paused | loading
        self.volume = DEFAULT_VOLUME
        self.ducked = False
        self.repeat_one = False
        self.radio_on = True
        self._lock = threading.RLock()
        self._advance = threading.Event()
        self._gen = 0                       # evita que una carga antigua pise a una nueva
        threading.Thread(target=self._watch, daemon=True, name="music-watch").start()
        self._apply_volume()

    # ── volumen y "ducking" ──
    def _apply_volume(self):
        v = self.volume * (DUCK_FACTOR if self.ducked else 1)
        try:
            self.backend.set_volume(round(v))
        except Exception:
            pass

    def duck(self, on: bool):
        if on != self.ducked:
            self.ducked = on
            self._apply_volume()

    def set_volume(self, v: int):
        self.volume = max(0, min(100, int(v)))
        self._apply_volume()

    # ── reproducción ──
    def _start(self, track: dict, gen: int):
        try:
            url, title = self.yt.stream_url(track["id"])
        except Exception as e:
            self.log(f"MÚSICA: no pude cargar «{track.get('title')}»: {e}")
            with self._lock:
                if gen == self._gen:
                    self._advance.set()      # salta a la siguiente
            return
        with self._lock:
            if gen != self._gen:
                return                       # mientras cargaba, pediste otra cosa
            track["title"] = title or track.get("title", "")
            self.backend.play(url)
            self._apply_volume()
            self.state = "playing"
            self.log(f"MÚSICA: ▶ {track['title']}")

    def _play_track(self, track: dict, remember: bool = True):
        with self._lock:
            if remember and self.current:
                self.history.append(self.current)
            self.current = track
            self.state = "loading"
            self._gen += 1
            gen = self._gen
        threading.Thread(target=self._start, args=(track, gen), daemon=True).start()

    def _fill_radio(self, seed_id: str, gen: int):
        try:
            tracks = self.yt.radio(seed_id)
        except Exception as e:
            self.log(f"MÚSICA: no pude crear la radio: {e}")
            return
        with self._lock:
            if gen == self._gen and not self.queue:
                self.queue.extend(tracks)

    def _ended_from_backend(self):
        # VLC avisa desde su propio hilo: aquí no se puede tocar VLC, solo avisar al vigilante
        self._advance.set()

    def _watch(self):
        while True:
            self._advance.wait()
            self._advance.clear()
            time.sleep(0.2)
            with self._lock:
                if self.state in ("stopped", "paused"):
                    continue
                if self.repeat_one and self.current:
                    self._play_track(self.current, remember=False)
                else:
                    self._next_locked()

    def _next_locked(self) -> str:
        if self.queue:
            self._play_track(self.queue.pop(0))
            return f"Siguiente: {self.current['title'] or 'cargando'}."
        self.state = "stopped"
        self.backend.stop()
        return "No hay más canciones en la cola."

    # ── acciones públicas ──
    def play(self, query: str, radio: bool = True) -> str:
        results = self.yt.search(query, 1)
        if not results:
            return f"No he encontrado nada para «{query}»."
        with self._lock:
            if len(results) > 1:             # una playlist
                first, rest = results[0], results[1:]
                self.queue = rest
            else:
                first = results[0]
                self.queue = []
            self._play_track(first)
            gen = self._gen
        name = first.get("title") or query
        if len(results) > 1:
            return f"Poniendo la lista: {len(results)} canciones, empezando por «{name}»."
        if radio and self.radio_on:
            threading.Thread(target=self._fill_radio, args=(first["id"], gen), daemon=True).start()
            return f"Poniendo «{name}» y canciones parecidas a continuación."
        return f"Poniendo «{name}»."

    def add(self, query: str) -> str:
        results = self.yt.search(query, 1)
        if not results:
            return f"No he encontrado «{query}»."
        with self._lock:
            # lo pedido va delante de las canciones automáticas de la radio
            self.queue.insert(0, results[0])
            if self.state == "stopped":
                self._next_locked()
                return f"Poniendo «{results[0].get('title') or query}»."
        return f"«{results[0].get('title') or query}» será la siguiente."

    def pause(self) -> str:
        with self._lock:
            if self.state != "playing":
                return "No está sonando nada."
            self.backend.pause(); self.state = "paused"
        return "Música en pausa."

    def resume(self) -> str:
        with self._lock:
            if self.state == "paused":
                self.backend.resume(); self.state = "playing"
                return "Seguimos."
            if self.state == "stopped" and self.current:
                self._play_track(self.current, remember=False)
                return "Vuelvo a poner la última canción."
        return "No hay nada en pausa."

    def toggle(self) -> str:
        return self.pause() if self.state == "playing" else self.resume()

    def next(self) -> str:
        with self._lock:
            return self._next_locked()

    def previous(self) -> str:
        with self._lock:
            if self.current and self.state == "playing":
                try:
                    if self.backend.position() > 8:      # como en cualquier reproductor: vuelve al inicio
                        self._play_track(self.current, remember=False)
                        return "Desde el principio."
                except Exception:
                    pass
            if not self.history:
                return "No hay canción anterior."
            if self.current:
                self.queue.insert(0, self.current)
            self._play_track(self.history.pop(), remember=False)
        return "Canción anterior."

    def stop(self) -> str:
        with self._lock:
            self._gen += 1
            self.backend.stop(); self.state = "stopped"
        return "Música parada."

    def shuffle(self) -> str:
        with self._lock:
            random.shuffle(self.queue)
        return "Cola mezclada."

    def set_repeat(self, on: bool) -> str:
        self.repeat_one = on
        return "Repitiendo esta canción." if on else "Repetición desactivada."

    def now_playing(self) -> str:
        if not self.current or self.state == "stopped":
            return "No está sonando nada."
        estado = {"paused": " (en pausa)", "loading": " (cargando)"}.get(self.state, "")
        nxt = f" Después: «{self.queue[0]['title']}»." if self.queue and self.queue[0].get("title") else ""
        return f"Suena «{self.current['title']}»{estado}. Volumen {self.volume}.{nxt}"

    def status(self) -> dict:
        return {"state": self.state, "title": (self.current or {}).get("title", ""), "volume": self.volume,
                "queue": len(self.queue)}


# ═══════════════════════════ INTEGRACIÓN CON ZAR ═══════════════════════════
_player: MusicPlayer | None = None
_player_lock = threading.Lock()
_player_error = ""


def _get_player(player=None) -> MusicPlayer | None:
    global _player, _player_error
    with _player_lock:
        if _player is None and not _player_error:
            def log(t):
                print(f"[ZAR] {t}")
                if player is not None and hasattr(player, "write_log"):
                    try: player.write_log(t)
                    except Exception: pass
            try:
                _player = MusicPlayer(log=log)
            except ImportError as e:
                _player_error = (f"Falta una librería ({e.name}). Instala con: pip install -U \"yt-dlp[default]\" python-vlc")
            except Exception as e:
                _player_error = ("No encuentro VLC. Instálalo (64 bits) desde videolan.org o con: "
                                 f"winget install VideoLAN.VLC  — detalle: {e}")
        return _player


def is_active() -> bool:
    return _player is not None and _player.state in ("playing", "paused", "loading")


def duck(on: bool):
    """ZAR llama a esto al empezar/terminar de hablar."""
    if _player is not None:
        _player.duck(on)


def media_key(key: str):
    """Para los gestos: palma = play/pausa, victoria = siguiente, índice = anterior."""
    if _player is None:
        return
    {"playpause": _player.toggle, "nexttrack": _player.next, "prevtrack": _player.previous}.get(key, lambda: None)()


def music_player(parameters: dict, player=None, speak=None) -> str:
    global _player_error
    p = parameters or {}
    action = (p.get("action") or "play").lower()
    query = (p.get("query") or "").strip()

    mp = _get_player(player)
    if mp is None:
        msg = _player_error
        _player_error = ""                 # permite reintentar tras instalar lo que falta
        return msg

    try:
        if action == "play":
            if not query:
                return mp.resume()
            return mp.play(query, radio=p.get("radio", True) is not False)
        if action in ("add", "queue"):
            return mp.add(query) if query else "Dime qué canción añadir."
        if action == "pause":    return mp.pause()
        if action == "resume":   return mp.resume()
        if action == "toggle":   return mp.toggle()
        if action == "next":     return mp.next()
        if action == "previous": return mp.previous()
        if action == "stop":     return mp.stop()
        if action == "shuffle":  return mp.shuffle()
        if action == "repeat":   return mp.set_repeat(p.get("state", True) is not False)
        if action == "current":  return mp.now_playing()
        if action == "volume":
            if p.get("volume") is not None:
                mp.set_volume(p["volume"])
            elif (p.get("direction") or "").lower() == "down":
                mp.set_volume(mp.volume - VOLUME_STEP)
            else:
                mp.set_volume(mp.volume + VOLUME_STEP)
            return f"Volumen de la música al {mp.volume}."
    except Exception as e:
        return f"Error con la música: {e}"
    return "Acción no reconocida."


# ═══════════════════════════ PRUEBA RÁPIDA ═══════════════════════════
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Back in Black AC/DC"
    print(music_player({"action": "play", "query": q}))
    try:
        while True:
            time.sleep(5)
            print(music_player({"action": "current"}))
    except KeyboardInterrupt:
        print(music_player({"action": "stop"}))
