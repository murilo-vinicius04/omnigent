# Proxmox / VM 103 / nexus (L40S) diagnosis — 2026-10-29

**Why:** the Proxmox host crashed (NIC hardware hang) and the user had to power it on in person. Task (Hub lane "Diagnóstico VM e rede Proxmox"): check VM 103 and its network, and find out why nexus (L40S) crashed and won't come back.

**Status: VM side done and healthy. Proxmox host and nexus NOT yet examined (no access from this VM).**

## VM 103 (`ubuntu-gpu`, run from inside it, user `nexus`) — healthy
- Up since 14:53 on 2026-10-29. KVM guest, RTX A2000 12GB (not the L40S), `nvidia-smi` clean (driver 595.99.02, 33C).
- NIC `enp6s18` (virtio) = `10.181.64.191/24`, gw `10.181.64.1`. Internet and DNS OK. The gateway does not answer ICMP, but its ARP entry is REACHABLE, so it probably just drops pings.
- Tailscale node `omnigent-vm` = 100.84.207.115. No failed system or user units. `omnigent-server`, `omnigent-host` and `fr3-dash-tunnel` are active. Disk 27% (126G of 489G), RAM 47G with plenty free.
- Boots on 2026-10-29: -4 ended 11:19, -3 11:22-11:25, -2 11:41-11:42, -1 11:45-11:46, 0 from 14:53. Boots -4 to -1 logged "Network is unreachable" for tailscaled and the lab tunnel (no network at all), which matches the host NIC hang. Normal since 14:53.
- Harmless noise: avahi `avahi_key_new() failed`, gcr-prompter "cannot open display".
- Risk: the DHCP lease `10.181.64.191` is not reserved (see `SERVER_MOVE_VM103_2026-10-27.md`).

## nexus (L40S) / Proxmox — open
- Tailscale: `nexus` 100.111.174.61 offline, last seen ~3h before 15:30. `pve` 100.82.54.27 online. `pve-winlocal` 100.121.118.16 offline.
- `ssh root@100.82.54.27` fails with "Host key verification failed": the key is not in this VM's hashed `known_hosts`. I did not bypass the check.
- The `pve` and `explora-vm` ssh aliases from the 10-27 move doc no longer exist in this VM's ssh config. `ssh pve` does not resolve. `~/.ssh` only has keys `id_ed25519_labdash` and `id_labgoto`. It is unknown whether either is authorized on `pve`.
- Nothing is known about why nexus is down. Hypotheses, unverified: it is on the same host and stuck after the NIC hang, or the L40S PCI passthrough did not recover after the hard power-on.

## Next steps (need user input first)
1. User confirms 100.82.54.27 is the real pve and says which user/key to use, or runs `! ssh root@100.82.54.27` themselves, or provides a PVE API token.
2. On pve: `qm list`, `pveversion`, `journalctl -b -1 -k | grep -iE "hang|NETDEV WATCHDOG|e1000|igb|ixgbe|r8169|tx timeout|reset adapter"`, `ip -br a`. Then check nexus's state and console, and L40S passthrough (`lspci -nnk`, vfio, `dmesg`).
3. Consider reserving the VM's DHCP lease and a persistent NIC fix on the host (e.g. disable offloads with `ethtool -K <nic> tso off gso off gro off` if it is the e1000e hang).

## Update 15:40 — pve acessado (Tailscale SSH aprovado)
- pve: PVE 8.4.0, kernel 6.8.12-9-pve, up desde 12:05 (reboot limpo via systemd às 12:04:59, sem crash/panic no log). VM 103 `ubuntu-gpu` running; VM 102 `windows-motive-local` stopped.
- NIC: sem "Hardware Unit Hang" no journal. e1000e (enp0s31f6, a que está na vmbr0) flapou link 11:50–12:00; atlantic enp1s0 (10.0.0.1, sem uso) flapa até 12:27. TSO/GSO/GRO já estão off na e1000e. Desde 12:05 a e1000e está estável.
- pve tem 2x RTX A2000 (55:00.0, a2:00.0) em vfio-pci; NÃO há L40S nesse host => nexus é outra máquina.
- nexus (100.111.174.61): `tailscale ping` do pve retorna "peer's node key has expired". Provável causa de estar offline no tailnet. Fix: admin console do Tailscale -> nexus -> desabilitar key expiry / reautenticar (`sudo tailscale up` no nexus).
