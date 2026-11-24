"""
security.py — Seguridad de ZAR

  · Bloqueo por ausencia: la webcam comprueba si sigues delante; si te vas, bloquea el PC.
  · Modo vigilancia: si alguien toca el PC mientras estás fuera, hace foto con la webcam
    y captura de pantalla, te las manda a Telegram, bloquea el PC y te llama
    por teléfono contándote los detalles (ver phone_call.py).
  · Red: avisa si se conecta a tu wifi un dispositivo que no conoce.
"""

from __future__ import annotations

import io
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ═══════════════════════════ AJUSTES ═══════════════════════════
CAMERA_INDEX       = 0
AWAY_LOCK_S        = 60      # sin verte ni tocar el PC este tiempo → bloquear
GUARD_GRACE_S      = 15      # tiempo para irte después de activar la vigilancia
NETWORK_EVERY_S    = 15 * 60
GUARD_USE_FACE     = False   # True = también salta si la webcam ve una cara (incluida la suya al volver)


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


KNOWN_DEVICES = _base_dir() / "config" / "known_devices.json"
INTRUDERS_DIR = _base_dir() / "logs" / "intrusos"


def lock_pc():
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.LockWorkStation()


def active_window() -> str:
    """Programa y título de la ventana que estaba en primer plano."""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        n = u32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        try:
            import psutil
            exe = psutil.Process(pid.value).name().rsplit(".", 1)[0]
        except Exception:
            pass
        if exe.lower() in ("explorer", "") and not title:
            return "el escritorio"
        if title and exe and exe.lower() not in title.lower():
            return f"{exe} ({title[:60]})"
        return (title or exe)[:80]
    except Exception:
        return ""


def _ago(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 1:
        return "menos de un minuto"
    if m < 60:
        return f"{m} minuto{'s' if m != 1 else ''}"
    h, m = divmod(m, 60)
    return f"{h} hora{'s' if h != 1 else ''}" + (f" y {m} minutos" if m else "")


def incident_details(why: str, when, window: str, people: int, photo: bool, shot: bool,
                     n: int, armed_for: float) -> list[str]:
    """Frases del aviso, de la más importante a la menos (la llamada gratuita corta las últimas)."""
    hora = time.strftime("%H:%M", when)
    parts = [f"A las {hora}, {why} en tu ordenador."]
    if people > 1:
        parts.append(f"La webcam ve a {people} personas.")
    elif people == 1:
        parts.append("La webcam ve a una persona delante.")
    elif people == 0:
        parts.append("La webcam no ve a nadie de frente, puede que se haya apartado.")
    if window:
        parts.append(f"Tenía abierto {window}.")
    parts.append("He bloqueado el equipo.")
    sent = [x for x, ok in (("una foto de la webcam", photo), ("una captura de pantalla", shot)) if ok]
    if sent:
        parts.append("Te he mandado " + " y ".join(sent) + " por Telegram.")
    parts.append(f"Es el aviso número {n}. La vigilancia llevaba activa {_ago(armed_for)}.")
    return parts


def _calls_ready() -> bool:
    try:
        from actions import phone_call
        return phone_call.configured()
    except Exception:
        return False


def _gestures_running() -> bool:
    g = sys.modules.get("actions.gesture_controller")
    return bool(g and getattr(g, "_thread", None) and g._thread.is_alive())


# ═══════════════════════════ CÁMARA ═══════════════════════════
FACE_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
                  "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite")
OPENCV_FIX = ("OpenCV está mal instalado. Arréglalo con: pip uninstall -y opencv-python "
              "opencv-python-headless opencv-contrib-python opencv-contrib-python-headless "
              "y luego: pip install opencv-contrib-python")


class _MPFaces:
    """Detector de caras de MediaPipe (por si OpenCV no trae el de caras)."""

    def __init__(self):
        import urllib.request
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        path = Path(__file__).resolve().parent.parent / "models" / "blaze_face_short_range.tflite"
        if not path.exists() or path.stat().st_size < 50_000:
            path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(FACE_MODEL_URL, timeout=60) as r:
                path.write_bytes(r.read())
        self.mp = mp
        self.det = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_buffer=path.read_bytes()),
            min_detection_confidence=0.6))

    def count(self, rgb) -> int:
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        return len(self.det.detect(img).detections)


