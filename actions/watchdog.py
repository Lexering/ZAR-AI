"""
watchdog.py — Partes y avisos de ZAR ("Señor, el traje está al 12 %")

  · Parte diario al encender: hora, tiempo, estado del PC, alertas activas y el bot.
  · Vigilancia continua: batería, disco, memoria, internet y el bot de trading.
  · Alertas de precio: "avísame si Bitcoin baja de 60.000".

Configuración en config/jarvis.json (se crea sola):
  {"city": "Aberdeen", "daily_briefing": true}
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

import requests


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG = _base_dir() / "config" / "jarvis.json"
ALERTS = _base_dir() / "config" / "price_alerts.json"
STATE = _base_dir() / "config" / "watchdog_state.json"

DEFAULT_CONFIG = {"city": "Aberdeen", "daily_briefing": True, "briefing_after_hour": 6,
                  "auto_location": True, "news": True}

CHECK_EVERY_S = 30
PRICE_EVERY_S = 60
BOT_EVERY_S = 600

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
WEATHER = {0: "despejado", 1: "casi despejado", 2: "parcialmente nublado", 3: "nublado", 45: "niebla",
           48: "niebla con escarcha", 51: "llovizna débil", 53: "llovizna", 55: "llovizna intensa",
           61: "lluvia débil", 63: "lluvia", 65: "lluvia fuerte", 66: "lluvia helada", 67: "lluvia helada fuerte",
           71: "nieve débil", 73: "nieve", 75: "nieve fuerte", 77: "granizo fino", 80: "chubascos débiles",
           81: "chubascos", 82: "chubascos fuertes", 85: "chubascos de nieve", 86: "chubascos de nieve fuertes",
           95: "tormenta", 96: "tormenta con granizo", 99: "tormenta fuerte con granizo"}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def config() -> dict:
    cfg = {**DEFAULT_CONFIG, **_read(CONFIG, {})}
    if not CONFIG.exists():
        _write(CONFIG, cfg)
    return cfg


# ═══════════════════════════ DATOS ═══════════════════════════
_loc_cache = {"t": 0.0, "data": {}}


def detect_location() -> dict:
    """Ciudad/país aproximados por la IP (para el tiempo y las noticias de donde estás)."""
    import time as _t
    if _t.time() - _loc_cache["t"] < 3600 and _loc_cache["data"]:
        return _loc_cache["data"]
    try:
        r = requests.get("http://ip-api.com/json/", timeout=6,
                         params={"fields": "status,country,regionName,city"}).json()
        if r.get("status") == "success":
            _loc_cache["data"] = {"city": r.get("city") or "", "region": r.get("regionName") or "",
                                  "country": r.get("country") or ""}
            _loc_cache["t"] = _t.time()
    except Exception:
        pass
    return _loc_cache["data"]


def top_news(place: str = "", country: str = "") -> str:
    """3-4 titulares importantes de hoy (mundo + país) con búsqueda real de Google (vía Gemini)."""
    try:
        from google.genai import types
        from actions import documents
        client = documents._gemini()
        hoy = datetime.now().strftime("%d/%m/%Y")
        donde = f" Incluye alguna de {country}." if country else ""
        prompt = (f"Dame los 3 o 4 titulares de noticias MÁS IMPORTANTES de hoy {hoy} que alguien debería saber "
                  f"(mundo y actualidad general).{donde} Una línea por titular, en español, sin enlaces ni numeración, "
                  "solo el titular, concretos y verificados.")
        cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
        for model in documents.TEXT_MODELS:
            try:
                r = client.models.generate_content(model=model, contents=prompt, config=cfg)
                t = (r.text or "").strip()
                if t:
                    return t
            except Exception:
                continue
    except Exception:
        pass
    return ""


def weather(city: str) -> str:
    try:
        g = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": city, "count": 1, "language": "es"}, timeout=6).json()
        loc = (g.get("results") or [None])[0]
        if not loc:
            return f"No encuentro la ciudad {city}."
        f = requests.get("https://api.open-meteo.com/v1/forecast", timeout=6, params={
            "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto", "forecast_days": 1,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"}).json()
        c, d = f["current"], f["daily"]
        estado = WEATHER.get(int(c["weather_code"]), "tiempo variable")
        lluvia = (d.get("precipitation_probability_max") or [None])[0]
        txt = (f"En {loc['name']}: {round(c['temperature_2m'])} grados, {estado}, viento {round(c['wind_speed_10m'])} km/h; "
               f"hoy entre {round(d['temperature_2m_min'][0])} y {round(d['temperature_2m_max'][0])} grados")
        if lluvia is not None:
            txt += f", {int(lluvia)} % de probabilidad de lluvia"
        return txt + "."
    except Exception as e:
        return f"No pude consultar el tiempo ({e.__class__.__name__})."


def pc_status() -> str:
    try:
        import psutil
    except ImportError:
        return ""
    parts = []
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat is not None:
        parts.append(f"batería al {int(bat.percent)} %" + (" cargando" if bat.power_plugged else ""))
    du = psutil.disk_usage("C:\\" if os.name == "nt" else "/")
    parts.append(f"{du.free / 1e9:.0f} GB libres en disco")
    parts.append(f"memoria al {int(psutil.virtual_memory().percent)} %")
    return "PC: " + ", ".join(parts) + "."


def binance_price(symbol: str) -> float:
    r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol.upper()}, timeout=6)
    r.raise_for_status()
    return float(r.json()["price"])


def normalize_symbol(s: str) -> str:
    s = (s or "").upper().replace("/", "").replace("-", "").replace(" ", "")
    names = {"BITCOIN": "BTC", "ETHEREUM": "ETH", "ETHER": "ETH", "SOLANA": "SOL", "RIPPLE": "XRP",
             "CARDANO": "ADA", "DOGECOIN": "DOGE", "BNB": "BNB"}
    s = names.get(s, s)
    if not any(s.endswith(q) and len(s) > len(q) for q in ("USDT", "USDC", "FDUSD", "EUR", "BTC")):
        s += "USDT"            # BTC → BTCUSDT, pero BTCUSDT o ETHBTC se quedan igual
    return s


# ═══════════════════════════ PARTE DIARIO ═══════════════════════════
def briefing_facts(with_news: bool | None = None) -> str:
    cfg = config()
    now = datetime.now()
    hoy = f"{DIAS[now.weekday()]} {now.day} de {MESES[now.month - 1]}"
    loc = detect_location() if cfg.get("auto_location", True) else {}
    city = loc.get("city") or cfg.get("city", "Aberdeen")
    facts = [f"Hoy es {hoy}, son las {now:%H:%M}.", weather(city), pc_status()]
    alerts = _read(ALERTS, [])
    if alerts:
        facts.append(f"Hay {len(alerts)} alertas de precio activas.")
    want_news = cfg.get("news", True) if with_news is None else with_news
    if want_news:
        news = top_news(city, loc.get("country", ""))
        if news:
            facts.append("Titulares de hoy:\n" + news)
    return " ".join(f for f in facts if f)


def briefing_instruction(first_of_day: bool) -> str:
    bot = ""
    saludo = "Es el primer arranque del día: saluda" if first_of_day else "Saluda"
    facts = briefing_facts()
    return (f"[SISTEMA — parte] {saludo} al estilo JARVIS y da un parte con estos datos: {facts}{bot} "
            "Primero una frase de saludo con la fecha y el tiempo, luego el estado del equipo en una frase, y si hay "
            "«Titulares de hoy», léelos como 3-4 noticias breves. Tono elegante, sin inventar nada.")


def should_brief_today() -> bool:
    cfg = config()
    if not cfg.get("daily_briefing", True):
        return False
    st = _read(STATE, {})
    today = date.today().isoformat()
    if st.get("last_briefing") == today or datetime.now().hour < int(cfg.get("briefing_after_hour", 6)):
        return False
    st["last_briefing"] = today
    _write(STATE, st)
    return True


# ═══════════════════════════ ALERTAS DE PRECIO ═══════════════════════════
def add_price_alert(symbol: str, condition: str, value: float | None = None, percent: float | None = None) -> str:
    sym = normalize_symbol(symbol)
    price = binance_price(sym)
    cond = (condition or "").lower()
    if cond in ("move", "mueve", "movimiento") or (percent and not value):
        pct = abs(float(percent or 3))
        item = {"symbol": sym, "op": "move", "pct": pct, "base": price}
        txt = f"Te aviso si {sym} se mueve un {pct:g} % desde {price:,.2f}."
    else:
        if value is None:
            raise ValueError("Falta el precio de la alerta.")
        op = "above" if cond in ("above", "sube", "supera", "por encima", "mayor") else "below"
        item = {"symbol": sym, "op": op, "value": float(value)}
        txt = (f"Te aviso si {sym} {'sube por encima de' if op == 'above' else 'baja de'} {float(value):,.2f}. "
               f"Ahora está en {price:,.2f}.")
    alerts = _read(ALERTS, [])
    alerts.append(item)
    _write(ALERTS, alerts)
    return txt


def list_price_alerts() -> str:
    alerts = _read(ALERTS, [])
    if not alerts:
        return "No hay alertas de precio."
    out = []
    for i, a in enumerate(alerts, 1):
        if a["op"] == "move":
            out.append(f"{i}) {a['symbol']} si se mueve un {a['pct']:g} %")
        else:
            out.append(f"{i}) {a['symbol']} {'por encima de' if a['op'] == 'above' else 'por debajo de'} {a['value']:,.2f}")
    return "Alertas: " + "; ".join(out) + "."


def remove_price_alerts(symbol: str = "") -> str:
    alerts = _read(ALERTS, [])
    if symbol:
        sym = normalize_symbol(symbol)
        keep = [a for a in alerts if a["symbol"] != sym]
    else:
        keep = []
    _write(ALERTS, keep)
    return f"Eliminadas {len(alerts) - len(keep)} alertas."


def check_prices(get_price=binance_price, notify_fn=None):
    alerts = _read(ALERTS, [])
    if not alerts:
        return
    from actions import notify
    notify_fn = notify_fn or (lambda t: notify.alert(t, important=True, key=f"price-{t}", cooldown_s=60))
    prices, keep, changed = {}, [], False
    for a in alerts:
        try:
            p = prices.get(a["symbol"]) or prices.setdefault(a["symbol"], get_price(a["symbol"]))
        except Exception:
            keep.append(a)
            continue
        if a["op"] == "above" and p >= a["value"]:
            notify_fn(f"{a['symbol']} ha superado {a['value']:,.2f}: ahora está en {p:,.2f}.")
            changed = True
        elif a["op"] == "below" and p <= a["value"]:
            notify_fn(f"{a['symbol']} ha bajado de {a['value']:,.2f}: ahora está en {p:,.2f}.")
            changed = True
        elif a["op"] == "move" and abs(p / a["base"] - 1) * 100 >= a["pct"]:
            ch = (p / a["base"] - 1) * 100
            notify_fn(f"{a['symbol']} se ha movido un {ch:+.1f} %: de {a['base']:,.2f} a {p:,.2f}.")
            a["base"] = p                     # se rearma desde el nuevo precio
            keep.append(a)
            changed = True
        else:
            keep.append(a)
    if changed:
        _write(ALERTS, keep)


# ═══════════════════════════ VIGILANCIA DEL SISTEMA ═══════════════════════════
class Monitor:
    def __init__(self, bot_status=None, log=print):
        self.bot_status = bot_status        # función que devuelve el estado del bot (texto)
        self.log = log
        self._net_fails = 0
        self._net_down_since = None
        self._ram_high = 0
        self._last_price = 0.0
        self._last_bot = time.time()        # no comprobar el bot nada más arrancar

    def internet_ok(self) -> bool:
        for host in (("1.1.1.1", 53), ("8.8.8.8", 53)):
            try:
                socket.create_connection(host, timeout=3).close()
                return True
            except OSError:
                continue
        return False

    def check_once(self):
        from actions import notify
        try:
            import psutil
            bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            if bat is not None and not bat.power_plugged:
                if bat.percent <= 10:
                    notify.alert(f"Batería al {int(bat.percent)} %. Conecte el cargador ya.", important=True,
                                 key="bat10", cooldown_s=600)
                elif bat.percent <= 20:
                    notify.alert(f"Batería al {int(bat.percent)} %.", key="bat20", cooldown_s=1800)
            du = psutil.disk_usage("C:\\" if os.name == "nt" else "/")
            if du.free < 8e9 or du.percent > 95:
                notify.alert(f"Queda poco espacio en el disco: {du.free / 1e9:.1f} GB libres.", key="disk",
                             cooldown_s=6 * 3600)
            self._ram_high = self._ram_high + 1 if psutil.virtual_memory().percent > 92 else 0
            if self._ram_high >= 4:
                notify.alert("La memoria lleva un rato por encima del 92 %. Algo está consumiendo demasiado.",
                             key="ram", cooldown_s=3600)
        except ImportError:
            pass

        if self.internet_ok():
            if self._net_down_since and self._net_fails >= 3:
                mins = (time.time() - self._net_down_since) / 60
                notify.alert(f"Internet ha vuelto tras {mins:.0f} minutos caído.", key="net-up", cooldown_s=60)
            self._net_fails, self._net_down_since = 0, None
        else:
            self._net_fails += 1
            self._net_down_since = self._net_down_since or time.time()
            if self._net_fails == 3:
                notify.say("Se ha caído la conexión a internet.")      # Telegram no llegaría

        if time.time() - self._last_price >= PRICE_EVERY_S:
            self._last_price = time.time()
            try:
                check_prices()
            except Exception as e:
                self.log(f"AVISOS: error comprobando precios ({e})")

        if self.bot_status and os.getenv("ALBERTO_BOT_TOKEN") and time.time() - self._last_bot >= BOT_EVERY_S:
            self._last_bot = time.time()
            try:
                txt = str(self.bot_status() or "")
                bad = any(w in txt.lower() for w in ("error", "no responde", "timeout", "401", "detenido", "stopped"))
            except Exception as e:
                txt, bad = str(e), True
            if bad:
                notify.alert(f"El bot de trading da problemas: {txt[:140]}", important=True, key="bot",
                             cooldown_s=3600)

    def run_forever(self):
        while True:
            try:
                self.check_once()
            except Exception as e:
                self.log(f"AVISOS: {e}")
            time.sleep(CHECK_EVERY_S)


_monitor: Monitor | None = None


def start(bot_status=None, log=print):
    global _monitor
    if _monitor is None:
        _monitor = Monitor(bot_status, log)
        threading.Thread(target=_monitor.run_forever, daemon=True, name="watchdog").start()
    return _monitor


# ═══════════════════════════ HERRAMIENTAS ═══════════════════════════
def briefing_tool(parameters: dict, player=None, speak=None) -> str:
    return briefing_instruction(first_of_day=False).replace("[SISTEMA — parte] Saluda al estilo JARVIS y da",
                                                           "Da")


def price_alert_tool(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "add").lower()
    try:
        if action == "list":
            return list_price_alerts()
        if action in ("remove", "delete"):
            return remove_price_alerts(p.get("symbol", ""))
        if action == "price":
            sym = normalize_symbol(p.get("symbol", "BTC"))
            return f"{sym} está ahora en {binance_price(sym):,.2f}."
        return add_price_alert(p.get("symbol", ""), p.get("condition", ""), p.get("value"), p.get("percent"))
    except Exception as e:
        return f"No pude con la alerta: {e}"
