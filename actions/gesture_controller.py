"""
gesture_controller.py — Control por gestos estilo Iron Man para ZAR

Usa la API nueva de MediaPipe (Tasks · GestureRecognizer), que funciona con
cualquier versión reciente de mediapipe. El modelo (~8 MB) se descarga solo
la primera vez en la carpeta models/.

Dos modos (cambias entre ellos con 🤟 mantenido 1 s, o por voz):

  🎵 MULTIMEDIA                         🖱️ RATÓN (Iron Man)
  ✋ palma quieta    → play / pausa      ☝ índice arriba   → mover el cursor
  👍 pulgar arriba   → subir volumen     🤏 pellizco         → clic
  👎 pulgar abajo    → bajar volumen     🤏 pellizco largo   → arrastrar
  ✌ victoria        → siguiente pista   🤏🤏 dos pellizcos  → doble clic
  ☝ índice arriba   → pista anterior    ✌ subir/bajar     → scroll
  ✊ puño            → silenciar         👍 pulgar arriba   → cambiar ventana
  ✋→ barrido        → pista sig./ant.   ✋→ barrido        → atrás / adelante
"""

from __future__ import annotations

import math
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

# ═══════════════════════════ AJUSTES ═══════════════════════════
CAMERA_INDEX        = 0        # 0 = webcam principal. Prueba 1 si tienes varias.
MIRROR              = True     # efecto espejo (más natural)
START_MODE          = "media"  # "media" o "mouse"
MIN_CONFIDENCE      = 0.60     # confianza mínima para aceptar un gesto

HOLD_S              = 0.45     # cuánto mantener un gesto para que cuente
MODE_SWITCH_HOLD_S  = 1.0      # cuánto mantener 🤟 para cambiar de modo
COOLDOWN_S          = 0.9      # pausa entre dos acciones iguales
REPEAT_S            = 0.22     # repetición del volumen mientras mantienes 👍/👎

CURSOR_SMOOTHING    = 0.35     # 0..1 · más bajo = más suave, más alto = más rápido
CURSOR_BOX          = 0.18     # margen del encuadre que no se usa (más alto = menos brazo)
PINCH_ON            = 0.33     # distancia pulgar-índice (relativa a la mano) para pellizco
PINCH_OFF           = 0.45     # distancia para soltar (histéresis, evita parpadeos)
DRAG_AFTER_S        = 0.45     # pellizco mantenido más que esto = arrastrar
DOUBLE_CLICK_S      = 0.40     # dos pellizcos en este tiempo = doble clic
SCROLL_GAIN         = 1800     # sensibilidad del scroll

SWIPE_DIST          = 0.22     # cuánto desplazar la palma (fracción del ancho)
SWIPE_WINDOW_S      = 0.35     # en cuánto tiempo

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
             "gesture_recognizer/float16/latest/gesture_recognizer.task")


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


MODEL_PATH = _base_dir() / "models" / "gesture_recognizer.task"

NOMBRES = {
    "Open_Palm": "palma", "Closed_Fist": "puño", "Thumb_Up": "pulgar arriba",
    "Thumb_Down": "pulgar abajo", "Victory": "victoria", "Pointing_Up": "índice",
    "ILoveYou": "cuernos 🤟", "None": "",
}


# ═══════════════════════════ GEOMETRÍA DE LA MANO ═══════════════════════════
# Índices de los 21 puntos de MediaPipe
WRIST, THUMB_TIP, INDEX_MCP, INDEX_PIP, INDEX_TIP = 0, 4, 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, RING_TIP, PINKY_PIP, PINKY_TIP = 14, 16, 18, 20


def _dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def hand_size(lm) -> float:
    return max(_dist(lm[WRIST], lm[MIDDLE_MCP]), 1e-6)


def pinch_ratio(lm) -> float:
    """Distancia pulgar-índice relativa al tamaño de la mano (no depende de la distancia a la cámara)."""
    return _dist(lm[THUMB_TIP], lm[INDEX_TIP]) / hand_size(lm)


def finger_up(lm, tip, pip) -> bool:
    return lm[tip].y < lm[pip].y


def is_pointing(lm) -> bool:
    """Índice estirado, corazón, anular y meñique doblados."""
    return (finger_up(lm, INDEX_TIP, INDEX_PIP)
            and not finger_up(lm, MIDDLE_TIP, MIDDLE_PIP)
            and not finger_up(lm, RING_TIP, RING_PIP)
            and not finger_up(lm, PINKY_TIP, PINKY_PIP))