class Camera:
    def __init__(self):
        import cv2
        if not hasattr(cv2, "VideoCapture"):
            raise RuntimeError(OPENCV_FIX)
        self.cv2 = cv2
        self.cap = None
        self._face = None            # se carga solo cuando hace falta (vigilancia de presencia)
        self._face_kind = None

    def _load_face(self):
        if self._face_kind is not None:
            return
        cv2 = self.cv2
        try:
            path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self._face = cv2.CascadeClassifier(path)
            if self._face.empty():
                raise RuntimeError("cascada vacía")
            self._face_kind = "cv2"
            return
        except Exception:
            pass
        try:
            self._face = _MPFaces()
            self._face_kind = "mp"
        except Exception:
            self._face_kind = "none"

    def open(self) -> bool:
        if self.cap is None:
            api = self.cv2.CAP_DSHOW if os.name == "nt" else 0
            self.cap = self.cv2.VideoCapture(CAMERA_INDEX, api) if api else self.cv2.VideoCapture(CAMERA_INDEX)
        return self.cap.isOpened()

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def frame(self):
        if not self.open():
            return None
        ok, f = None, None
        for _ in range(3):                 # descartar fotogramas viejos del búfer
            ok, f = self.cap.read()
        return f if ok else None

    def can_see_faces(self) -> bool:
        self._load_face()
        return self._face_kind in ("cv2", "mp")

    def faces(self, frame) -> int:
        """Número de caras. -1 si no hay detector de caras disponible."""
        self._load_face()
        if self._face_kind == "cv2":
            g = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
            return len(self._face.detectMultiScale(g, 1.2, 5, minSize=(60, 60)))
        if self._face_kind == "mp":
            return self._face.count(self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB))
        return -1

    def jpeg(self, frame) -> bytes:
        ok, buf = self.cv2.imencode(".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else b""


def screenshot_jpeg() -> bytes:
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True).convert("RGB")
        img.thumbnail((1920, 1920))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return buf.getvalue()
    except Exception:
        return b""


# ═══════════════════════════ BLOQUEO POR AUSENCIA ═══════════════════════════
class PresenceLock:
    def __init__(self, log=print):
        self.log = log
        self.active = False
        self._t = None

    def start(self) -> str:
        if self.active:
            return "El bloqueo por ausencia ya está activo."
        if _gestures_running():
            return "La cámara la está usando el control por gestos. Desactívalo primero."
        self.active = True
        self._t = threading.Thread(target=self._loop, daemon=True, name="presence")
        self._t.start()
        return f"Bloqueo por ausencia activado: si me quedo {AWAY_LOCK_S} segundos sin verle, bloqueo el PC."

    def stop(self) -> str:
        self.active = False
        return "Bloqueo por ausencia desactivado."

    def _loop(self):
        from actions import notify
        try:
            cam = Camera()
        except Exception as e:
            self.log(f"SEGURIDAD: no puedo usar la cámara ({e})"); self.active = False; return
        if not cam.can_see_faces():
            self.log("SEGURIDAD: no tengo detector de caras; bloqueo por ausencia desactivado. " + OPENCV_FIX)
            from actions import notify
            notify.alert("No puedo reconocer caras con la webcam, así que he desactivado el bloqueo por ausencia.",
                         key="noface")
            self.active = False
            return
        last_seen = time.time()
        locked = False
        try:
            while self.active:
                f = cam.frame()
                if f is None:
                    self.log("SEGURIDAD: la webcam no responde"); time.sleep(5); continue
                seen = cam.faces(f) > 0
                touching = notify.idle_seconds() < 5
                if seen or touching:
                    last_seen = time.time()
                    locked = False
                elif not locked and time.time() - last_seen > AWAY_LOCK_S and notify.idle_seconds() > AWAY_LOCK_S:
                    self.log("SEGURIDAD: no le veo, bloqueo el PC")
                    lock_pc()
                    locked = True
                time.sleep(2)
        finally:
            cam.close()


