"""
Build a one-click update file for ZAR:  updates/ZAR_Update_vN.cmd

Your colleagues just download it and double-click it. It finds their ZAR,
closes it, backs up the old files, copies the new ones, installs new packages
if needed and starts ZAR again. Their keys, memory and voice are never touched.

Run it with make_update.bat (in ZAR's folder).
"""
import base64
import fnmatch
import io
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
TEMPLATE = TOOLS / "updater_template.ps1"
KEEP_FILE = TOOLS / "update_keep.txt"
OUT_DIR = ROOT / "updates"

# Folders never sent to colleagues
EXCLUDE_DIRS = {"venv", ".venv", ".git", ".github", "dist", "installer/deps", "__pycache__", "config", "memory/history", "logs",
                "backups", "updates", "models", "voices", "agent/task_queue", "node_modules"}
# Files never sent (secrets, personal data, junk)
EXCLUDE_FILES = ["*.pyc", "*.log", "*.bak", "*.bak*", "*_antes*.py", "*.zip", "*.env", ".env",
                 "*.pem", "*.pkl", "token.json", "credentials.json", "client_secret*.json",
                 "voices.json", "long_term.json", "alberto_learning.json", "shop.json",
                 "orders.json", "products.json", "hud_apps.json", "Alberto_Update_*.cmd", "ZAR_Update_*.cmd", "ZAR-Setup*.exe",
                 "Alberto_secrets*", "LEEME_ESTADO.txt"]
# ...except these, which are safe templates
ALLOW = ["config/*.example.json", "memory/*.py"]

SECRET_PATTERNS = [
    (r"AIza[0-9A-Za-z_\-]{35}", "Google/Gemini API key"),
    (r"sk-ant-[A-Za-z0-9_\-]{20,}", "Anthropic key"),
    (r"sk-or-v1-[A-Za-z0-9]{20,}", "OpenRouter key"),
    (r"\bsk-[A-Za-z0-9]{32,}", "OpenAI-style key"),
    (r"\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}", "Telegram bot token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"\bghp_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"\bshpat_[a-f0-9]{20,}", "Shopify token"),
]
TEXT_EXT = {".py", ".pyw", ".txt", ".json", ".html", ".js", ".css", ".md", ".bat", ".ps1", ".cfg", ".ini", ".yml", ".yaml"}
MAX_FILE_MB = 25


def is_excluded(rel: str) -> bool:
    if any(fnmatch.fnmatch(rel, a) for a in ALLOW):
        return False
    if rel.startswith("memory/"):
        return True
    parts = rel.split("/")
    for i in range(len(parts) - 1):
        if parts[i] in EXCLUDE_DIRS or "/".join(parts[: i + 1]) in EXCLUDE_DIRS:
            return True
    return any(fnmatch.fnmatch(parts[-1], p) for p in EXCLUDE_FILES)


def collect():
    files = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if is_excluded(rel):
            continue
        if p.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            print(f"  [skip] {rel} is bigger than {MAX_FILE_MB} MB")
            continue
        files.append((p, rel))
    return files


def scan_secrets(files):
    hits = []
    for p, rel in files:
        if p.suffix.lower() not in TEXT_EXT:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat, name in SECRET_PATTERNS:
            if re.search(pat, text):
                hits.append(f"{rel}  ->  looks like a {name}")
    return hits


ADDON_FILES = ["actions/addons.py", "actions/addons_notify.py", "actions/libreoffice.py",
               "actions/world_monitor.py", "actions/boards.py", "wallpaper/world.html", "wallpaper/boards.html",
               "tools/addon_patch.py",
               # new HUD (wallpaper with quick access, World News, weather, chat, World Monitor and Boards buttons)
               "actions/hud_bridge.py", "wallpaper/index.html", "wallpaper/chat.html", "wallpaper/settings.html"]


def main():
    addons_mode = "--addons" in sys.argv
    if not (ROOT / "main.py").exists():
        sys.exit("[X] Run this from ZAR's folder (main.py not found).")

    ver_file = ROOT / ("addons_version.txt" if addons_mode else "version.txt")
    try:
        version = int(ver_file.read_text().strip()) + 1
    except Exception:
        version = 1

    print("=" * 46)
    print(f"  ZAR - building {'ADD-ONS' if addons_mode else 'update'} v{version}")
    print("=" * 46)
    try:
        notes = input("What changed? (one line, Enter to skip): ").strip()
    except EOFError:
        notes = ""
    notes = "\n".join(l for l in notes.splitlines() if not l.lstrip().startswith("'@"))

    files = collect()
    if addons_mode:
        files = [(ROOT / f, f) for f in ADDON_FILES]
    hits = scan_secrets(files)
    if hits:
        print("\n[X] STOP - these files contain something that looks like a key:")
        for h in hits:
            print("   ", h)
        print("Move the key to config/ (it's never sent) and try again. Nothing was built.")
        sys.exit(1)

    keep = []
    if KEEP_FILE.exists():
        keep = [l.strip() for l in KEEP_FILE.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.strip().startswith("#")]

    ver_file.write_text(f"{version}\n")  # bump before zipping so it ships
    if not any(rel == ver_file.name for _, rel in files):
        files.append((ver_file, ver_file.name))
    if addons_mode:
        keep = []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p, rel in files:
            z.write(p, rel)
    b64 = base64.b64encode(buf.getvalue()).decode()
    b64_lines = "\r\n".join(b64[i:i + 120] for i in range(0, len(b64), 120))

    keep_ps = ", ".join("'" + k.replace("'", "''") + "'" for k in keep)
    ps = (TEMPLATE.read_text(encoding="utf-8")
          .replace("__VERSION__", str(version))
          .replace("__NOTES__", notes)
          .replace("__KEEP__", keep_ps)
          .replace("__MODE__", "addons" if addons_mode else "full"))

    header = (
        "@echo off\r\n"
        "title ZAR Update\r\n"
        'set "ALBERTO_UPD=%~f0"\r\n'
        'powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:ALBERTO_UPD); '
        "$a=$s.IndexOf('#'+'#ALBERTO_PS#'+'#'); $b=$s.IndexOf('#'+'#ALBERTO_DATA#'+'#'); "
        'iex $s.Substring($a,$b-$a)"\r\n'
        "if errorlevel 1 pause\r\n"
        "exit /b\r\n"
    )
    body = header + ps.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n##ALBERTO_DATA##\r\n" + b64_lines + "\r\n"

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / (f"ZAR_Addons_v{version}.cmd" if addons_mode else f"ZAR_Update_v{version}.cmd")
    out.write_bytes(body.encode("utf-8"))  # UTF-8 without BOM (a BOM breaks cmd)

    print(f"\n  Files packed : {len(files)}")
    print(f"  Size         : {out.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"  Kept on their PC (not overwritten): {', '.join(keep) or 'none'}")
    print(f"\n  DONE -> {out}")
    print("  Send that file to your colleagues (Telegram / WhatsApp / Drive).")
    print("  They just double-click it.")


if __name__ == "__main__":
    main()