def is_two_fingers(lm) -> bool:
    return (finger_up(lm, INDEX_TIP, INDEX_PIP) and finger_up(lm, MIDDLE_TIP, MIDDLE_PIP)
            and not finger_up(lm, RING_TIP, RING_PIP) and not finger_up(lm, PINKY_TIP, PINKY_PIP))


def map_to_screen(x: float, y: float, sw: int, sh: int, box: float = CURSOR_BOX) -> tuple[int, int]:
    """Convierte la posición del dedo (0..1) en píxeles, usando solo la zona central del encuadre."""
    nx = min(max((x - box) / (1 - 2 * box), 0.0), 1.0)
    ny = min(max((y - box) / (1 - 2 * box), 0.0), 1.0)
    # nunca tocar las esquinas exactas (pyautogui lo considera parada de emergencia)
    return int(min(max(nx * sw, 3), sw - 4)), int(min(max(ny * sh, 3), sh - 4))


# ═══════════════════════════ MOTOR DE GESTOS ═══════════════════════════
class GestureEngine:
    """
    Recibe en cada fotograma (gesto, puntos de la mano, instante) y decide qué hacer.
    No sabe nada de la cámara: así se puede probar sin webcam.
    """

    def __init__(self, keys, screen_size: tuple[int, int], log=print, on_mode=None, mode=START_MODE):
        self.k = keys                     # objeto tipo pyautogui
        self.sw, self.sh = screen_size
        self.log = log
        self.on_mode = on_mode
        self.mode = mode
        self._reset_hold()
        self._last_fire: dict[str, float] = {}
        self._palm_track: deque = deque()
        self._cursor: tuple[float, float] | None = None
        self._pinching = False
        self._pinch_start = 0.0
        self._dragging = False
        self._last_click = 0.0
        self._scroll_y: float | None = None
        self.last_action = ""

    # ── utilidades ──
    def _reset_hold(self, name: str = ""):
        self._hold_name, self._hold_start, self._hold_fired = name, 0.0, False

    def _cool(self, key: str, now: float, wait: float = COOLDOWN_S) -> bool:
        if now - self._last_fire.get(key, -99) < wait:
            return False
        self._last_fire[key] = now
        return True

    def _do(self, label: str, fn, *args):
        try:
            fn(*args)
        except Exception as e:                      # un fallo de teclado/ratón no debe matar el hilo
            self.log(f"GESTOS: error en {label}: {e}")
            return
        self.last_action = label
        self.log(f"GESTO: {label}")

    def set_mode(self, mode: str):
        if mode not in ("media", "mouse") or mode == self.mode:
            return
        self._release_drag()
        self.mode = mode
        self._cursor = None
        self.log(f"GESTOS: modo {'ratón' if mode == 'mouse' else 'multimedia'}")
        if self.on_mode:
            self.on_mode(mode)

    def _release_drag(self):
        if self._dragging:
            self._do("soltar", self.k.mouseUp)
            self._dragging = False
        self._pinching = False

    # ── entrada principal ──
    def update(self, gesture: str, score: float, lm, now: float):
        if lm is None:                               # no hay mano
            self._release_drag()
            self._reset_hold()
            self._palm_track.clear()
            self._scroll_y = None
            return
        if score < MIN_CONFIDENCE:
            gesture = "None"

        # 🤟 mantenido → cambiar de modo (en ambos modos)
        if gesture == "ILoveYou":
            if self._hold_name != "ILoveYou":
                self._reset_hold("ILoveYou"); self._hold_start = now
            elif not self._hold_fired and now - self._hold_start >= MODE_SWITCH_HOLD_S:
                self._hold_fired = True
                self.set_mode("mouse" if self.mode == "media" else "media")
            return

        if self._swipe(gesture, lm, now):
            return

        if self.mode == "mouse":
            self._mouse(gesture, lm, now)
        else:
            self._media(gesture, now)

    # ── barridos con la palma ──
    def _swipe(self, gesture: str, lm, now: float) -> bool:
        if gesture != "Open_Palm" or self._pinching:
            self._palm_track.clear()
            return False
        x = lm[WRIST].x
        self._palm_track.append((now, x))
        while self._palm_track and now - self._palm_track[0][0] > SWIPE_WINDOW_S:
            self._palm_track.popleft()
        dx = x - self._palm_track[0][1]
        if abs(dx) < SWIPE_DIST or not self._cool("swipe", now, 0.8):
            return False
        self._palm_track.clear()
        self._reset_hold("swipe"); self._hold_fired = True     # que la palma no dispare además play/pausa
        derecha = dx > 0
        if self.mode == "mouse":
            if derecha:
                self._do("barrido → adelante", self.k.hotkey, "alt", "right")
            else:
                self._do("barrido ← atrás", self.k.hotkey, "alt", "left")
        else:
            if derecha:
                self._do("barrido → siguiente pista", self.k.press, "nexttrack")
            else:
                self._do("barrido ← pista anterior", self.k.press, "prevtrack")
        return True

    # ── modo multimedia ──
    def _media(self, gesture: str, now: float):
        if gesture != self._hold_name:
            self._reset_hold(gesture); self._hold_start = now
            return
        held = now - self._hold_start

        if gesture in ("Thumb_Up", "Thumb_Down"):                    # volumen con repetición
            if held >= HOLD_S and self._cool(gesture, now, REPEAT_S):
                if gesture == "Thumb_Up":
                    self._do("subir volumen", self.k.press, "volumeup")
                else:
                    self._do("bajar volumen", self.k.press, "volumedown")
            return

        acciones = {
            "Open_Palm":   ("play / pausa", "playpause"),
            "Victory":     ("siguiente pista", "nexttrack"),
            "Pointing_Up": ("pista anterior", "prevtrack"),
            "Closed_Fist": ("silenciar / activar sonido", "volumemute"),
        }
        if gesture in acciones and not self._hold_fired and held >= HOLD_S:
            self._hold_fired = True
            if self._cool(gesture, now):
                label, key = acciones[gesture]
                self._do(label, self.k.press, key)

    # ── modo ratón (Iron Man) ──
    def _mouse(self, gesture: str, lm, now: float):
        ratio = pinch_ratio(lm)

        # Pellizco con histéresis: entra con PINCH_ON, sale con PINCH_OFF
        if not self._pinching and ratio < PINCH_ON:
            self._pinching, self._pinch_start = True, now
        elif self._pinching and ratio > PINCH_OFF:
            if self._dragging:
                self._release_drag()
            else:
                # Cada pellizco es un clic; dos seguidos Windows los entiende como doble clic
                doble = now - self._last_click < DOUBLE_CLICK_S
                self._do("doble clic" if doble else "clic", self.k.click)
                self._last_click = now
            self._pinching = False

        if self._pinching and not self._dragging and now - self._pinch_start >= DRAG_AFTER_S:
            self._dragging = True
            self._do("arrastrar", self.k.mouseDown)

        # Cursor: sigue el nudillo del índice, que apenas se mueve al pellizcar.
        # Mientras pellizcas (sin arrastrar) el cursor se congela para que el clic sea preciso.
        moving = self._dragging or (is_pointing(lm) and not self._pinching)
        if moving:
            p = lm[INDEX_MCP]
            tx, ty = map_to_screen(p.x, p.y, self.sw, self.sh)
            if self._cursor is None:
                self._cursor = (tx, ty)
            else:
                a = CURSOR_SMOOTHING
                self._cursor = (self._cursor[0] + a * (tx - self._cursor[0]),
                                self._cursor[1] + a * (ty - self._cursor[1]))
            try:
                self.k.moveTo(int(self._cursor[0]), int(self._cursor[1]))
            except Exception as e:
                self.log(f"GESTOS: error moviendo el cursor: {e}")

        # Scroll con dos dedos (✌) subiendo o bajando
        if gesture == "Victory" or is_two_fingers(lm):
            y = (lm[INDEX_TIP].y + lm[MIDDLE_TIP].y) / 2
            if self._scroll_y is not None:
                amount = int((self._scroll_y - y) * SCROLL_GAIN)
                if abs(amount) >= 15:
                    self._do("scroll " + ("arriba" if amount > 0 else "abajo"), self.k.scroll, amount)
                    self._scroll_y = y
            else:
                self._scroll_y = y
        else:
            self._scroll_y = None

        # 👍 mantenido → cambiar de ventana
        if gesture == "Thumb_Up":
            if self._hold_name != "Thumb_Up":
                self._reset_hold("Thumb_Up"); self._hold_start = now
            elif not self._hold_fired and now - self._hold_start >= HOLD_S:
                self._hold_fired = True
                if self._cool("alttab", now):
                    self._do("cambiar de ventana", self.k.hotkey, "alt", "tab")
        elif self._hold_name == "Thumb_Up":
            self._reset_hold()


