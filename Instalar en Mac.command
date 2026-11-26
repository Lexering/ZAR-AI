#!/bin/bash
# ZAR for macOS - double-click installer.
# Installs its own Python (with uv), the AI components, starts at login (LaunchAgent) and runs in the background.
# The first time macOS may say it can't verify the developer: right-click this file -> Open -> Open.

APP_NAME="ZAR"
APP_ID="com.zar.assistant"
GUARD="zar_guardian.pyw"
CFG_NAME="zar.json"
WAKE="Zar"

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/$APP_NAME"
APP="$DEST/app"
LOG="$DEST/install.log"
mkdir -p "$DEST"
exec > >(tee -a "$LOG") 2>&1

say() { printf "\n\033[1;35m%s\033[0m\n" "$1"; }
ask() {  # ask "question" "default"
  osascript -e "text returned of (display dialog \"$1\" default answer \"$2\" with title \"$APP_NAME\" buttons {\"OK\"} default button 1)" 2>/dev/null
}
info() { osascript -e "display dialog \"$1\" with title \"$APP_NAME\" buttons {\"OK\"} default button 1" >/dev/null 2>&1; }

clear
say "=========================================="
say "   Instalando $APP_NAME en tu Mac"
say "=========================================="

# ---------- 1. Questions ----------
NAME="$(ask "¿Cómo te llamas? / What's your name?" "")"
LANG_CHOICE="$(osascript -e "choose from list {\"Español\", \"English\"} with title \"$APP_NAME\" with prompt \"Idioma / Language\" default items {\"Español\"}" 2>/dev/null)"
[ "$LANG_CHOICE" = "English" ] && UI_LANG="en" || UI_LANG="es"
KEY=""
if [ -f "$SRC/config/api_keys.json" ] && grep -q '"gemini_api_key": *"[A-Za-z0-9_-]\{20,\}"' "$SRC/config/api_keys.json"; then
  KEY="(from file)"
else
  KEY="$(ask "Clave gratuita de Google Gemini (aistudio.google.com/apikey → Create API key):" "")"
fi
IMG="$(ask "Clave para imágenes (opcional): Gemini con facturación o token de Hugging Face hf_..." "")"

# ---------- 2. Copy the app ----------
say "Copiando $APP_NAME..."
mkdir -p "$APP"
rsync -a --delete --exclude ".git" --exclude "dist" --exclude "installer/deps" --exclude "memory/*.db" \
      --exclude "memory/*.json" --exclude "memory/backups" --exclude "config/*.json" --exclude "logs" "$SRC/" "$APP/" \
  || cp -R "$SRC/." "$APP/"
