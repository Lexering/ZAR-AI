"""
alberto_agents.py — Equipo de agentes GRATUITOS + sistema de aprendizaje de ZAR

Qué hace:
  • Agentes especializados (programador, investigador, traductor, redactor, analista,
    resumidor, general), cada uno con su personalidad y sus modelos preferidos.
  • Solo usa modelos GRATUITOS: OpenCode Zen (*-free, big-pickle), Ollama local,
    OpenRouter (:free) y Gemini (nivel gratuito).
  • Aprende de ti:
      - Guarda qué modelo funciona mejor para cada agente (según tus valoraciones y fallos).
      - Guarda tus correcciones como "lecciones" y las aplica en preguntas parecidas.
      - Guarda tus reglas generales ("responde corto", "háblame de tú"...) y las aplica siempre.
  • Modo equipo: pregunta a 3 modelos gratis a la vez y otro fusiona la mejor respuesta.

Uso desde terminal (modo entrenamiento):
  python actions\\alberto_agents.py                 → chat para entrenar a ZAR
  python actions\\alberto_agents.py --agentes       → qué modelo gratis usa cada agente
  python actions\\alberto_agents.py --stats         → lo que ha aprendido
  python actions\\alberto_agents.py --setup-ollama  → añade tus modelos de Ollama a OpenCode

Requiere opencode_bridge.py en la misma carpeta.
"""

import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
try:
    from actions.owner_profile import OWNER
except Exception:
    OWNER = "the user"


try:
    from actions import opencode_bridge as ob
except ImportError:
    import opencode_bridge as ob

# ─────────────────────────── Archivos ───────────────────────────

LEARN_FILE = Path(ob.WORKDIR) / "alberto_learning.json"
CONFIG_FILE = Path(__file__).with_name("alberto_agents.json")

DEFAULT_CONFIG = {
    "free_only": True,          # solo modelos gratuitos
    "gemini_is_free": True,     # el nivel gratuito de Gemini cuenta como gratis
    "extra_free_models": [],    # modelos que quieras marcar como gratis a mano
    "blocked_models": [],       # modelos que no quieras usar nunca
    "exploration": 0.1,         # 10% de las veces prueba el 2º mejor modelo para seguir aprendiendo
}

# ─────────────────────────── Agentes ───────────────────────────

BASE_SYSTEM = (
    "Eres parte de ZAR, el asistente personal de " + OWNER + ". "
    "Responde en el idioma en que te hablen (por defecto español), de forma clara y directa. "
)

