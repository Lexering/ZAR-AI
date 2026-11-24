from agents._base import run_specialist

ROLE = "You are ZAR's programming specialist. Write correct, working code and explain briefly how to use it."


def run(query: str, speak=None) -> str:
    return run_specialist(query, "programador", ROLE, speak)
