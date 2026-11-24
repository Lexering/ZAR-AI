"""Pick which specialist agent fits a request."""
import importlib

KEYWORDS = {
    "programador": ["code", "código", "codigo", "program", "script", "python", "bug", "error", "función", "funcion", "app"],
    "investigador": ["research", "investiga", "busca", "search", "find out", "compare", "compara", "noticias", "news"],
    "organizador": ["plan", "organiza", "organise", "organize", "schedule", "agenda", "horario", "tareas", "tasks"],
    "entretenimiento": ["movie", "película", "pelicula", "serie", "music", "música", "musica", "game", "juego", "recommend", "recomienda"],
    "seguridad": ["security", "seguridad", "hack", "virus", "malware", "password", "contraseña", "phishing", "wifi"],
}


def detect_agent(text: str) -> str | None:
    t = (text or "").lower()
    best, score = None, 0
    for agent, words in KEYWORDS.items():
        s = sum(1 for w in words if w in t)
        if s > score:
            best, score = agent, s
    return best


def get_agent(name: str):
    if name not in KEYWORDS:
        return None
    return importlib.import_module(f"agents.{name}")