AGENTS = {
    "programador": {
        "desc": "Escribe, revisa y arregla código (Python, JS, Node, FastAPI, bots…).",
        "task": "codigo",
        "keywords": ["código", "codigo", "script", "python", "javascript", "bug", "error", "función",
                     "funcion", "api", "fastapi", "node", "html", "css", "sql", "traceback", "programa",
                     "refactor", "debug", "clase", "endpoint"],
        "prefs": ["qwen3-coder", "coder", "deepseek", "big-pickle", "kimi", "glm", "gpt-oss",
                  "gemini-3", "qwen", "codestral", "llama"],
        "system": "Eres un programador senior. Da código completo y funcional, explica lo justo, "
                  "señala riesgos y cómo probarlo. Si falta información, asume lo razonable y dilo.",
    },
    "investigador": {
        "desc": "Analiza, razona, compara opciones y planifica.",
        "task": "razonamiento",
        "keywords": ["analiza", "compara", "por qué", "razona", "plan", "estrategia", "evalúa",
                     "evalua", "arquitectura", "decide", "pros", "contras", "investiga", "calcula"],
        "prefs": ["deepseek-r1", "nemotron-3-ultra", "nemotron", "gemini-3", "big-pickle", "qwen3", "gpt-oss", "kimi",
                  "glm", "llama"],
        "system": "Eres un analista riguroso. Razona paso a paso, separa hechos de suposiciones, "
                  "da una conclusión clara y los riesgos principales.",
    },
    "traductor": {
        "desc": "Traduce entre idiomas manteniendo tono y registro.",
        "task": "traduccion",
        "keywords": ["traduce", "traducir", "translate", "traducción", "traduccion", "en inglés",
                     "en árabe", "en francés", "al inglés", "al árabe", "al español"],
        "prefs": ["gemini-3", "gemma", "qwen", "mistral", "llama", "big-pickle"],
        "system": "Eres traductor profesional. Devuelve solo la traducción, natural y con el mismo "
                  "tono; añade una nota breve solo si hay una ambigüedad importante.",
    },
    "redactor": {
        "desc": "Emails, posts, textos de marketing, guiones.",
        "task": "creativo",
        "keywords": ["escribe", "redacta", "email", "correo", "post", "marketing", "slogan", "guion",
                     "mensaje", "anuncio", "presentación", "linkedin", "instagram"],
        "prefs": ["gemini-3", "big-pickle", "llama", "mistral", "qwen", "kimi", "gpt-oss"],
        "system": "Eres redactor profesional. Escribe textos listos para enviar, con buen ritmo, "
                  "sin relleno, adaptados al canal y al destinatario.",
    },
    "analista": {
        "desc": "Mercados, cripto, trading y finanzas (análisis, no consejo financiero).",
        "task": "razonamiento",
        "keywords": ["bitcoin", "btc", "eth", "cripto", "crypto", "trading", "mercado", "acciones",
                     "forex", "binance", "precio", "velas", "rsi", "tendencia", "riesgo", "stop loss",
                     "cartera", "futuros"],
        "prefs": ["deepseek", "nemotron-3-ultra", "nemotron", "gemini-3", "qwen3", "big-pickle", "gpt-oss", "llama"],
        "system": "Eres analista cuantitativo. Analiza datos y escenarios con números, gestiona el "
                  "riesgo (stops, tamaño de posición) y recuerda que es análisis, no consejo financiero.",
    },
    "resumidor": {
        "desc": "Resúmenes y respuestas rápidas.",
        "task": "rapido",
        "keywords": ["resume", "resumen", "breve", "corto", "rápido", "rapido", "tl;dr", "en una frase"],
        "prefs": ["flash", "lightning", "lite", "mimo", "gemma", "qwen3:4b", "llama3.2", "big-pickle"],
        "system": "Resume con precisión y brevedad. Viñetas cortas, solo lo importante.",
    },
    "general": {
        "desc": "Todo lo demás.",
        "task": "general",
        "keywords": [],
        "prefs": ["gemini-3", "big-pickle", "qwen3", "llama", "gpt-oss", "kimi", "nemotron", "mistral"],
        "system": "Eres un asistente general útil y conciso.",
    },
}

# ─────────────────────────── Config y memoria ───────────────────────────


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[Agentes] No pude leer {CONFIG_FILE.name}: {e}")
    return cfg


def _empty_memory() -> dict:
    return {"stats": {}, "lessons": [], "rules": [], "history": []}


def load_memory() -> dict:
    try:
        mem = json.loads(LEARN_FILE.read_text(encoding="utf-8"))
        for k, v in _empty_memory().items():
            mem.setdefault(k, v)
        return mem
    except Exception:
        return _empty_memory()


def save_memory(mem: dict):
    LEARN_FILE.parent.mkdir(parents=True, exist_ok=True)
    mem["history"] = mem["history"][-200:]
    mem["lessons"] = mem["lessons"][-500:]
    tmp = LEARN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(LEARN_FILE)


def _stat(mem, agent, model) -> dict:
    return mem["stats"].setdefault(agent, {}).setdefault(
        model, {"good": 0, "bad": 0, "fail": 0, "uses": 0, "avg_s": 0.0})


# ─────────────────────────── Modelos gratis ───────────────────────────


