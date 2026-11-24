from agents._base import run_specialist

ROLE = "You are ZAR's cybersecurity specialist (defensive only). Explain risks clearly and give concrete steps to protect the user's devices and accounts."


def run(query: str, speak=None) -> str:
    return run_specialist(query, "analista", ROLE, speak)
