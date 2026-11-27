#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG_FILE=${XDG_CONFIG_HOME:-"$HOME/.config"}/nmdc-hook-detector/environment

if [ -f "$CONFIG_FILE" ]; then
    . "$CONFIG_FILE"
fi

PYTHON=${NMDC_PYTHON:-}
if [ -z "$PYTHON" ]; then
    for candidate in "$APP_DIR/.venv/bin/python" "$APP_DIR/.venv-linux/bin/python"; do
        if [ -x "$candidate" ] && "$candidate" -c 'import dx_engine' >/dev/null 2>&1; then
            PYTHON=$candidate
            break
        fi
    done
fi

if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ] || ! "$PYTHON" -c 'import dx_engine' >/dev/null 2>&1; then
    printf '%s\n' "DEEPX DX-RT Python runtime was not found. Set NMDC_PYTHON to its Python executable in $CONFIG_FILE." >&2
    exit 1
fi

if ! command -v xdg-open >/dev/null 2>&1; then
    printf '%s\n' "Install xdg-utils to open the browser automatically." >&2
    exit 1
fi

PREFERRED_PORT=${NMDC_HOOK_PORT:-8004}
PORT_RESULT=$("$PYTHON" - "$PREFERRED_PORT" <<'PY'
import socket
import sys
import urllib.error
import urllib.request

try:
    preferred = int(sys.argv[1])
except ValueError:
    raise SystemExit("NMDC_HOOK_PORT must be a number from 1 to 65535.")

if not 1 <= preferred <= 65535:
    raise SystemExit("NMDC_HOOK_PORT must be a number from 1 to 65535.")

for port in range(preferred, min(preferred + 21, 65535)):
    app_url = f"http://127.0.0.1:{port + 1}/"
    try:
        with urllib.request.urlopen(app_url, timeout=1) as response:
            page = response.read().decode("utf-8", errors="replace")
        if '<div id="root"></div>' in page:
            print(f"running:{port + 1}")
            raise SystemExit(0)
    except urllib.error.HTTPError:
        pass
    except urllib.error.URLError:
        pass

    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port + 1))
        print(f"free:{port}")
        raise SystemExit(0)
    except OSError:
        continue

raise SystemExit("No available port found in the configured 21-port range.")
PY
)

case "$PORT_RESULT" in
    running:*)
        xdg-open "http://127.0.0.1:${PORT_RESULT#running:}" >/dev/null 2>&1 &
        exit 0
        ;;
    free:*) PORT=${PORT_RESULT#free:} ;;
    *) printf '%s\n' "$PORT_RESULT" >&2; exit 1 ;;
esac

APP_URL="http://127.0.0.1:$((PORT + 1))/"
"$PYTHON" "$APP_DIR/app.py" --host 127.0.0.1 --port "$PORT" &
APP_PID=$!
trap 'kill "$APP_PID" 2>/dev/null || true' EXIT INT TERM

"$PYTHON" - "$APP_URL" "$APP_PID" <<'PY'
import os
import subprocess
import sys
import time
import urllib.request

url = sys.argv[1]
app_pid = int(sys.argv[2])
for _ in range(30):
    try:
        os.kill(app_pid, 0)
    except ProcessLookupError:
        raise SystemExit("The detector stopped during startup; see the error above.")
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            page = response.read().decode("utf-8", errors="replace")
        if '<div id="root"></div>' in page:
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit(f"The frontend did not start at {url}.")

subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
PY

wait "$APP_PID"