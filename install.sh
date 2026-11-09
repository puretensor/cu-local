#!/bin/sh
# Install cu-local into ~/.local/bin (or $PREFIX/bin). Safe to re-run.
#   curl -fsSL https://raw.githubusercontent.com/puretensor/cu-local/main/install.sh | sh
# From a checkout:  ./install.sh   (installs that checkout's cu_local.py)
set -eu

PREFIX="${PREFIX:-$HOME/.local}"
BIN="$PREFIX/bin"
REPO_RAW="${CU_LOCAL_RAW:-https://raw.githubusercontent.com/puretensor/cu-local/main}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "cu-local needs python3 (3.9+). On macOS run: xcode-select --install" >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  echo "cu-local needs Python 3.9 or newer; found $(python3 --version 2>&1)" >&2
  exit 1
fi

# Use a local copy only when this script was run as a file from a checkout.
# Under `curl | sh`, $0 is the shell, and the current directory is not trusted.
SRC=""
case "$(basename -- "$0")" in
  install.sh)
    if [ -f "$0" ]; then
      HERE=$(cd "$(dirname -- "$0")" && pwd)
      [ -f "$HERE/cu_local.py" ] && SRC="$HERE/cu_local.py"
    fi
    ;;
esac

mkdir -p "$BIN"
TMP=$(mktemp "$BIN/.cu-local.XXXXXX")
trap 'rm -f "$TMP"' EXIT INT TERM
if [ -n "$SRC" ]; then
  cp "$SRC" "$TMP"
else
  curl -fsSL "$REPO_RAW/cu_local.py" -o "$TMP"
fi
chmod 0755 "$TMP"
# Validate before replacing a working install.
VERSION=$(python3 "$TMP" --version) || { echo "downloaded cu-local failed to run; nothing changed" >&2; exit 1; }
mv -f "$TMP" "$BIN/cu-local"
trap - EXIT INT TERM

echo "Installed $VERSION to $BIN/cu-local"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Add it to your PATH:  echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
esac
echo "Next:  cu-local doctor --endpoint http://YOUR-SERVER:8000/v1 --save"
