#!/bin/bash
# net-failover: keep the Proxmox host reachable when the wired NIC (Intel I219, e1000e) hangs.
#
# A hung I219 keeps "Link detected: yes", so the kernel keeps using the wired default route and the
# eduroam wifi (metric 600) never takes over. This probes each path with `ping -I <iface>` and:
#   wired down, wifi up   -> push the wired default route to metric 900 so wifi wins (tailscale follows),
#                            then try to repair the NIC (link bounce, later a driver reload)
#   wired back            -> restore the normal route
#   both down             -> assume an upstream outage: only nudge the wifi connection, never touch the NIC
# DRY_RUN=1 logs what it would do without changing anything.
set -u

WIRED=vmbr0
PORT=enp0s31f6
DRIVER=e1000e
GW=10.181.64.1
WIFI=wlp3s0
WIFI_CON=eduroam
TARGETS=${TARGETS:-"1.1.1.1 8.8.8.8 9.9.9.9"}
INTERVAL=${INTERVAL:-30}
FAIL_THRESHOLD=3          # consecutive wired failures before acting (3 x 30s)
RELOAD_AFTER=${RELOAD_AFTER:-5}  # failures before the driver reload (after the link bounce)
RELOAD_COOLDOWN=900       # seconds between driver reloads
RECOVER_THRESHOLD=2       # consecutive wired successes before restoring the route
DRY=${DRY_RUN:-0}

log() { logger -t net-failover -- "$*"; echo "$(date '+%F %T') $*"; }
act() { if [ "$DRY" = 1 ]; then log "DRY_RUN: $*"; else "$@"; fi; }

path_ok() {  # $1 = interface
    local t
    for t in $TARGETS; do
        ping -I "$1" -c1 -W2 -q "$t" >/dev/null 2>&1 && return 0
    done
    return 1
}

failover_on() {
    ip route show default | grep -q "dev $WIRED .*metric 900" && return 0
    log "wired path dead, wifi alive: moving default route to $WIFI"
    act ip route add default via "$GW" dev "$WIRED" onlink metric 900
    act ip route del default via "$GW" dev "$WIRED" metric 0
}

failover_off() {
    ip route show default | grep -q "dev $WIRED .*metric 900" || return 0
    log "wired path recovered: restoring default route on $WIRED"
    act ip route replace default via "$GW" dev "$WIRED" onlink metric 0
    act ip route del default via "$GW" dev "$WIRED" metric 900
}

repair_bounce() {
    log "repair 1: bouncing $PORT"
    act ip link set "$PORT" down
    act sleep 3
    act ip link set "$PORT" up
}

repair_reload() {
    log "repair 2: reloading $DRIVER and reapplying network config"
    act modprobe -r "$DRIVER"
    act sleep 2
    act modprobe "$DRIVER"
    act sleep 5
    act ifreload -a
    act systemctl restart intel-nic-fix.service
    # ifreload restores the normal route; the next failing tick re-applies the failover
}

wifi_nudge() {
    log "wifi path also dead: nudging $WIFI_CON"
    act nmcli connection up "$WIFI_CON"
}

wired_fails=0 wired_oks=0 last_reload=0 last_nudge=0 bounced=0

while true; do
    now=$(date +%s)
    if path_ok "$WIRED"; then
        wired_fails=0 bounced=0
        wired_oks=$((wired_oks + 1))
        [ "$wired_oks" -ge "$RECOVER_THRESHOLD" ] && failover_off
    else
        wired_oks=0
        wired_fails=$((wired_fails + 1))
        if [ "$wired_fails" -ge "$FAIL_THRESHOLD" ]; then
            if path_ok "$WIFI"; then
                failover_on
                [ "$bounced" = 0 ] && { repair_bounce; bounced=1; }
                if [ "$wired_fails" -ge "$RELOAD_AFTER" ] && [ $((now - last_reload)) -ge "$RELOAD_COOLDOWN" ]; then
                    repair_reload
                    last_reload=$now
                fi
            elif [ $((now - last_nudge)) -ge 300 ]; then
                wifi_nudge
                last_nudge=$now
            fi
        fi
    fi
    [ "${ONCE:-0}" = 1 ] && break
    sleep "$INTERVAL"
done