def is_free(model: str, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    if model in cfg.get("extra_free_models", []):
        return True
    prov, _, mid = model.partition("/")
    mid_l = mid.lower()
    if prov in ("ollama", "lmstudio", "local"):
        return True
    if prov == "openrouter":
        return mid_l.endswith(":free")
    if prov == "opencode":
        if "free" in mid_l or mid_l == "big-pickle":
            return True
        cost = ob.model_info(model).get("cost") or {}
        return cost.get("input") == 0 and cost.get("output") == 0
    if prov == "google":
        return bool(cfg.get("gemini_is_free", True))
    return False


def usable_models(cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_config()
    status = ob._load_status()
    out = []
    for m in ob.available_models():
        if not ob._is_chat_model(m) or m in cfg.get("blocked_models", []):
            continue
        if status.get(m, {}).get("ok") is False:
            continue
        if cfg.get("free_only", True) and not is_free(m, cfg):
            continue
        out.append(m)
    return out


def rank_models(agent: str, mem: dict | None = None, cfg: dict | None = None) -> list[str]:
    """Ordena los modelos gratis para un agente: preferencias + lo aprendido."""
    mem = mem or load_memory()
    cfg = cfg or load_config()
    spec = AGENTS[agent]
    prefs = spec["prefs"]
    models = usable_models(cfg)
    stats = mem["stats"].get(agent, {})

    def score(m):
        ml = m.lower()
        idx = next((i for i, p in enumerate(prefs) if p in ml), None)
        prior = 1 - idx / len(prefs) if idx is not None else 0.1
        s = stats.get(m, {})
        good, bad, fail = s.get("good", 0), s.get("bad", 0), s.get("fail", 0)
        learned = (good + 1) / (good + bad + fail + 2)  # 0.5 si no sabe nada
        confidence = min(1.0, (good + bad + fail) / 5)  # cuanto más datos, más pesa lo aprendido
        final = (1 - 0.7 * confidence) * prior + (0.7 * confidence) * learned
        return (final, ob._score(m, spec["task"]))

    ranked = sorted(models, key=score, reverse=True)
    if len(ranked) > 1 and random.random() < cfg.get("exploration", 0.1):
        ranked[0], ranked[1] = ranked[1], ranked[0]  # explorar
    return ranked


# ─────────────────────────── Aprendizaje ───────────────────────────


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9ñ]+", _norm(text)) if len(w) > 3}


def relevant_lessons(agent: str, prompt: str, mem: dict, k: int = 5) -> list[str]:
    pw = _words(prompt)
    scored = []
    for i, les in enumerate(mem["lessons"]):
        overlap = len(pw & _words(les.get("context", "") + " " + les["text"]))
        same_agent = les.get("agent") == agent
        if overlap or (same_agent and les.get("general")):
            scored.append((overlap + (2 if same_agent else 0), i, les["text"]))
    scored.sort(reverse=True)
    return [t for _, _, t in scored[:k]]


def build_system(agent: str, prompt: str, mem: dict) -> str:
    parts = [BASE_SYSTEM + AGENTS[agent]["system"]]
    if mem["rules"]:
        parts.append("Reglas que " + OWNER + " te ha pedido seguir SIEMPRE:\n" +
                     "\n".join(f"- {r}" for r in mem["rules"][-20:]))
    lessons = relevant_lessons(agent, prompt, mem)
    if lessons:
        parts.append("Correcciones que " + OWNER + " te hizo antes en casos parecidos (aplícalas):\n" +
                     "\n".join(f"- {t}" for t in lessons))
    return "\n\n".join(parts)


def pick_agent(prompt: str) -> str:
    text = _norm(prompt)
    best, best_score = "general", 0
    for name, spec in AGENTS.items():
        sc = sum(1 for kw in spec["keywords"] if _norm(kw) in text)
        if sc > best_score:
            best, best_score = name, sc
    if best_score == 0 and len(prompt) < 60:
        return "resumidor" if any(w in text for w in ("resume", "resumen")) else "general"
    return best


# ─────────────────────────── Preguntar ───────────────────────────

_agent_sessions: dict[str, str] = {}


def ask_agent(prompt: str, agent: str | None = None, max_tries: int = 4,
              new_session: bool = False, exclude: list[str] | None = None) -> dict:
    """Pregunta a un agente gratis. Devuelve {'id','text','agent','model','seconds'}."""
    if not ob.ensure_server():
        return {"id": None, "text": "OpenCode no está disponible.", "agent": agent, "model": None}

    agent = agent if agent in AGENTS else pick_agent(prompt)
    mem, cfg = load_memory(), load_config()
    candidates = [m for m in rank_models(agent, mem, cfg) if m not in (exclude or [])][:max_tries]
    if not candidates:
        return {"id": None, "agent": agent, "model": None,
                "text": "No hay modelos gratis disponibles. Ejecuta: python actions\\opencode_bridge.py --probe"}

    system = build_system(agent, prompt, mem)
    errors = []
    for m in candidates:
        if new_session or agent not in _agent_sessions:
            _agent_sessions[agent] = ob._new_session(f"ZAR · {agent}")
            new_session = False
        t0 = time.time()
        try:
            try:
                text = ob.ask_server(prompt, m, system=system, session_id=_agent_sessions[agent])
            except ob.SessionGone:
                _agent_sessions[agent] = ob._new_session(f"ZAR · {agent}")
                text = ob.ask_server(prompt, m, system=system, session_id=_agent_sessions[agent])
            secs = time.time() - t0
            s = _stat(mem, agent, m)
            s["uses"] += 1
            s["avg_s"] = round((s["avg_s"] * (s["uses"] - 1) + secs) / s["uses"], 2)
            iid = f"{int(time.time() * 1000)}"
            mem["history"].append({"id": iid, "agent": agent, "model": m, "prompt": prompt[:1500],
                                   "answer": text[:3000], "ts": time.strftime("%Y-%m-%d %H:%M")})
            save_memory(mem)
            return {"id": iid, "text": text, "agent": agent, "model": m, "seconds": round(secs, 1),
                    "errors": errors}
        except Exception as e:
            errors.append(f"{m}: {e}")
            print(f"[Agentes] {agent}/{m} falló → {str(e)[:150]}")
            _stat(mem, agent, m)["fail"] += 1
            if ob._is_permanent(str(e)):
                ob.mark_model(m, False, str(e))
            save_memory(mem)
    return {"id": None, "agent": agent, "model": None,
            "text": "Ningún modelo gratis respondió:\n" + "\n".join(errors)}


def ask_team(prompt: str, agent: str | None = None, size: int = 3) -> dict:
    """Pregunta a varios modelos gratis en paralelo y fusiona la mejor respuesta."""
    if not ob.ensure_server():
        return {"text": "OpenCode no está disponible."}
    agent = agent if agent in AGENTS else pick_agent(prompt)
    mem = load_memory()
    models = rank_models(agent, mem)[: size + 2]
    system = build_system(agent, prompt, mem)

    def one(m):
        try:
            sid = ob._new_session(f"Equipo · {m}")
            return m, ob.ask_server(prompt, m, system=system, session_id=sid)
        except Exception as e:
            if ob._is_permanent(str(e)):
                ob.mark_model(m, False, str(e))
            return m, None

    answers = []
    with ThreadPoolExecutor(max_workers=size + 2) as ex:
        for m, txt in ex.map(one, models):
            if txt and len(answers) < size:
                answers.append((m, txt))
    if not answers:
        return {"text": "Ningún modelo del equipo respondió.", "agent": agent, "model": None}
    if len(answers) == 1:
        return {"text": answers[0][1], "agent": agent, "model": answers[0][0], "team": [answers[0][0]]}

    joined = "\n\n".join(f"### Respuesta {i + 1} ({m})\n{t}" for i, (m, t) in enumerate(answers))
    judge_prompt = (f"Pregunta original:\n{prompt}\n\nVarios expertos respondieron:\n\n{joined}\n\n"
                    "Fusiona lo mejor en UNA respuesta final correcta y completa. Corrige errores si "
                    "los ves. No menciones que hubo varias respuestas.")
    final = ask_agent(judge_prompt, agent="investigador" if agent != "programador" else "programador",
                      new_session=True)
    final["team"] = [m for m, _ in answers]
    final["agent"] = agent
    return final


# ─────────────────────────── Entrenar ───────────────────────────


def feedback(good: bool, correction: str | None = None, interaction_id: str | None = None,
             retry: bool = True) -> str:
    """Valora la última respuesta (o una concreta). Si es mala y das corrección, la aprende
    y (opcionalmente) vuelve a responder con otro modelo aplicando la corrección."""
    mem = load_memory()
    if not mem["history"]:
        return "No hay ninguna respuesta que valorar todavía."
    item = next((h for h in reversed(mem["history"]) if h["id"] == interaction_id), None) \
        if interaction_id else mem["history"][-1]
    if not item:
        return "No encuentro esa respuesta."

    s = _stat(mem, item["agent"], item["model"])
    s["good" if good else "bad"] += 1
    item["rating"] = "bien" if good else "mal"
    msg = f"Anotado: {item['agent']} con {item['model']} → {'bien' if good else 'mal'}."

    if correction:
        mem["lessons"].append({"agent": item["agent"], "text": correction.strip(),
                               "context": item["prompt"][:300], "date": time.strftime("%Y-%m-%d")})
        msg += " He guardado la corrección y la aplicaré en casos parecidos."
    save_memory(mem)

    if not good and retry:
        new_prompt = item["prompt"]
        if correction:
            new_prompt += f"\n\n(Tu respuesta anterior no era buena. Corrección de {OWNER}: {correction})"
        again = ask_agent(new_prompt, agent=item["agent"], exclude=[item["model"]])
        msg += f"\n\nNueva respuesta ({again['model']}):\n{again['text']}"
    return msg


def teach_rule(rule: str) -> str:
    mem = load_memory()
    rule = rule.strip()
    if rule and rule not in mem["rules"]:
        mem["rules"].append(rule)
        save_memory(mem)
    return f"Regla guardada. Ahora tengo {len(mem['rules'])} reglas."


def forget_rule(text_or_index: str) -> str:
    mem = load_memory()
    if text_or_index.isdigit() and 0 < int(text_or_index) <= len(mem["rules"]):
        removed = mem["rules"].pop(int(text_or_index) - 1)
    else:
        matches = [r for r in mem["rules"] if _norm(text_or_index) in _norm(r)]
        if not matches:
            return "No encuentro esa regla."
        removed = matches[0]
        mem["rules"].remove(removed)
    save_memory(mem)
    return f"Regla olvidada: {removed}"


def learning_summary() -> str:
    mem = load_memory()
    lines = []
    if mem["rules"]:
        lines.append("Reglas:")
        lines += [f"  {i + 1}. {r}" for i, r in enumerate(mem["rules"])]
    lines.append(f"\nLecciones guardadas: {len(mem['lessons'])}")
    lines.append(f"Respuestas registradas: {len(mem['history'])}\n")
    lines.append("Mejor modelo por agente (según lo aprendido):")
    for agent in AGENTS:
        ranked = rank_models(agent, mem, {**load_config(), "exploration": 0})
        st = mem["stats"].get(agent, {})
        if ranked:
            best = ranked[0]
            s = st.get(best, {})
            lines.append(f"  {agent:12s} → {best}   (👍{s.get('good', 0)} 👎{s.get('bad', 0)} "
                         f"✖{s.get('fail', 0)}, {s.get('avg_s', 0)}s)")
        else:
            lines.append(f"  {agent:12s} → (sin modelos gratis)")
    return "\n".join(lines)


# ─────────────────────────── Ollama ───────────────────────────


def opencode_config_path() -> Path:
    return Path.home() / ".config" / "opencode" / "opencode.json"


def setup_ollama() -> str:
    """Lee `ollama list` y añade esos modelos como proveedor en la config de OpenCode."""
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=20).stdout
    except FileNotFoundError:
        return ("Ollama no está instalado. Descárgalo de https://ollama.com/download y luego, por ejemplo:\n"
                "  ollama pull qwen3:8b\n  ollama pull gemma3:4b\n"
                "y vuelve a ejecutar --setup-ollama")
    names = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip()]
    names = [n for n in names if "embed" not in n.lower()]
    if not names:
        return "Ollama está instalado pero no tienes modelos. Prueba: ollama pull qwen3:8b"

    path = opencode_config_path()
    cfg = {}
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            backup = path.with_suffix(".json.bak")
            path.replace(backup)
            print(f"[Ollama] Config anterior ilegible, copia en {backup}")
    cfg.setdefault("$schema", "https://opencode.ai/config.json")
    prov = cfg.setdefault("provider", {}).setdefault("ollama", {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Ollama (local)",
        "options": {"baseURL": "http://localhost:11434/v1"},
        "models": {},
    })
    prov.setdefault("models", {})
    for n in names:
        prov["models"].setdefault(n, {"name": n})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return (f"Añadidos {len(names)} modelos de Ollama a {path}:\n  " + "\n  ".join(names) +
            "\n\nReinicia OpenCode para que los vea:\n  taskkill /F /IM opencode.exe\n"
            "y luego: python actions\\opencode_bridge.py --probe")


