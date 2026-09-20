#!/bin/bash
# venv_guard.sh [--repair]  -> verify the checkout's venv still resolves to the checkout.
#
# Every run tree symlinks REPO/.venv rather than building its own, so a worker
# that runs `uv pip install -e .` or `uv sync` inside its tree rewrites the
# REAL venv's editable pointers to that tree. The tree is a snapshot of an old
# commit, so from then on the live server imports old code: on 2026-09-20 the
# Gemini live router vanished from a running server that way, and the voice
# engine picker disappeared from the UI with it.
set -eu
ARENA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${ARENA_REPO:-$(cd "$ARENA/../../.." && pwd)}"
SP="$REPO/.venv/lib/python3.12/site-packages"

# Resolve from outside the checkout: inside it, cwd would mask the pointer.
got="$(cd / && "$REPO/.venv/bin/python" -c 'import omnigent; print(omnigent.__file__)' 2>/dev/null || true)"
case "$got" in
  "$REPO"/*) echo "venv ok: $got"; exit 0 ;;
esac

echo "VENV HIJACKED: omnigent resolves to ${got:-<import failed>}" >&2
if [ "${1:-}" != "--repair" ]; then
  echo "run: $0 --repair" >&2
  exit 1
fi

# Point every editable record back at the checkout, whatever tree grabbed it.
bad="$(printf '%s' "$got" | sed 's#/omnigent/__init__.py$##')"
[ -n "$bad" ] || { echo "cannot tell what to repair" >&2; exit 1; }
grep -rl -- "$bad" "$SP" 2>/dev/null | while read -r f; do
  sed -i "s#$bad#$REPO#g" "$f"
done
# The console scripts' shebangs get rewritten by the same install.
for s in omni omnigent; do
  [ -f "$REPO/.venv/bin/$s" ] && sed -i "1c #!$REPO/.venv/bin/python" "$REPO/.venv/bin/$s"
done
now="$(cd / && "$REPO/.venv/bin/python" -c 'import omnigent; print(omnigent.__file__)' 2>/dev/null || true)"
case "$now" in
  "$REPO"/*) echo "repaired: $now"; echo "restart omnigent-server.service to pick it up" ;;
  *) echo "repair failed, still $now" >&2; exit 1 ;;
esac
