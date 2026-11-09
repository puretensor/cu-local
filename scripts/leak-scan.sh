#!/bin/sh
# Fail if the tree contains private addresses, home paths, usernames or tokens.
# Run before every push to the public repo. Prints "clean" on success.
set -u
cd "$(dirname "$0")/.." || exit 1

PATTERNS='
100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[0-9]+
192\.168\.[0-9]+\.[0-9]+
10\.200\.[0-9]+\.[0-9]+
/home/[a-z]
/Users/[A-Za-z]
tail[0-9a-f]{6}\.ts\.net
gh[pousr]_[A-Za-z0-9]{20,}
sk-[A-Za-z0-9]{20,}
hf_[A-Za-z0-9]{20,}
AKIA[0-9A-Z]{16}
-----BEGIN [A-Z ]*PRIVATE KEY
'
# Extra private terms (hostnames, names) can be supplied out of tree, one regex per line.
EXTRA="${LEAK_SCAN_EXTRA:-$HOME/.config/cu-local/leak-terms}"

found=0
files=$(git ls-files --cached --others --exclude-standard 2>/dev/null || find . -type f -not -path './.git/*')
echo "$PATTERNS" | while IFS= read -r p; do
  [ -z "$p" ] && continue
  if echo "$files" | xargs grep -nIE -- "$p" 2>/dev/null | grep -v 'scripts/leak-scan.sh'; then
    echo "LEAK: pattern $p" >&2
    exit 1
  fi
done || found=1
if [ -f "$EXTRA" ]; then
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    if echo "$files" | xargs grep -niIE -- "$p" 2>/dev/null; then
      echo "LEAK: private term from $EXTRA" >&2
      found=1
    fi
  done < "$EXTRA"
fi
if [ "$found" = 0 ]; then echo clean; else exit 1; fi
