from agents._base import run_specialist

ROLE = "You are ZAR's research specialist. Find accurate, current facts, compare sources and give a short, clear conclusion."


def run(query: str, speak=None) -> str:
    return run_specialist(query, "investigador", ROLE, speak)
