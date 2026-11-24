"""Who owns this ZAR. Name comes from config/owner.json -> {"name": "..."}.
Each PC has its own (the updater asks for it once), so shared code never hardcodes a person."""
import json
from pathlib import Path

_OWNER_FILE = Path(__file__).resolve().parent.parent / "config" / "owner.json"
_DEFAULT = "the user"


def get_owner_name() -> str:
    try:
        n = json.loads((_OWNER_FILE.parent / "zar.json").read_text(encoding="utf-8-sig")).get("name", "").strip()
        if n:
            return n
    except Exception:
        pass
    try:
        name = json.loads(_OWNER_FILE.read_text(encoding="utf-8-sig")).get("name", "").strip()
        return name or _DEFAULT
    except Exception:
        return _DEFAULT


OWNER = get_owner_name()
