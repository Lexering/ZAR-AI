"""ZAR long-term memory: memory/long_term.json  ->  {category: {key: {"value": ...}}}"""
import json
import threading
from datetime import datetime
from pathlib import Path

MEMORY_PATH = Path(__file__).resolve().parent / "long_term.json"
_LOCK = threading.Lock()


def load_memory() -> dict:
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict) and "value" not in v:
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def update_memory(updates: dict) -> dict:
    with _LOCK:
        mem = load_memory()
        stamp = datetime.now().strftime("%Y-%m-%d")
        for cat in updates.values():
            if isinstance(cat, dict):
                for item in cat.values():
                    if isinstance(item, dict) and "value" in item:
                        item.setdefault("updated", stamp)
        _merge(mem, updates)
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MEMORY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(MEMORY_PATH)
        return mem


def format_memory_for_prompt(memory: dict, max_chars: int = 4000) -> str:
    if not memory:
        return ""
    lines = ["[LONG-TERM MEMORY - what you know about the user]"]
    for cat, items in memory.items():
        if not isinstance(items, dict) or not items:
            continue
        lines.append(f"{cat}:")
        for key, item in items.items():
            val = item.get("value", item) if isinstance(item, dict) else item
            lines.append(f"  - {key}: {val}")
    text = "\n".join(lines)
    return text[:max_chars]
