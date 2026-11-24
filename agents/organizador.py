from agents._base import run_specialist

ROLE = "You are ZAR's organisation specialist. Turn the request into a clear plan: priorities, steps, times and reminders."


def run(query: str, speak=None) -> str:
    return run_specialist(query, "general", ROLE, speak)
