#!/bin/sh
# Fail if the tree contains private addresses, home paths, usernames or tokens.
# Run before every push to the public repo. Prints "clean" on success; any
# match or any file that cannot be scanned is a failure.
# LEAK_SCAN_ROOT overrides the tree (default: this repo).
set -u
ROOT="${LEAK_SCAN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT" || exit 2

PATTERNS='(^|[^0-9.])10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}
(^|[^0-9.])172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}
(^|[^0-9.])192\.168\.[0-9]{1,3}\.[0-9]{1,3}
(^|[^0-9.])100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}
/home/[a-z]
/Users/[A-Za-z]
\.ts\.net
gh[pousr]_[A-Za-z0-9]{20,}
github_pat_[A-Za-z0-9_]{20,}
sk-(proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}
hf_[A-Za-z0-9]{20,}
AKIA[0-9A-Z]{16}
xox[baprs]-[A-Za-z0-9-]{10,}
-----BEGIN [A-Z ]*PRIVATE KEY'
# Extra private terms (hostnames, names), one regex per line, kept outside the repo.
EXTRA="${LEAK_SCAN_EXTRA:-$HOME/.config/cu-local/leak-terms}"

PAT=$(mktemp); LIST=$(mktemp); OUT=$(mktemp)
trap 'rm -f "$PAT" "$LIST" "$OUT"' EXIT
printf '%s\n' "$PATTERNS" > "$PAT"
[ -f "$EXTRA" ] && grep -v '^[[:space:]]*$' "$EXTRA" >> "$PAT"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git ls-files -z --cached --others --exclude-standard > "$LIST" || exit 2
else
  find . -type f -not -path './.git/*' -print0 > "$LIST" || exit 2
fi

# grep exit status: 0 match, 1 no match, 2 error. Treat errors as failures.
# The scanner itself is excluded (it contains the patterns).
xargs -0 grep -nIiE -f "$PAT" -- < "$LIST" > "$OUT" 2>&1
rc=$?
grep -v '^scripts/leak-scan.sh:' "$OUT" > "$OUT.f" 2>/dev/null; mv "$OUT.f" "$OUT"
if [ -s "$OUT" ]; then
  cat "$OUT"
  echo "LEAK: matches above" >&2
  exit 1
fi
if [ "$rc" -gt 1 ] && [ "$rc" -ne 123 ]; then
  echo "leak-scan: grep failed (exit $rc); refusing to report clean" >&2
  exit 2
fi
echo clean