# ─────────────────────────── Integración con ZAR (Gemini tools) ───────────────────────────

TOOL_DECLARATIONS = [
    {
        "name": "agente",
        "description": "ALWAYS use this tool (not code_helper, dev_agent or specialist_agent) when the user mentions agente, agentes, agentes gratis, equipo, consejo, programador, investigador, traductor, redactor, analista or resumidor, or asks to consult an agent or a free AI. It sends the task to the team of FREE AI agents and returns their answer to read to the user. Use modo=equipo to ask several models and merge the answer.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "Full task or question."},
                "agente": {"type": "STRING", "description": "Optional agent name; auto if empty."},
                "modo": {"type": "STRING", "description": "'normal' (default) or 'equipo'."},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "valorar_respuesta",
        "description": ("Record the user's feedback on the last agent answer so ZAR learns. "
                        "Use when the user says it was good/bad or corrects it."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "valoracion": {"type": "STRING", "description": "'bien' or 'mal'."},
                "correccion": {"type": "STRING", "description": "What was wrong / how it should be."},
            },
            "required": ["valoracion"],
        },
    },
    {
        "name": "ensenar_regla",
        "description": "Save a permanent rule/preference the user wants all agents to follow.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"regla": {"type": "STRING", "description": "The rule, in the user's words."}},
            "required": ["regla"],
        },
    },
    {
        "name": "ver_aprendizaje",
        "description": "Show what ZAR has learned: rules, lessons and best free model per agent.",
        "parameters": {"type": "OBJECT", "properties": {"agente": {"type": "STRING", "description": "Optional: only this agent."}}},
    },
]


