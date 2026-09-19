#!/usr/bin/env bash
# Install the Omnigent server + host as permanent systemd user units.
#
# Replaces the transient `systemd-run` units that vanished on reboot and ran
# the host from a different checkout. Re-run after editing either unit file.
# Restarting the host interrupts every open session's runner.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
units="$HOME/.config/systemd/user"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# The units are templates: they carry no machine's paths. Render them for THIS
# checkout, so a fresh clone installs units that point at itself.
checkout="$(cd "$here/../.." && pwd)"
mkdir -p "$units"
for template in "$here"/*.service.in; do
  unit="$(basename "${template%.in}")"
  sed -e "s#@CHECKOUT@#$checkout#g" -e "s#@HOME@#$HOME#g" -e "s#@PATH@#$PATH#g" \
      "$template" > "$units/$unit"
  chmod 0644 "$units/$unit"
done

# A transient unit of the same name shadows the file; stop it first.
for unit in omnigent-host.service omnigent-server.service; do
  if systemctl --user show "$unit" -p Transient --value 2>/dev/null | grep -qx yes; then
    systemctl --user stop "$unit"
  fi
done

systemctl --user daemon-reload
systemctl --user enable omnigent-server.service omnigent-host.service
systemctl --user restart omnigent-server.service
systemctl --user restart omnigent-host.service

# Keep user units running without a login session (survives reboot).
loginctl enable-linger "$(id -un)" 2>/dev/null \
  || echo "note: 'loginctl enable-linger $(id -un)' needs admin rights; run it with sudo"

systemctl --user is-active omnigent-server.service omnigent-host.service
