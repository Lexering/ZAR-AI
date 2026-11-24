import json
import os
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
HISTORY_DIR = BASE_DIR / "memory" / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

def _today_file() -> Path:
    return HISTORY_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.json"

def save_message(role: str, text: str):
    """Save a message to today's history file."""
    try:
        path = _today_file()
        history = []
        if path.exists():
            history = json.loads(path.read_text(encoding="utf-8"))
        history.append({
            "role": role,
            "text": text,
            "time": datetime.now().strftime("%H:%M:%S")
        })
        path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[History] Error saving: {e}")

def get_today_history() -> list:
    """Get today's conversation history."""
    try:
        path = _today_file()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []

def get_history_summary(days: int = 1) -> str:
    """Get a formatted summary of recent history."""
    try:
        files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:days]
        result = []
        for f in files:
            date = f.stem
            messages = json.loads(f.read_text(encoding="utf-8"))
            result.append(f"\n--- {date} ---")
            for m in messages[-20:]:
                role = "Tú" if m["role"] == "user" else "ZAR"
                result.append(f"[{m['time']}] {role}: {m['text'][:100]}")
        return "\n".join(result) if result else "No hay historial disponible."
    except Exception as e:
        return f"Error leyendo historial: {e}"

def get_all_dates() -> list:
    """Get list of dates with history."""
    return [f.stem for f in sorted(HISTORY_DIR.glob("*.json"), reverse=True)]