def handle_tool(name: str, args: dict, player=None) -> str:
    """Llama a esto desde main.py cuando Gemini invoque una de las TOOL_DECLARATIONS."""
    args = args or {}
    if name == "agente":
        if (args.get("modo") or "").lower() == "equipo":
            r = ask_team(args.get("prompt", ""), args.get("agente"))
            head = f"[equipo {r.get('agent')}: {', '.join(r.get('team', []))}]"
        else:
            r = ask_agent(args.get("prompt", ""), args.get("agente"))
            head = f"[{r.get('agent')} → {r.get('model')}]"
        result = f"{head}\n{r['text']}"
    elif name == "valorar_respuesta":
        good = _norm(args.get("valoracion", "")).startswith(("bien", "good", "buen", "si", "ok"))
        result = feedback(good, args.get("correccion"))
    elif name == "ensenar_regla":
        result = teach_rule(args.get("regla", ""))
    elif name == "ver_aprendizaje":
        result = learning_summary()
    else:
        return f"Herramienta desconocida: {name}"

    if player is not None and hasattr(player, "write_log"):
        try:
            player.write_log(result[:2000])
        except Exception:
            pass
    return result[:6000]



ANSWERS_DIR = Path(ob.WORKDIR) / "respuestas"
SLOW_TOOLS = ("agente", "valorar_respuesta")
_last_spoken = [0.0]
FEEDBACK_GUARD_S = 25

