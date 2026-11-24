"""
World Monitor - ZAR's live map of what's happening in the world.

Free, key-less sources:
  - USGS            earthquakes (M2.5+, last 24 h)
  - GDACS           disasters with alert levels (quakes, cyclones, floods, volcanoes, droughts, wildfires)
  - NASA EONET      open natural events (wildfires, severe storms, volcanoes, ice, floods)
  - World news RSS  (BBC, Guardian, Al Jazeera, NPR, Sky) placed on the map by country / city

Used by:
  - the HUD window  /ui/world   (map)       data from GET /world_events
  - the voice tool  world_monitor           (summary, near a place, open map, alerts on/off)
  - a background watcher that warns about big events (voice + Telegram)
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS = BASE_DIR / "config" / "world_monitor.json"
UA = {"User-Agent": "ZAR-WorldMonitor/1.0"}
CACHE_S = 180
WATCH_EVERY_S = 600

_cache = {"t": 0.0, "events": [], "errors": []}
_lock = threading.Lock()
_seen_alerts: set[str] = set()
_watch_started = False

# ───────────────────────── places (for news headlines) ─────────────────────────
# name -> (lat, lon). Countries plus frequent news hotspots. Longest names are matched first.
PLACES = {
    "afghanistan": (33.9, 67.7), "albania": (41.2, 20.2), "algeria": (28.0, 1.7), "angola": (-11.2, 17.9),
    "argentina": (-38.4, -63.6), "armenia": (40.1, 45.0), "australia": (-25.3, 133.8), "austria": (47.5, 14.6),
    "azerbaijan": (40.1, 47.6), "bahrain": (26.0, 50.6), "bangladesh": (23.7, 90.4), "belarus": (53.7, 28.0),
    "belgium": (50.5, 4.5), "bolivia": (-16.3, -63.6), "bosnia": (43.9, 17.7), "brazil": (-14.2, -51.9),
    "bulgaria": (42.7, 25.5), "burkina faso": (12.2, -1.6), "cambodia": (12.6, 104.9), "cameroon": (7.4, 12.4),
    "canada": (56.1, -106.3), "chad": (15.5, 18.7), "chile": (-35.7, -71.5), "china": (35.9, 104.2),
    "colombia": (4.6, -74.3), "congo": (-4.0, 21.8), "costa rica": (9.7, -83.8), "croatia": (45.1, 15.2),
    "cuba": (21.5, -77.8), "cyprus": (35.1, 33.4), "czech": (49.8, 15.5), "denmark": (56.3, 9.5),
    "ecuador": (-1.8, -78.2), "egypt": (26.8, 30.8), "el salvador": (13.8, -88.9), "eritrea": (15.2, 39.8),
    "estonia": (58.6, 25.0), "ethiopia": (9.1, 40.5), "finland": (61.9, 25.7), "france": (46.2, 2.2),
    "georgia": (42.3, 43.4), "germany": (51.2, 10.5), "ghana": (7.9, -1.0), "greece": (39.1, 21.8),
    "greenland": (71.7, -42.6), "guatemala": (15.8, -90.2), "haiti": (18.97, -72.3), "honduras": (15.2, -86.2),
    "hong kong": (22.3, 114.2), "hungary": (47.2, 19.5), "iceland": (64.96, -19.0), "india": (20.6, 79.0),
    "indonesia": (-0.8, 113.9), "iran": (32.4, 53.7), "iraq": (33.2, 43.7), "ireland": (53.4, -8.2),
    "israel": (31.0, 34.9), "italy": (41.9, 12.6), "jamaica": (18.1, -77.3), "japan": (36.2, 138.3),
    "jordan": (30.6, 36.2), "kazakhstan": (48.0, 66.9), "kenya": (-0.02, 37.9), "kosovo": (42.6, 20.9),
    "kuwait": (29.3, 47.5), "laos": (19.9, 102.5), "latvia": (56.9, 24.6), "lebanon": (33.9, 35.9),
    "libya": (26.3, 17.2), "lithuania": (55.2, 23.9), "madagascar": (-18.8, 46.9), "malawi": (-13.3, 34.3),
    "malaysia": (4.2, 101.98), "mali": (17.6, -4.0), "mexico": (23.6, -102.6), "moldova": (47.4, 28.4),
    "mongolia": (46.9, 103.8), "morocco": (31.8, -7.1), "mozambique": (-18.7, 35.5), "myanmar": (21.9, 95.96),
    "nepal": (28.4, 84.1), "netherlands": (52.1, 5.3), "new zealand": (-40.9, 174.9), "nicaragua": (12.9, -85.2),
    "niger": (17.6, 8.1), "nigeria": (9.1, 8.7), "north korea": (40.3, 127.5), "norway": (60.5, 8.5),
    "oman": (21.5, 55.9), "pakistan": (30.4, 69.3), "palestin": (31.9, 35.2), "panama": (8.5, -80.8),
    "paraguay": (-23.4, -58.4), "peru": (-9.2, -75.0), "philippines": (12.9, 121.8), "poland": (51.9, 19.1),
    "portugal": (39.4, -8.2), "qatar": (25.4, 51.2), "romania": (45.9, 24.97), "russia": (61.5, 105.3),
    "rwanda": (-1.9, 29.9), "saudi": (23.9, 45.1), "senegal": (14.5, -14.5), "serbia": (44.0, 21.0),
    "singapore": (1.35, 103.8), "slovakia": (48.7, 19.7), "slovenia": (46.2, 14.99), "somalia": (5.2, 46.2),
    "south africa": (-30.6, 22.9), "south korea": (35.9, 127.8), "south sudan": (6.9, 31.3), "spain": (40.5, -3.7),
    "sri lanka": (7.9, 80.8), "sudan": (12.9, 30.2), "sweden": (60.1, 18.6), "switzerland": (46.8, 8.2),
    "syria": (34.8, 39.0), "taiwan": (23.7, 121.0), "tajikistan": (38.9, 71.3), "tanzania": (-6.4, 34.9),
    "thailand": (15.9, 100.99), "tunisia": (33.9, 9.5), "turkey": (38.96, 35.2), "turkiye": (38.96, 35.2),
    "uganda": (1.4, 32.3), "ukraine": (48.4, 31.2), "united arab emirates": (23.4, 53.8), "uae": (23.4, 53.8),
    "united kingdom": (55.4, -3.4), "uk": (55.4, -3.4), "britain": (55.4, -3.4), "england": (52.4, -1.5),
    "scotland": (56.5, -4.2), "wales": (52.1, -3.8), "united states": (39.8, -98.6), "us": (39.8, -98.6),
    "america": (39.8, -98.6), "uruguay": (-32.5, -55.8), "uzbekistan": (41.4, 64.6), "venezuela": (6.4, -66.6),
    "vietnam": (14.1, 108.3), "yemen": (15.6, 48.5), "zambia": (-13.1, 27.8), "zimbabwe": (-19.0, 29.2),
    # cities / regions often in headlines
    "gaza": (31.42, 34.36), "west bank": (31.95, 35.3), "jerusalem": (31.77, 35.21), "tel aviv": (32.09, 34.78),
    "beirut": (33.89, 35.5), "damascus": (33.51, 36.29), "baghdad": (33.31, 44.36), "tehran": (35.69, 51.39),
    "kyiv": (50.45, 30.52), "kiev": (50.45, 30.52), "kharkiv": (49.99, 36.23), "odesa": (46.48, 30.72),
    "crimea": (45.3, 34.4), "donbas": (48.0, 37.8), "moscow": (55.76, 37.62), "beijing": (39.9, 116.4),
    "shanghai": (31.23, 121.47), "tokyo": (35.68, 139.69), "seoul": (37.57, 126.98), "pyongyang": (39.04, 125.76),
    "delhi": (28.61, 77.21), "mumbai": (19.08, 72.88), "kabul": (34.56, 69.21), "islamabad": (33.68, 73.05),
    "karachi": (24.86, 67.0), "dhaka": (23.81, 90.41), "manila": (14.6, 120.98), "jakarta": (-6.21, 106.85),
    "sydney": (-33.87, 151.21), "london": (51.51, -0.13), "paris": (48.86, 2.35), "berlin": (52.52, 13.4),
    "madrid": (40.42, -3.7), "barcelona": (41.39, 2.17), "rome": (41.9, 12.5), "brussels": (50.85, 4.35),
    "washington": (38.9, -77.04), "new york": (40.71, -74.0), "los angeles": (34.05, -118.24),
    "california": (36.8, -119.4), "texas": (31.0, -100.0), "florida": (27.8, -81.7), "chicago": (41.88, -87.63),
    "mexico city": (19.43, -99.13), "caracas": (10.48, -66.9), "bogota": (4.71, -74.07), "lima": (-12.05, -77.04),
    "sao paulo": (-23.55, -46.63), "buenos aires": (-34.6, -58.38), "cairo": (30.04, 31.24),
    "khartoum": (15.5, 32.56), "darfur": (13.5, 24.0), "nairobi": (-1.29, 36.82), "lagos": (6.52, 3.38),
    "johannesburg": (-26.2, 28.05), "dubai": (25.2, 55.27), "riyadh": (24.71, 46.68), "doha": (25.29, 51.53),
    "istanbul": (41.01, 28.98), "ankara": (39.93, 32.86), "red sea": (20.0, 38.5), "taiwan strait": (24.5, 119.5),
    "south china sea": (12.0, 114.0), "sahel": (14.5, 0.0), "kashmir": (34.08, 74.8), "tigray": (14.0, 38.5),
    "aberdeen": (57.15, -2.09), "edinburgh": (55.95, -3.19), "glasgow": (55.86, -4.25),
}
_PLACE_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in PLACES), key=len, reverse=True)) + r")\b",
                       re.IGNORECASE)
_AMBIGUOUS = {"us", "uk", "america", "georgia", "niger"}   # need capitals / context to count
HIGH_WORDS = re.compile(r"\b(killed|dead|deaths|death toll|explosion|blast|missile|airstrike|air strike|attack|"
                        r"invasion|war|massacre|earthquake|tsunami|hurricane|typhoon|coup|shooting|bomb|"
                        r"evacuat\w*|emergency)\b", re.IGNORECASE)


def locate(text: str):
    """First place mentioned in a headline -> (name, lat, lon) or None."""
    for m in _PLACE_RE.finditer(text or ""):
        word = m.group(1)
        key = word.lower()
        if key in _AMBIGUOUS and not word.isupper() and not word[0].isupper():
            continue
        if key == "us" and word != "US":
            continue
        lat, lon = PLACES[key]
        return word, lat, lon
    return None


# ───────────────────────── sources ─────────────────────────
def _get_json(url: str, timeout: float = 12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _quakes():
    d = _get_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson")
    out = []
    for f in d.get("features", []):
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or [0, 0]
        mag = p.get("mag") or 0
        sev = 3 if mag >= 6.5 else 2 if mag >= 5 else 1
        if p.get("tsunami"):
            sev = max(sev, 3)
        out.append({
            "id": "usgs:" + str(f.get("id")), "type": "earthquake", "severity": sev,
            "title": f"M{mag:.1f} earthquake - {p.get('place') or 'unknown location'}",
            "lat": g[1], "lon": g[0], "ts": (p.get("time") or 0) / 1000,
            "url": p.get("url") or "", "source": "USGS",
            "detail": f"Depth {g[2]:.0f} km" + (" - tsunami warning" if p.get("tsunami") else "") if len(g) > 2 else "",
        })
    return out


GDACS_TYPES = {"EQ": "earthquake", "TC": "cyclone", "FL": "flood", "VO": "volcano", "DR": "drought",
               "WF": "wildfire", "TS": "tsunami"}


def _gdacs():
    try:
        d = _get_json("https://www.gdacs.org/gdacsapi/api/events/geteventlist/MAP")
    except Exception:
        return _gdacs_rss()
    out = []
    for f in d.get("features", []):
        p = f.get("properties") or {}
        g = (f.get("geometry") or {}).get("coordinates") or None
        if not g or not isinstance(g[0], (int, float)):
            continue
        level = (p.get("alertlevel") or "Green").capitalize()
        etype = GDACS_TYPES.get(p.get("eventtype"), "disaster")
        if etype == "earthquake" and level == "Green":
            continue                                    # USGS already shows small quakes
        sev = {"Red": 3, "Orange": 2}.get(level, 1)
        try:
            ts = datetime.fromisoformat((p.get("fromdate") or "").replace("Z", "")).replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            ts = time.time()
        url = ""
        if isinstance(p.get("url"), dict):
            url = p["url"].get("report") or p["url"].get("details") or ""
        out.append({
            "id": f"gdacs:{p.get('eventtype')}{p.get('eventid')}", "type": etype, "severity": sev,
            "title": f"{level} alert: {p.get('name') or p.get('eventname') or etype.title()}",
            "lat": g[1], "lon": g[0], "ts": ts, "url": url, "source": "GDACS",
            "detail": (p.get("description") or p.get("htmldescription") or "")[:200],
        })
    return out


def _gdacs_rss():
    """Fallback: GDACS public RSS feed (always available)."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    req = urllib.request.Request("https://www.gdacs.org/xml/rss.xml", headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        root = ET.fromstring(r.read())
    ns = {"geo": "http://www.w3.org/2003/01/geo/wgs84_pos#", "gdacs": "http://www.gdacs.org"}
    out = []
    for it in root.iter("item"):
        lat, lon = it.findtext("geo:Point/geo:lat", None, ns), it.findtext("geo:Point/geo:long", None, ns)
        if lat is None or lon is None:
            continue
        level = (it.findtext("gdacs:alertlevel", "Green", ns) or "Green").capitalize()
        code = it.findtext("gdacs:eventtype", "", ns)
        etype = GDACS_TYPES.get(code, "disaster")
        if etype == "earthquake" and level == "Green":
            continue
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except Exception:
            ts = time.time()
        out.append({
            "id": f"gdacs:{code}{it.findtext('gdacs:eventid', '', ns)}", "type": etype,
            "severity": {"Red": 3, "Orange": 2}.get(level, 1),
            "title": f"{level} alert: {it.findtext('title') or etype.title()}",
            "lat": float(lat), "lon": float(lon), "ts": ts, "url": it.findtext("link") or "",
            "source": "GDACS", "detail": (it.findtext("description") or "")[:200],
        })
    return out


