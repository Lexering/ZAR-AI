from agents._base import run_specialist

ROLE = "You are ZAR's entertainment specialist. Recommend films, series, music, games or plans that fit the request, with one line on why."


def run(query: str, speak=None) -> str:
    return run_specialist(query, "general", ROLE, speak)