# ═══════════════════════════ MODO VIGILANCIA ═══════════════════════════
class Guard:
    def __init__(self, log=print):
        self.log = log
        self.armed = False
        self.events = 0
        self.armed_at = time.time()

    def arm(self) -> str:
        if self.armed:
            return "La vigilancia ya está activa."
        if _gestures_running():
            return "La cámara la está usando el control por gestos. Desactívalo primero."
        self.armed = True
        self.events = 0
        self.armed_at = time.time()
        threading.Thread(target=self._loop, daemon=True, name="guard").start()
        return (f"Modo vigilancia activado. Tiene {GUARD_GRACE_S} segundos para irse; después, si alguien toca "
                "el PC, le haré una foto, se la enviaré a Telegram, bloquearé el equipo"
                + (" y le llamaré para contarle los detalles." if _calls_ready() else "."))

    def disarm(self) -> str:
        if not self.armed:
            return "La vigilancia no estaba activa."
        self.armed = False
        return f"Vigilancia desactivada. Incidentes registrados: {self.events}."

    def _loop(self):
        from actions import notify
        time.sleep(GUARD_GRACE_S)
        if not self.armed:
            return
        try:
            cam = Camera()
        except Exception as e:
            self.log(f"SEGURIDAD: vigilancia sin cámara ({e}); solo detectaré el teclado y el ratón")
            cam = None
        idle_base = notify.idle_seconds()
        last_alert = 0.0
        self.log("SEGURIDAD: vigilancia armada")
        try:
            while self.armed:
                idle = notify.idle_seconds()
                touched = idle < idle_base and idle < 3
                idle_base = idle
                face = False
                frame = cam.frame() if cam else None
                if GUARD_USE_FACE and frame is not None and not touched:
                    face = cam.faces(frame) > 0
                if (touched or face) and time.time() - last_alert > 60:
                    last_alert = time.time()
                    self.events += 1
                    self._incident(cam, frame, "han tocado el teclado o el ratón" if touched else "hay alguien delante")
                time.sleep(1.5)
        finally:
            if cam:
                cam.close()

    def _incident(self, cam, frame, why: str):
        from actions import notify
        when = time.localtime()
        window = active_window()                       # antes de bloquear: qué estaban usando
        if cam is not None:
            frame = cam.frame() if frame is None else frame
        photo = cam.jpeg(frame) if (cam is not None and frame is not None) else b""
        people = -1
        if cam is not None and frame is not None:
            try:
                people = cam.faces(frame)
            except Exception:
                people = -1
        shot = screenshot_jpeg()
        INTRUDERS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", when)
        if photo:
            (INTRUDERS_DIR / f"{stamp}_camara.jpg").write_bytes(photo)
        if shot:
            (INTRUDERS_DIR / f"{stamp}_pantalla.jpg").write_bytes(shot)
        lock_pc()

        parts = incident_details(why, when, window, people, bool(photo), bool(shot), self.events,
                                 time.time() - self.armed_at)
        report = "🚨 VIGILANCIA\n" + "\n".join(f"• {p}" for p in parts)
        self.log("SEGURIDAD: " + " ".join(parts))
        notify.say("Alguien ha tocado el PC en modo vigilancia. Advierte en voz alta, serio y breve, de que el "
                   "equipo está bloqueado, se le ha hecho una foto y el propietario ya está avisado.")
        notify.telegram(report, photo=photo or shot or None)
        if photo and shot:
            notify.telegram("Captura de pantalla en ese momento:", photo=shot)
        self._phone(parts)

    def _phone(self, parts: list[str]):
        try:
            from actions import notify, phone_call
        except Exception:
            return
        if not phone_call.configured():
            return
        spoken = ["Alerta de ZAR."] + parts + ["Di desarmar en Telegram para parar la vigilancia."]
        phone_call.call_async(spoken, log=self.log,
                              on_done=lambda ok, msg: notify.telegram("📞 Te estoy llamando." if ok else f"📞 {msg}"))


# ═══════════════════════════ RED ═══════════════════════════
def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _ping(ip: str):
    flag = ["-n", "1", "-w", "300"] if os.name == "nt" else ["-c", "1", "-W", "1"]
    subprocess.run(["ping", *flag, ip], capture_output=True)


def arp_table() -> dict[str, str]:
    out = subprocess.run(["arp", "-a"], capture_output=True, text=True, errors="ignore").stdout
    devices = {}
    for ip, mac in re.findall(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})", out):
        mac = mac.lower().replace("-", ":")
        if mac.startswith(("ff:ff", "01:00:5e", "33:33")) or ip.endswith(".255"):
            continue
        devices[mac] = ip
    return devices


def scan_network(sweep: bool = True) -> dict[str, str]:
    if sweep:
        net = ipaddress.ip_network(local_ip() + "/24", strict=False)
        with ThreadPoolExecutor(max_workers=64) as ex:
            list(ex.map(_ping, (str(h) for h in net.hosts())))
    return arp_table()


def _hostname(ip: str) -> str:
    try:
        socket.setdefaulttimeout(1.5)
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""
    finally:
        socket.setdefaulttimeout(None)