cp "$SRC"/config/*.example.json "$APP/config/" 2>/dev/null
mkdir -p "$APP/logs" "$APP/memory" "$APP/config"

# ---------- 3. Python (private, with uv) ----------
say "Instalando el motor (Python)..."
if [ ! -x "$DEST/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$DEST/bin" INSTALLER_NO_MODIFY_PATH=1 UV_NO_MODIFY_PATH=1 sh
fi
UV="$DEST/bin/uv"
if [ ! -x "$UV" ]; then info "No se pudo descargar el motor. Revisa internet y vuelve a abrir el instalador."; exit 1; fi
[ -x "$DEST/venv/bin/python" ] || "$UV" venv --python 3.12 "$DEST/venv"
PY="$DEST/venv/bin/python"

# ---------- 4. AI components ----------
say "Instalando los componentes de IA (5-15 minutos, no cierres esta ventana)..."
# Older macOS (12 Monterey and before): newest Qt / Playwright builds need macOS 13+,
# so use the last versions that still have ready-made packages (nothing gets compiled).
MACV="$(sw_vers -productVersion)"; MAJOR="${MACV%%.*}"
CONS="$DEST/constraints.txt"
if [ "$MAJOR" -lt 13 ]; then
  printf "PyQt6==6.7.1\nPyQt6-Qt6==6.7.3\nplaywright==1.47.0\n" > "$CONS"
  say "macOS $MACV: usando versiones compatibles con este Mac."
else
  : > "$CONS"
fi
UVI=("$UV" pip install --python "$PY" -c "$CONS" --only-binary PyQt6 --only-binary PyQt6-Qt6 --only-binary PyQt6-sip)
"${UVI[@]}" webrtcvad-wheels scipy librosa
"${UVI[@]}" torch || say "(El reconocimiento de voz del dueño no está disponible en este Mac; todo lo demás sí.)"
"${UVI[@]}" resemblyzer --no-deps
"${UVI[@]}" PyQt6
if ! "${UVI[@]}" -r "$APP/requirements_mac.txt"; then
  say "Instalando uno a uno..."
  grep -v '^#' "$APP/requirements_mac.txt" | while read -r pkg; do
    [ -n "$pkg" ] && { "${UVI[@]}" "$pkg" || echo "FAILED $pkg"; }
  done
fi
"${UVI[@]}" pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-WebKit pyobjc-framework-Quartz
"$PY" -m playwright install chromium >/dev/null 2>&1 || say "(Navegador automático no disponible en este Mac; no afecta al resto.)"
"$PY" -m compileall -q "$APP" >/dev/null 2>&1
if ! "$PY" -c "import numpy, PyQt6, google.genai"; then
  info "Faltan componentes importantes. Revisa internet y vuelve a abrir el instalador."; exit 1
fi

# ---------- 5. Config ----------
NAME="$NAME" UI_LANG="$UI_LANG" KEY="$KEY" IMG="$IMG" SRC="$SRC" APP="$APP" CFG_NAME="$CFG_NAME" "$PY" - <<'PYEOF'
import json, os, pathlib
app = pathlib.Path(os.environ["APP"]); cfg = app / "config"; cfg.mkdir(parents=True, exist_ok=True)
(cfg / os.environ["CFG_NAME"]).write_text(json.dumps({"name": os.environ["NAME"].strip(), "lang": os.environ["UI_LANG"]}, ensure_ascii=False))
keys = {}
src = pathlib.Path(os.environ["SRC"]) / "config" / "api_keys.json"
if src.exists():
    try: keys = json.loads(src.read_text(encoding="utf-8-sig"))
    except Exception: keys = {}
if os.environ["KEY"] and os.environ["KEY"] != "(from file)":
    keys["gemini_api_key"] = os.environ["KEY"].strip()
img = os.environ["IMG"].strip()
if img:
    keys["huggingface_token" if img.startswith("hf_") else "gemini_image_key"] = img
(cfg / "api_keys.json").write_text(json.dumps(keys, indent=2))
PYEOF

# ---------- 6. Start at login, in the background ----------
say "Configurando el arranque automático..."
PLIST="$HOME/Library/LaunchAgents/$APP_ID.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$APP_ID</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$APP/$GUARD</string></array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$APP/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$APP/logs/launchd.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$APP_ID" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"

# ---------- 7. Animated wallpaper (built in, no extra app needed) ----------
say "Poniendo el fondo animado..."
WPLIST="$HOME/Library/LaunchAgents/$APP_ID.wallpaper.plist"
cat > "$WPLIST" <<EOF2
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$APP_ID.wallpaper</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$APP/mac_wallpaper.py</string></array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$APP/logs/wallpaper.log</string>
  <key>StandardErrorPath</key><string>$APP/logs/wallpaper.log</string>
</dict></plist>
EOF2
launchctl bootout "gui/$(id -u)/$APP_ID.wallpaper" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$WPLIST" 2>/dev/null || launchctl load -w "$WPLIST"

info "¡$APP_NAME está instalado!

Arranca solo en segundo plano cada vez que enciendes el Mac, con su fondo animado.

PERMISOS: cuando macOS lo pida, permite el MICRÓFONO (y Accesibilidad si quieres que controle el Mac).
Di «$WAKE» para hablar."
say "Listo. Puedes cerrar esta ventana."
