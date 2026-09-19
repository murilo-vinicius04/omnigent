#!/usr/bin/env bash
# Bring a fresh clone to the same state this layer runs in: dependencies, the web
# bundle the server serves from disk, agent configs pointing at THIS checkout, and
# the two user services. Idempotent — safe to re-run after a pull.
#
#   deploy/friendly-layer/bootstrap.sh [--install-services] [--start]
set -euo pipefail

CHECKOUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
install_services=0; start=0
for arg in "$@"; do
  case "$arg" in
    --install-services) install_services=1 ;;
    --start) install_services=1; start=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
say() { printf '\n== %s\n' "$1"; }

say "checkout: $CHECKOUT"
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
command -v npm >/dev/null || { echo "node/npm is required (web bundle)" >&2; exit 1; }

say "python dependencies (uv sync --extra all --group dev)"
(cd "$CHECKOUT" && uv sync --extra all --group dev)

say "web bundle -> omnigent/server/static/web-ui"
# The server serves this from disk: it must be rebuilt after any web/src change.
(cd "$CHECKOUT/web" && npm ci && npm run build)

say "agent configs: absolute paths -> this checkout"
# nexus dispatches workers by absolute config_path (read relative to the runner's
# workspace otherwise, which differs per session), so these are rewritten in place.
mapfile -t stale < <(grep -rl '/home/nexus/wt/friendly-layer' "$CHECKOUT/examples" 2>/dev/null || true)
if [ "${#stale[@]}" -gt 0 ] && [ "$CHECKOUT" != "/home/nexus/wt/friendly-layer" ]; then
  sed -i "s#/home/nexus/wt/friendly-layer#$CHECKOUT#g" "${stale[@]}"
  printf '   rewrote: %s\n' "${stale[@]}"
else
  echo "   nothing to rewrite"
fi

say "systemd user units"
render() {  # <template> -> <unit path>
  sed -e "s#@CHECKOUT@#$CHECKOUT#g" -e "s#@HOME@#$HOME#g" -e "s#@PATH@#$PATH#g" "$1"
}
if [ "$install_services" = 1 ]; then
  mkdir -p "$UNITS"
  for t in "$CHECKOUT"/deploy/friendly-layer/*.service.in; do
    unit="$(basename "${t%.in}")"
    render "$t" > "$UNITS/$unit"
    echo "   wrote $UNITS/$unit"
  done
  systemctl --user daemon-reload
  loginctl enable-linger "$USER" >/dev/null 2>&1 || true   # services survive logout
  if [ "$start" = 1 ]; then
    systemctl --user enable --now omnigent-server.service omnigent-host.service
    echo "   started; http://127.0.0.1:6767"
  else
    echo "   start with: systemctl --user enable --now omnigent-server omnigent-host"
  fi
else
  echo "   skipped (pass --install-services, or --start to enable them too)"
  echo "   preview: $CHECKOUT/deploy/friendly-layer/omnigent-server.service.in"
fi

cat <<TXT

Next, and only what you need:
  * Claude worker      : claude login (Claude Code CLI)
  * Gemini worker      : the antigravity CLI, logged in; nexus needs bypassPermissions
  * OpenAI worker      : put the key in \$OMNIGENT_DATA_DIR/openai-key (default ~/.omnigent)
  * Spoken summary/TTS : see docs/FRIENDLY_LAYER_SETUP.md
  * Benchmark          : dev/benchmarks/nexus_arena/README.md
TXT