# ═══════════════════════════ CÁMARA + MEDIAPIPE ═══════════════════════════
_thread: threading.Thread | None = None
_stop = threading.Event()
_ready = threading.Event()      # se activa cuando la cámara ya funciona o cuando ha fallado
_engine: GestureEngine | None = None
_state = {"preview": False, "error": ""}


def _ensure_model() -> bytes:
    if not MODEL_PATH.exists() or MODEL_PATH.stat().st_size < 1_000_000:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODEL_PATH.with_suffix(".part")
        with urllib.request.urlopen(MODEL_URL, timeout=60) as r, open(tmp, "wb") as f:
            f.write(r.read())
        tmp.replace(MODEL_PATH)
    return MODEL_PATH.read_bytes()      # por bytes: evita fallos con rutas raras en Windows


def _draw_hud(cv2, frame, lm, engine: GestureEngine, gesture: str):
    h, w = frame.shape[:2]
    cian = (255, 220, 0)
    if lm is not None:
        conexiones = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
                      (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18),
                      (18, 19), (19, 20), (0, 17)]
        pts = [(int(p.x * w), int(p.y * h)) for p in lm]
        for a, b in conexiones:
            cv2.line(frame, pts[a], pts[b], cian, 2)
        for p in pts:
            cv2.circle(frame, p, 4, (255, 255, 255), -1)
    if engine.mode == "mouse":
        b = CURSOR_BOX
        cv2.rectangle(frame, (int(b * w), int(b * h)), (int((1 - b) * w), int((1 - b) * h)), cian, 1)
    modo = "RATON" if engine.mode == "mouse" else "MULTIMEDIA"
    cv2.putText(frame, f"MODO {modo}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, cian, 2)
    cv2.putText(frame, f"{gesture}  {engine.last_action}", (12, h - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, cian, 2)


def _run(player, speak, mode: str):
    global _engine
    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        import pyautogui
    except ImportError as e:
        _state["error"] = f"Falta una librería: {e}. Instala con: pip install mediapipe opencv-python pyautogui"
        _log(player, "GESTOS: " + _state["error"])
        _ready.set()
        return

    pyautogui.PAUSE = 0               # sin pausas artificiales entre acciones
    pyautogui.FAILSAFE = True

    try:
        model = _ensure_model()
    except Exception as e:
        _state["error"] = f"No pude descargar el modelo de gestos: {e}"
        _log(player, "GESTOS: " + _state["error"])
        _ready.set()
        return

    options = vision.GestureRecognizerOptions(
        base_options=mp_python.BaseOptions(model_asset_buffer=model),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.6,
        min_hand_presence_confidence=0.6,
        min_tracking_confidence=0.5,
    )

    cam = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(CAMERA_INDEX)
    if not cam.isOpened():
        _state["error"] = "No puedo abrir la webcam. ¿La está usando otra aplicación?"
        _log(player, "GESTOS: " + _state["error"])
        _ready.set()
        return
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    def on_mode(m):
        if speak:
            speak("[SISTEMA] El control por gestos ha cambiado a modo "
                  + ("ratón" if m == "mouse" else "multimedia")
                  + ". Díselo al usuario en una frase muy corta.")

    class _Keys:
        """Las teclas multimedia van a la música de YouTube de ZAR si está sonando."""
        def __getattr__(self, name):
            return getattr(pyautogui, name)

        def press(self, key):
            music = sys.modules.get("actions.youtube_music")
            if key in ("playpause", "nexttrack", "prevtrack") and music is not None and music.is_active():
                music.media_key(key)
            else:
                pyautogui.press(key)

    _engine = GestureEngine(_Keys(), pyautogui.size(), log=lambda t: _log(player, t),
                            on_mode=on_mode, mode=mode)
    _log(player, f"GESTOS: activo en modo {'ratón' if mode == 'mouse' else 'multimedia'}")
    _ready.set()

    t0 = time.monotonic()
    last_ts = -1
    with vision.GestureRecognizer.create_from_options(options) as recognizer:
        while not _stop.is_set():
            ok, frame = cam.read()
            if not ok:
                time.sleep(0.05)
                continue
            if MIRROR:
                frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ts = int((time.monotonic() - t0) * 1000)
            if ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts
            result = recognizer.recognize_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)

            lm, gesture, score = None, "None", 0.0
            if result.hand_landmarks:
                lm = result.hand_landmarks[0]
                if result.gestures and result.gestures[0]:
                    gesture = result.gestures[0][0].category_name or "None"
                    score = result.gestures[0][0].score
            _engine.update(gesture, score, lm, time.monotonic())

            if _state["preview"]:
                _draw_hud(cv2, frame, lm, _engine, NOMBRES.get(gesture, gesture))
                cv2.imshow("ZAR · Gestos (Q para ocultar)", frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    _state["preview"] = False
                    cv2.destroyAllWindows()

    _engine._release_drag()
    cam.release()
    try:
        cv2.destroyAllWindows()
    except Exception:
        pass
    _log(player, "GESTOS: desactivado")


def _log(player, text: str):
    print(f"[ZAR] {text}")
    if player is not None and hasattr(player, "write_log"):
        try:
            player.write_log(text)
        except Exception:
            pass


GUIA = (
    "Modo multimedia: palma quieta = play/pausa, pulgar arriba/abajo = volumen, "
    "victoria = siguiente pista, índice = pista anterior, puño = silenciar, "
    "barrido con la palma = pista siguiente/anterior. "
    "Modo ratón: índice estirado = mover cursor, pellizco = clic, dos pellizcos = doble clic, "
    "pellizco mantenido = arrastrar, dos dedos subiendo/bajando = scroll, "
    "pulgar arriba = cambiar de ventana, barrido = atrás/adelante. "
    "Cuernos (🤟) mantenidos 1 segundo = cambiar de modo."
)


# ═══════════════════════════ PUNTO DE ENTRADA (herramienta) ═══════════════════════════
def gesture_controller(parameters: dict, player=None, speak=None) -> str:
    global _thread
    params = parameters or {}
    action = (params.get("action") or "start").lower()
    mode = (params.get("mode") or "").lower()
    mode = {"raton": "mouse", "ratón": "mouse", "multimedia": "media", "music": "media"}.get(mode, mode)
    preview = params.get("preview")

    running = _thread is not None and _thread.is_alive()

    if action == "stop":
        if not running:
            return "El control por gestos ya estaba apagado."
        _stop.set()
        _thread.join(timeout=3)
        return "Control por gestos desactivado."

    if action == "status":
        if not running:
            return "Control por gestos apagado. " + (_state["error"] or "")
        modo = "ratón" if _engine and _engine.mode == "mouse" else "multimedia"
        return f"Control por gestos activo en modo {modo}. {GUIA}"

    if action == "guide":
        return GUIA

    if action == "mode":
        if not running:
            action = "start"
        elif mode in ("media", "mouse"):
            _engine.set_mode(mode)
            return f"Modo {'ratón' if mode == 'mouse' else 'multimedia'} activado."
        else:
            return "Indica el modo: multimedia o ratón."

    if action == "preview":
        _state["preview"] = bool(preview) if preview is not None else not _state["preview"]
        if not running:
            action = "start"
        else:
            return "Vista de la cámara " + ("activada." if _state["preview"] else "ocultada.")

    if action == "start":
        if preview is not None:
            _state["preview"] = bool(preview)
        if running:
            if mode in ("media", "mouse") and _engine:
                _engine.set_mode(mode)
            return "El control por gestos ya está activo. " + GUIA
        _stop.clear()
        _ready.clear()
        _state["error"] = ""
        start_mode = mode if mode in ("media", "mouse") else START_MODE
        _thread = threading.Thread(target=_run, args=(player, speak, start_mode), daemon=True)
        _thread.start()
        _ready.wait(timeout=90)          # la primera vez descarga el modelo
        if _state["error"]:
            return _state["error"]
        return (f"Control por gestos activado en modo {'ratón' if start_mode == 'mouse' else 'multimedia'}. "
                + GUIA)

    return "Acción no reconocida. Usa start, stop, status, mode, preview o guide."
