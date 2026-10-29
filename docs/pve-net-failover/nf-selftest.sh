#!/bin/bash
# nf-selftest: exercise net-failover.sh by dropping the host's outgoing ICMP on vmbr0 (forwarded VM
# traffic and tailscale are untouched). Modes: dry | live | cleanup. cleanup is idempotent and is also
# what the safety timers run, so the network always ends on the wired default route.
LOG=/var/log/nf-selftest.log
GW=10.181.64.1
UNIT=nf-under-test
BLOCK=(OUTPUT -o vmbr0 -p icmp -j DROP)

say() { echo "$(date '+%F %T') $*" >>"$LOG"; }
wired_default() { ip -4 route show default | grep -q "dev vmbr0 .*metric 900"; }

cleanup() {
    while iptables -D "${BLOCK[@]}" 2>/dev/null; do :; done
    systemctl stop "$UNIT.service" 2>/dev/null
    ip route show default | grep -q "dev vmbr0" && ! wired_default || {
        ip route add default via "$GW" dev vmbr0 onlink 2>/dev/null
        ip route del default via "$GW" dev vmbr0 metric 900 2>/dev/null
    }
    say "cleanup: $(ip -4 route show default | tr '\n' ';')"
}

wait_for() {  # $1 = seconds, rest = condition command
    local end=$((SECONDS + $1)); shift
    while [ $SECONDS -lt $end ]; do "$@" && return 0; sleep 2; done
    return 1
}

wired_route_normal() { ! wired_default && ip route get 1.1.1.1 | grep -q "dev vmbr0"; }
fail() { say "RESULT: FAIL - $*"; exit 1; }

mode=${1:-}
[ "$mode" = cleanup ] && { cleanup; exit 0; }
[[ "$mode" = dry || "$mode" = live ]] || { echo "usage: $0 dry|live|cleanup"; exit 2; }

trap cleanup EXIT
say "=== selftest $mode start; baseline: $(ip -4 route show default | tr '\n' ';')"
ping -I vmbr0 -c2 -W2 -q 1.1.1.1 >/dev/null || fail "precondition: wired path not working"
ping -I wlp3s0 -c2 -W2 -q 1.1.1.1 >/dev/null || fail "precondition: wifi path not working"
iptables -I "${BLOCK[@]}"
say "ICMP block on vmbr0 inserted"

if [ "$mode" = dry ]; then
    DRY_RUN=1 INTERVAL=5 timeout 60 /usr/local/sbin/net-failover.sh >>"$LOG" 2>&1
    grep -q "DRY_RUN: ip route add default via $GW dev vmbr0 onlink metric 900" "$LOG" || fail "dry: no failover decision"
    wired_default && fail "dry: route was changed"
    say "RESULT: PASS (dry) - failover decision made, routes untouched"
    exit 0
fi

systemd-run --unit="$UNIT" -E RELOAD_AFTER=999 -E INTERVAL=10 /usr/local/sbin/net-failover.sh >/dev/null
wait_for 180 wired_default || fail "live: no failover within 180s"
say "failover active after ${SECONDS}s: $(ip -4 route show default | tr '\n' ';')"
ip route get 1.1.1.1 | grep -q "dev wlp3s0" || fail "live: traffic not on wifi"
ping -c2 -W3 -q 1.1.1.1 >/dev/null || fail "live: no internet on wifi path"
tailscale status --json | grep -q '"Online": *true' || fail "live: tailscale offline on wifi"
say "on wifi: internet ok, tailscale online"
sleep 20
iptables -D "${BLOCK[@]}"
say "ICMP block removed"
wait_for 180 wired_route_normal || fail "live: did not return to wired within 180s"
say "back on wired: $(ip -4 route show default | tr '\n' ';')"
ping -I vmbr0 -c2 -W2 -q 1.1.1.1 >/dev/null || fail "live: wired path broken after return"
ethtool enp0s31f6 | grep -q "Link detected: yes" || fail "live: NIC link down"
ping -c2 -W2 -q 10.181.64.191 >/dev/null || fail "live: VM 103 unreachable after return"
say "RESULT: PASS (live) - failed over to wifi and returned to wired"