EONET_TYPES = {"wildfires": "wildfire", "severeStorms": "storm", "volcanoes": "volcano", "floods": "flood",
               "seaLakeIce": "ice", "earthquakes": "earthquake", "landslides": "landslide", "drought": "drought",
               "dustHaze": "dust", "snow": "snow", "tempExtremes": "heat"}


def _eonet():
    d = _get_json("https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=10&limit=150")
    out = []
    for e in d.get("events", []):
        geos = [g for g in e.get("geometry") or [] if g.get("type") == "Point"]
        if not geos:
            continue
        last = geos[-1]
        cat = (e.get("categories") or [{}])[0].get("id", "")
        etype = EONET_TYPES.get(cat, "natural")
        if etype in ("ice", "earthquake"):
            continue
        try:
            ts = datetime.fromisoformat(last["date"].replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = time.time()
        sev = 2 if etype in ("storm", "volcano") else 1
        src = (e.get("sources") or [{}])[0]
        out.append({
            "id": "eonet:" + str(e.get("id")), "type": etype, "severity": sev, "title": e.get("title") or etype,
            "lat": last["coordinates"][1], "lon": last["coordinates"][0], "ts": ts,
            "url": src.get("url") or e.get("link") or "", "source": "NASA EONET", "detail": "",
        })
    return out


NEWS_FEEDS = [("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
              ("The Guardian", "https://www.theguardian.com/world/rss"),
              ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml")]


def _own_news():
    """Used when this ZAR's HUD has no news module."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    items, urls = [], []
    for source, url in NEWS_FEEDS:
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=12) as r:
                root = ET.fromstring(r.read())
            for it in list(root.iter("item"))[:15]:
                try:
                    ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
                except Exception:
                    ts = time.time()
                items.append({"id": len(urls), "title": (it.findtext("title") or "").strip(), "source": source, "ts": ts})
                urls.append(it.findtext("link") or "")
        except Exception:
            continue
    return items, urls


def _news():
    try:
        from actions import hud_bridge as hb
        items = hb.get_news()
        urls = list(hb._news.get("urls") or [])
    except Exception:
        items, urls = _own_news()
    out = []
    for it in items:
        title = it.get("title") or ""
        place = locate(title)
        url = urls[it["id"]] if it.get("id") is not None and it["id"] < len(urls) else ""
        sev = 2 if HIGH_WORDS.search(title) else 1
        ev = {"id": "news:" + str(abs(hash(title)) % 10**10), "type": "news", "severity": sev, "title": title,
              "ts": it.get("ts") or time.time(), "url": url, "source": it.get("source") or "News",
              "detail": f"Location: {place[0]}" if place else "Global"}
        if place:
            ev["lat"], ev["lon"] = place[1], place[2]
        out.append(ev)
    return out


SOURCES = {"USGS": _quakes, "GDACS": _gdacs, "NASA EONET": _eonet, "News": _news}


def get_events(force: bool = False) -> dict:
    with _lock:
        if not force and _cache["events"] and time.time() - _cache["t"] < CACHE_S:
            return {"events": _cache["events"], "errors": _cache["errors"], "updated": _cache["t"]}
    events, errors = [], []
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as ex:
        futures = {name: ex.submit(fn) for name, fn in SOURCES.items()}
        for name, fut in futures.items():
            try:
                events.extend(fut.result(timeout=25))
            except Exception as e:
                errors.append(f"{name}: {e}")
    events.sort(key=lambda e: (e["ts"]), reverse=True)
    with _lock:
        _cache.update(t=time.time(), events=events, errors=errors)
    return {"events": events, "errors": errors, "updated": _cache["t"]}


# ───────────────────────── summaries for the voice tool ─────────────────────────
def _ago(ts: float) -> str:
    m = max(0, int((time.time() - ts) / 60))
    return f"{m} min ago" if m < 60 else f"{m // 60} h ago" if m < 1440 else f"{m // 1440} days ago"


def _dist_km(a, b, c, d) -> float:
    r = math.radians
    h = math.sin(r(c - a) / 2) ** 2 + math.cos(r(a)) * math.cos(r(c)) * math.sin(r(d - b) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def summary(limit: int = 8) -> str:
    evs = get_events()["events"]
    day = time.time() - 86400
    top = sorted([e for e in evs if e["ts"] >= day], key=lambda e: (e["severity"], e["ts"]), reverse=True)[:limit]
    if not top:
        return "World Monitor: nothing notable in the last 24 hours (or the sources are unreachable)."
    quakes = sum(1 for e in evs if e["type"] == "earthquake" and e["ts"] >= day)
    lines = [f"World Monitor - last 24 h: {quakes} earthquakes M2.5+, "
             f"{sum(1 for e in evs if e['source'] == 'GDACS')} disaster alerts, "
             f"{sum(1 for e in evs if e['source'] == 'NASA EONET')} active natural events. Most important:"]
    lines += [f"- {e['title']} ({_ago(e['ts'])}, {e['source']})" for e in top]
    return "\n".join(lines)


def near(place: str, radius_km: float = 1000) -> str:
    loc = None
    p = locate(place or "")
    if p:
        loc = (p[1], p[2], p[0])
    else:
        try:
            import urllib.parse
            d = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&name="
                          + urllib.parse.quote(place))
            r = (d.get("results") or [None])[0]
            if r:
                loc = (r["latitude"], r["longitude"], r.get("name") or place)
        except Exception:
            pass
    if not loc:
        return f"I couldn't find '{place}' on the map."
    evs = [e for e in get_events()["events"] if "lat" in e
           and _dist_km(loc[0], loc[1], e["lat"], e["lon"]) <= radius_km]
    if not evs:
        return f"Nothing reported within {int(radius_km)} km of {loc[2]} right now."
    evs.sort(key=lambda e: (e["severity"], e["ts"]), reverse=True)
    return f"Near {loc[2]} ({int(radius_km)} km):\n" + "\n".join(
        f"- {e['title']} ({_ago(e['ts'])})" for e in evs[:8])


# ───────────────────────── alerts ─────────────────────────
def _settings() -> dict:
    try:
        return {"alerts": True, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
    except Exception:
        return {"alerts": True}


def _save_settings(s: dict):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _watch():
    first = True
    while True:
        try:
            evs = get_events(force=True)["events"]
            fresh = [e for e in evs if e["severity"] >= 3 and e["type"] != "news"
                     and time.time() - e["ts"] < 6 * 3600 and e["id"] not in _seen_alerts]
            for e in fresh:
                _seen_alerts.add(e["id"])
            if fresh and not first and _settings().get("alerts", True):
                from actions.addons_notify import alert as _alert
                for e in fresh[:3]:
                    _alert(f"World Monitor: {e['title']}.", important=True, key=e["id"], cooldown_s=86400)
            first = False                      # don't announce what was already there at start-up
        except Exception as e:
            print(f"[ZAR] World Monitor: {e}")
        time.sleep(WATCH_EVERY_S)


def start():
    global _watch_started
    if _watch_started:
        return
    _watch_started = True
    threading.Thread(target=_watch, daemon=True, name="world-monitor").start()


def world_tool(parameters: dict, player=None, speak=None) -> str:
    a = parameters or {}
    action = (a.get("action") or "summary").lower()
    try:
        if action == "open":
            from actions import addons
            ok = addons.open_page("world")
            return "World Monitor map opened." if ok else "I couldn't open the map window."
        if action == "near":
            return near(a.get("place", ""), float(a.get("radius_km") or 1000))
        if action in ("alerts_on", "alerts_off"):
            s = _settings(); s["alerts"] = action == "alerts_on"; _save_settings(s)
            return "World alerts turned " + ("on." if s["alerts"] else "off.")
        if action == "refresh":
            get_events(force=True)
        return summary()
    except Exception as e:
        return f"World Monitor error: {e}"


TOOL_DECLARATION = {
    "name": "world_monitor",
    "description": (
        "ZAR's live World Monitor: earthquakes (USGS), disaster alerts (GDACS), wildfires / storms / "
        "volcanoes (NASA) and breaking world news on a map. Use when the user asks what's happening in the "
        "world, about earthquakes, disasters, wildfires, storms, crises, or events near a place, or to open "
        "the world map. action: summary (default), near (with place), open (shows the map), refresh, "
        "alerts_on / alerts_off (warnings about big events)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "summary | near | open | refresh | alerts_on | alerts_off"},
            "place": {"type": "STRING", "description": "Country, city or region for action=near"},
            "radius_km": {"type": "NUMBER", "description": "Search radius for action=near (default 1000)"},
        },
        "required": ["action"],
    },
}
