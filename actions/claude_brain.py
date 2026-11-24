"""
claude_brain.py — Módulo de ZAR
Conecta ZAR con Claude (API oficial de Anthropic).

Instalación:   pip install anthropic
Clave:         setx ANTHROPIC_API_KEY "sk-ant-..."
Prueba:        python actions\\claude_brain.py
"""

import os
import json
import logging
from datetime import date
from pathlib import Path
try:
    from actions.owner_profile import OWNER
except Exception:
    OWNER = "the user"


log = logging.getLogger("alberto.claude")

try:
    import anthropic
except ImportError:
    anthropic = None

# ─────────────────────────── CONFIGURACIÓN ───────────────────────────

MODELS = {
    "rapido":   "claude-haiku-4-5-20251001",
    "normal":   "claude-sonnet-5-5",
    "potente":  "claude-opus-5-5",
}

TASK_TO_MODEL = {
    "rapido":       "rapido",
    "general":      "rapido",
    "traduccion":   "rapido",
    "codigo":       "normal",
    "trading":      "normal",
    "creativo":     "normal",
    "razonamiento": "potente",
}

SYSTEM_PROMPT = """Eres ZAR, el asistente personal de IA de """ + OWNER + """.
Hablas en el idioma en que te hablen, de forma clara, directa y cercana.
Tus respuestas se leen en voz alta, así que evita tablas, listas largas
y formato markdown salvo que te pidan código."""

MAX_TOKENS = 2048
HISTORY_TURNS = 10
HISTORY_DIR = Path(__file__).resolve().parent.parent / "memory" / "history"


# ─────────────────────────── CEREBRO CLAUDE ───────────────────────────

class ClaudeBrain:
    def __init__(self):
        if anthropic is None:
            raise RuntimeError("Falta la librería. Ejecuta: pip install anthropic")
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Falta la clave. Configura ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic()
        self.history = []

    def ask(self, prompt, task="general", remember=True):
        model = MODELS[TASK_TO_MODEL.get(task, "rapido")]
        messages = self.history[-HISTORY_TURNS * 2:] + [{"role": "user", "content": prompt}]

        response = self.client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        text = "".join(b.text for b in response.content if b.type == "text")

        if remember:
            self.history.append({"role": "user", "content": prompt})
            self.history.append({"role": "assistant", "content": text})
            self._save_to_daily_history(prompt, text, model)

        log.info("[Claude] tarea=%s modelo=%s", task, model)
        return {"text": text, "model": model, "task": task}

    def reset(self):
        self.history = []

    def _save_to_daily_history(self, prompt, text, model):
        try:
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            f = HISTORY_DIR / f"{date.today().isoformat()}.json"
            data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
            data.append({"source": "claude", "model": model, "user": prompt, "alberto": text})
            f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log.warning("No pude guardar historial: %s", e)


# ─────────────── Acción para el dispatcher de ZAR ───────────────

_brain = None

def claude_action(parameters, player=None):
    global _brain
    try:
        if _brain is None:
            _brain = ClaudeBrain()
    except RuntimeError as e:
        return str(e)

    prompt = parameters.get("prompt", "").strip()
    if not prompt:
        return "No me has dicho qué quieres preguntarle a Claude."

    task = parameters.get("task") or "general"

    try:
        result = _brain.ask(prompt, task)
    except Exception as e:
        log.error("Error con Claude: %s", e)
        return f"No he podido hablar con Claude: {e}"

    if player is not None and hasattr(player, "write_log"):
        player.write_log(f"[Claude] {result['task']} -> {result['model']}")
    return result["text"]


# ─────────────────────────── PRUEBA MANUAL ───────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        brain = ClaudeBrain()
    except RuntimeError as e:
        print("ERROR:", e)
        raise SystemExit(1)

    print("OK: Conectado con Claude\n")
    for task, q in [
        ("general", "Hola, preséntate en una frase."),
        ("codigo", "Dame una función Python corta que calcule la media móvil de una lista."),
        ("general", "¿Qué te acabo de pedir antes de esto?"),
    ]:
        r = brain.ask(q, task)
        print(f"> {q}\n  [{r['model']}]\n  {r['text']}\n")