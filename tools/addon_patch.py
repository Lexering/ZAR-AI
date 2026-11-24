"""
Plugs the add-ons (actions/addons.py) into an existing main.py WITHOUT replacing it.

Adds three small blocks, marked "# ZAR-ADDONS":
  1. import of actions.addons (right before TOOL_DECLARATIONS)
  2. TOOL_DECLARATIONS += new tools (right after the list)
  3. at the start of JarvisLive._execute_tool: run add-on tools and return their result

Safe: makes a backup, checks the result compiles, restores the backup on any problem,
does nothing if main.py already has these tools or was already patched.
Usage: python tools/addon_patch.py   (from ZAR's folder)
"""
import ast
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
MARK = "# ZAR-ADDONS"
NEW_TOOLS = ('"office_pdf"', '"world_monitor"', '"boards"')


HUD_MARK = "# ZAR-HUD"


def patch_hud() -> int:
    """Connect the new HUD's chat box and file clip (two optional lines)."""
    src = MAIN.read_text(encoding="utf-8")
    if HUD_MARK in src or "set_text_sender" in src:
        return 0
    out, done = [], 0
    for line in src.splitlines(keepends=True):
        out.append(line)
        st = line.strip()
        ind = line[: len(line) - len(line.lstrip())]
        if st == "self._hud = hud_bridge" and "_on_text_command" in src:
            out.append(f"{ind}{HUD_MARK}: the HUD chat box talks to ZAR\n"
                       f"{ind}if hasattr(hud_bridge, 'set_text_sender'):\n"
                       f"{ind}    hud_bridge.set_text_sender(self._on_text_command)\n")
            done += 1
        elif st == "hud_bridge.set_file_handler(give_file)":
            quiet = ('lambda p: _WINCTL.request.emit("filequiet:" + str(p))' if '"filequiet:"' in src else "give_file")
            out.append(f"{ind}{HUD_MARK}: files attached with the HUD clip\n"
                       f"{ind}if hasattr(hud_bridge, 'set_quiet_file_handler'):\n"
                       f"{ind}    hud_bridge.set_quiet_file_handler({quiet})\n")
            done += 1
    if not done:
        print("[addons] HUD chat/clip hooks not added (main.py differs) - the HUD still works")
        return 0
    new = "".join(out)
    try:
        compile(new, str(MAIN), "exec")
    except SyntaxError as e:
        print(f"[addons] HUD hooks skipped ({e})")
        return 0
    MAIN.write_text(new, encoding="utf-8")
    print(f"[addons] HUD chat and file clip connected ({done} hook(s))")
    return 0


def main() -> int:
    if not MAIN.exists():
        print("[addons] main.py not found")
        return 1
    rc = patch_tools()
    patch_hud()
    return rc


def patch_tools() -> int:
    src = MAIN.read_text(encoding="utf-8")
    if MARK in src:
        print("[addons] already installed in main.py")
        return 0
    if any(f"name == {t}" in src for t in NEW_TOOLS):
        print("[addons] this main.py already includes the new tools - nothing to do")
        return 0

    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)

    decl = next((n for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "TOOL_DECLARATIONS" for t in n.targets)), None)
    if decl is None:
        print("[addons] TOOL_DECLARATIONS not found - can't plug the add-ons in automatically")
        return 2

    exe = None
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            for f in n.body:
                if isinstance(f, ast.AsyncFunctionDef) and f.name == "_execute_tool":
                    exe = f
    if exe is None or len(exe.args.args) < 2:
        print("[addons] _execute_tool not found - can't plug the add-ons in automatically")
        return 2
    fc = exe.args.args[1].arg
    # first statement that defines "name" and "args" -> insert after the later of the two
    after = None
    have = set()
    for st in exe.body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name) and t.id in ("name", "args"):
                    have.add(t.id)
        if {"name", "args"} <= have:
            after = st
            break
    if after is None:
        print("[addons] _execute_tool has no 'name'/'args' variables - can't plug in")
        return 2
    indent = " " * after.col_offset

    hook = (
        f"{indent}{MARK}: new tools (LibreOffice/PDF, World Monitor, Boards)\n"
        f"{indent}if _addons is not None and name in _addons.HANDLERS:\n"
        f"{indent}    try:\n"
        f"{indent}        _r = await asyncio.get_running_loop().run_in_executor(\n"
        f"{indent}            None, lambda: _addons.HANDLERS[name](args, self))\n"
        f"{indent}    except Exception as _e:\n"
        f"{indent}        _r = f\"Error: {{_e}}\"\n"
        f"{indent}    try:\n"
        f"{indent}        if not getattr(self.ui, 'muted', False):\n"
        f"{indent}            self.ui.set_state(\"LISTENING\")\n"
        f"{indent}    except Exception:\n"
        f"{indent}        pass\n"
        f"{indent}    return types.FunctionResponse(id={fc}.id, name=name, response={{\"result\": _r or \"Done.\"}})\n"
    )
    imp = (
        f"{MARK}: extra tools that plug in without replacing this file\n"
        "try:\n"
        "    from actions import addons as _addons\n"
        "except Exception as _e:\n"
        "    print(f\"[ZAR] Add-ons not loaded: {_e}\")\n"
        "    _addons = None\n\n"
    )
    extend = (
        f"\n{MARK}: register the add-on tools\n"
        "if _addons is not None:\n"
        "    _known = {d.get(\"name\") for d in TOOL_DECLARATIONS}\n"
        "    TOOL_DECLARATIONS += [d for d in _addons.DECLARATIONS if d.get(\"name\") not in _known]\n"
    )

    # insert from the bottom up so line numbers stay valid
    lines.insert(after.end_lineno, hook)
    lines.insert(decl.end_lineno, extend)
    lines.insert(decl.lineno - 1, imp)
    new = "".join(lines)

    if "import asyncio" not in new or "types" not in new:
        print("[addons] main.py lacks asyncio/types - can't plug in")
        return 2
    try:
        compile(new, str(MAIN), "exec")
    except SyntaxError as e:
        print(f"[addons] patch would break main.py ({e}) - nothing changed")
        return 3

    backup = MAIN.with_name(f"main.py.before_addons_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(MAIN, backup)
    MAIN.write_text(new, encoding="utf-8")
    print(f"[addons] installed in main.py (backup: {backup.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