class NetworkWatch:
    def __init__(self, log=print, scan=scan_network):
        self.log = log
        self.scan = scan
        self.active = False

    def known(self) -> dict:
        try:
            return json.loads(KNOWN_DEVICES.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict):
        KNOWN_DEVICES.parent.mkdir(parents=True, exist_ok=True)
        KNOWN_DEVICES.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(self) -> list[tuple[str, str]]:
        from actions import notify
        found = self.scan()
        known = self.known()
        if not known:                                       # primera vez: todo lo actual es de confianza
            self._save({m: {"ip": ip, "name": _hostname(ip), "first_seen": time.strftime("%Y-%m-%d")}
                        for m, ip in found.items()})
            self.log(f"RED: aprendidos {len(found)} dispositivos de tu red")
            return []
        new = [(m, ip) for m, ip in found.items() if m not in known]
        for mac, ip in new:
            name = _hostname(ip)
            known[mac] = {"ip": ip, "name": name, "first_seen": time.strftime("%Y-%m-%d"), "new": True}
            notify.alert(f"Se ha conectado a tu red un dispositivo nuevo: {name or 'sin nombre'} ({ip}).",
                         important=True, key=f"net-{mac}", cooldown_s=86400)
        if new:
            self._save(known)
        return new

    def start(self):
        if self.active:
            return
        self.active = True

        def loop():
            time.sleep(60)
            while self.active:
                try:
                    self.check()
                except Exception as e:
                    self.log(f"RED: no pude escanear ({e})")
                time.sleep(NETWORK_EVERY_S)
        threading.Thread(target=loop, daemon=True, name="netwatch").start()

    def describe(self) -> str:
        known = self.known()
        if not known:
            return "Todavía no he escaneado la red."
        nuevos = [d for d in known.values() if d.get("new")]
        txt = f"Conozco {len(known)} dispositivos en tu red."
        if nuevos:
            txt += " Nuevos sin confirmar: " + ", ".join(f"{d.get('name') or 'sin nombre'} ({d['ip']})" for d in nuevos) + "."
        return txt

    def trust_all(self) -> str:
        known = self.known()
        for d in known.values():
            d.pop("new", None)
        self._save(known)
        return "Hecho: todos los dispositivos actuales son de confianza."


# ═══════════════════════════ INTEGRACIÓN ═══════════════════════════
presence = PresenceLock()
guard = Guard()
network = NetworkWatch()


def start(log=print):
    presence.log = guard.log = network.log = log
    network.start()


def is_guarding() -> bool:
    return guard.armed


_panicked = False


def panic(on: bool = True, mute_cb=None) -> str:
    """Modo pánico / blindaje: silencia el micro, arma la vigilancia y bloquea el PC."""
    global _panicked
    _panicked = on
    if on:
        try:
            if mute_cb:
                mute_cb(True)
        except Exception:
            pass
        try:
            guard.arm()
        except Exception:
            pass
        try:
            from actions import notify
            notify.telegram("🛑 MODO PÁNICO activado: micrófono silenciado, vigilancia armada y PC bloqueado.")
        except Exception:
            pass
        lock_pc()
        return ("Modo pánico activado: he silenciado el micrófono, armado la vigilancia y bloqueado el equipo. "
                "Di «desactiva el pánico» o usa Telegram para volver a la normalidad.")
    try:
        if mute_cb:
            mute_cb(False)
    except Exception:
        pass
    try:
        guard.disarm()
    except Exception:
        pass
    return "Modo pánico desactivado: todo vuelve a la normalidad."


def is_panicked() -> bool:
    return _panicked


def security_tool(parameters: dict, player=None, speak=None) -> str:
    action = ((parameters or {}).get("action") or "status").lower()
    if action == "presence_on":  return presence.start()
    if action == "presence_off": return presence.stop()
    if action == "guard_on":     return guard.arm()
    if action == "guard_off":    return guard.disarm()
    if action == "lock":
        lock_pc(); return "PC bloqueado."
    if action == "network":
        try:
            network.check()
        except Exception as e:
            return f"No pude escanear la red: {e}"
        return network.describe()
    if action == "test_call":
        from actions import phone_call
        return phone_call.test_call()
    if action == "trust_network":
        return network.trust_all()
    if action == "panic":
        return panic(True)
    if action in ("unpanic", "panic_off"):
        return panic(False)
    if action == "status":
        return (f"Bloqueo por ausencia: {'activo' if presence.active else 'apagado'}. "
                f"Vigilancia: {'armada' if guard.armed else 'apagada'}. "
                f"Llamadas de alerta: {'configuradas' if _calls_ready() else 'sin configurar'}. {network.describe()}")
    return "Acción no reconocida."