def _speakable(text, limit=450):
    t = re.sub(r"```.*?```", " [codigo en pantalla] ", text, flags=re.S)
    t = re.sub(r"^\[[^\]]*\]\s*", "", t)
    t = re.sub(r"[#*_`>|]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        cut = t[:limit]
        t = cut[: max(cut.rfind(". "), cut.rfind("? "), 200) + 1] + " La respuesta completa esta en pantalla."
    return t

def handle_tool_async(name, args, player=None, speak=None):
    if name == "valorar_respuesta" and time.time() - _last_spoken[0] < FEEDBACK_GUARD_S:
        return ("Ignored: that was the agents' own answer being read aloud, not user feedback. "
                "Do not call valorar_respuesta unless the user explicitly rates or corrects it.")
    if name not in SLOW_TOOLS:
        return handle_tool(name, args, player=player)
    import threading
    def work():
        try:
            result = handle_tool(name, args, player=None)
        except Exception as e:
            result = f"Los agentes fallaron: {e}"
        try:
            ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
            (ANSWERS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.md").write_text(result, encoding="utf-8")
        except Exception:
            pass
        if player is not None and hasattr(player, "write_log"):
            try: player.write_log(result[:4000])
            except Exception: pass
        print("[Agentes] Respuesta lista")
        if speak:
            _last_spoken[0] = time.time()
            try: speak("[Respuesta de los agentes gratis. Leela en voz alta tal cual; NO es una valoracion "
                       "del usuario, no llames a ninguna herramienta.] " + _speakable(result))
            except Exception as e: print(f"[Agentes] No pude hablar: {e}")
    threading.Thread(target=work, daemon=True).start()
    return ("The free agents are working on it in the background and will speak the answer "
            "themselves when done. Tell the user in one short sentence that the agents are on it, then stay silent.")


# ─────────────────────────── Modo entrenamiento (terminal) ───────────────────────────

HELP = """Comandos:
  (escribe normal)         → pregunta; ZAR elige el agente
  @agente texto            → forzar agente (ej: @programador arregla esto)
  /equipo texto            → varios modelos gratis + respuesta fusionada
  /bien                    → la última respuesta fue buena
  /mal [corrección]        → fue mala; aprende la corrección y reintenta con otro modelo
  /regla texto             → regla permanente (ej: /regla responde siempre en viñetas)
  /olvidar N|texto         → borrar una regla
  /stats                   → qué ha aprendido
  /agentes                 → modelo gratis de cada agente
  /salir"""


def repl():
    print("Modo entrenamiento de ZAR (solo modelos gratis). /ayuda para comandos.\n")
    if not ob.ensure_server():
        print("OpenCode no disponible.")
        return
    while True:
        try:
            line = input("tú › ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in ("/salir", "/exit", "/q"):
            break
        if line in ("/ayuda", "/help"):
            print(HELP)
        elif line == "/bien":
            print(feedback(True))
        elif line.startswith("/mal"):
            print(feedback(False, line[4:].strip() or None))
        elif line.startswith("/regla "):
            print(teach_rule(line[7:]))
        elif line.startswith("/olvidar "):
            print(forget_rule(line[9:].strip()))
        elif line == "/stats":
            print(learning_summary())
        elif line == "/agentes":
            print_agents()
        elif line.startswith("/equipo "):
            r = ask_team(line[8:])
            print(f"\n[equipo {r.get('agent')}: {', '.join(r.get('team', []))}]\n{r['text']}\n")
        else:
            agent = None
            if line.startswith("@"):
                agent, _, line = line[1:].partition(" ")
            r = ask_agent(line, agent)
            print(f"\n[{r['agent']} → {r['model']}, {r.get('seconds', '?')}s]\n{r['text']}\n")
            print("  (¿bien? /bien · ¿mal? /mal <corrección>)\n")


def print_agents():
    mem, cfg = load_memory(), {**load_config(), "exploration": 0}
    free = usable_models(cfg)
    print(f"{len(free)} modelos gratis utilizables:")
    for m in sorted(free):
        print("   " + m)
    print()
    for a, spec in AGENTS.items():
        r = rank_models(a, mem, cfg)
        print(f"  {a:12s} → {r[0] if r else '—'}   (reserva: {', '.join(r[1:3]) or '-'})")
        print(f"  {'':12s}   {spec['desc']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--setup-ollama" in args:
        print(setup_ollama())
    elif "--agentes" in args:
        if ob.ensure_server():
            print_agents()
    elif "--stats" in args:
        ob.ensure_server()
        print(learning_summary())
    else:
        repl()
