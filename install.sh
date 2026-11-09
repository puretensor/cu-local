#!/bin/sh
# Install cu-local into ~/.local/bin (or $PREFIX/bin). Safe to re-run.
#   curl -fsSL https://raw.githubusercontent.com/puretensor/cu-local/main/install.sh | sh
set -eu

PREFIX="${PREFIX:-$HOME/.local}"
BIN="$PREFIX/bin"
REPO_RAW="${CU_LOCAL_RAW:-https://raw.githubusercontent.com/puretensor/cu-local/main}"
HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "cu-local needs python3 (3.9+). On macOS run: xcode-select --install" >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  echo "cu-local needs Python 3.9 or newer; found $(python3 --version 2>&1)" >&2
  exit 1
fi

mkdir -p "$BIN"
if [ -n "$HERE" ] && [ -f "$HERE/cu_local.py" ]; then
  cp "$HERE/cu_local.py" "$BIN/cu-local"
else
  curl -fsSL "$REPO_RAW/cu_local.py" -o "$BIN/cu-local"
fi
chmod 0755 "$BIN/cu-local"

echo "Installed $("$BIN/cu-local" --version) to $BIN/cu-local"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Add it to your PATH:  echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
esac
echo "Next:  cu-local doctor --endpoint http://YOUR-SERVER:8000/v1 --save"
