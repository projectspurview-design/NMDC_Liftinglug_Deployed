#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=$(command -v python3 || true)

if [ -z "$PYTHON" ]; then
    printf '%s\n' "Python 3 is required. Install Python 3 and retry." >&2
    exit 1
fi

VENV_PYTHON="$APP_DIR/.venv-linux/bin/python"
if [ ! -x "$VENV_PYTHON" ] || ! "$VENV_PYTHON" -c 'import sys' >/dev/null 2>&1; then
    "$PYTHON" -m venv --clear "$APP_DIR/.venv-linux"
fi

"$VENV_PYTHON" -m pip install -r "$APP_DIR/requirements.txt"

DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
VOICE_DIR="$DATA_HOME/nmdc-hook-detector/voices"
VOICE_MODEL="$VOICE_DIR/hi_IN-rohan-medium.onnx"
mkdir -p "$VOICE_DIR"
if [ ! -f "$VOICE_MODEL" ]; then
    "$VENV_PYTHON" -m piper.download_voices \
        --download-dir "$VOICE_DIR" hi_IN-rohan-medium
fi

APP_MENU_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
mkdir -p "$APP_MENU_DIR" "$ICON_DIR"
cp "$APP_DIR/nmdc-hook-detector.svg" "$ICON_DIR/nmdc-hook-detector.svg"

"$PYTHON" - "$APP_DIR" "$APP_MENU_DIR/nmdc-hook-detector.desktop" <<'PY'
from pathlib import Path
import sys

app_dir = Path(sys.argv[1])
desktop_file = Path(sys.argv[2])
launcher = str(app_dir / "launch_linux.sh")

def quote_exec(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("$", "\\$").replace("`", "\\`").replace("%", "%%")
    return f'"{value}"'

desktop_file.write_text(
    "[Desktop Entry]\n"
    "Version=1.0\n"
    "Type=Application\n"
    "Name=NMDC Lifting Hook Detector\n"
    "Comment=Live lifting hook and lifting lug detection\n"
    f"Exec=/bin/sh {quote_exec(launcher)}\n"
    "Icon=nmdc-hook-detector\n"
    "Terminal=true\n"
    "Categories=Utility;\n",
    encoding="utf-8",
)
desktop_file.chmod(0o755)
PY

DESKTOP_DIR=
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || true)
fi
if [ -z "$DESKTOP_DIR" ] || [ "$DESKTOP_DIR" = "$HOME" ]; then
    DESKTOP_DIR="$HOME/Desktop"
fi
mkdir -p "$DESKTOP_DIR"
DESKTOP_SHORTCUT="$DESKTOP_DIR/nmdc-hook-detector.desktop"
cp "$APP_MENU_DIR/nmdc-hook-detector.desktop" "$DESKTOP_SHORTCUT"
chmod +x "$DESKTOP_SHORTCUT"
if command -v gio >/dev/null 2>&1; then
    gio set "$DESKTOP_SHORTCUT" metadata::trusted true >/dev/null 2>&1 || true
fi

printf '%s\n' "Installed. Search the applications menu or desktop for NMDC Lifting Hook Detector."