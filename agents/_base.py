"""Shared runner for the specialist agents: uses ZAR's free agents (OpenCode)."""
try:
    from actions.alberto_agents import ask_agent
except Exception:  # OpenCode bridge missing
    ask_agent = None

LIMIT = 1200


def run_specialist(query: str, agent: str, role: str, speak=None) -> str:
    query = (query or "").strip()
    if not query:
        return "No task was given."
    if ask_agent is None:
        return "Specialist agents need OpenCode (free agents). Run setup.bat with Node.js installed."
    try:
        r = ask_agent(f"{role}\n\nTask: {query}", agent)
        text = (r or {}).get("text") or "No answer."
    except Exception as e:
        text = f"Specialist agent failed: {e}"
    return text[:LIMIT]
