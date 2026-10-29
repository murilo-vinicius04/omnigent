# Moving the Omnigent server to VM 103 — state and runbook (2026-10-27)

**Status: PAUSED by the user.** A working side-by-side copy of the server runs on VM 103; the real switch is
NOT done and waits for the user's explicit go ("later, after I finish the other things"). Nothing on nexus
was changed except one inert unit file (see "Prepared, not used").

## Handoff at 18:30 (turn interrupted by the 5h plan window at 99%, resets 21:20)

- **Re-checked 2026-10-28 02:58:** VM `omnigent-server` and `omnigent-host` units still `active`, `omnigent-vm` node
  still on the tailnet, both servers answer HTTP (the `/v1/health` path is a 404 on both, so it is not a health URL; use
  `/v1/me` with a token). Nothing changed since the 18:30 handoff.
- **In the middle of nothing.** The last thing done was writing this doc and the `RESUME.md` block. No background job of
  mine is left (login ssh exited, tunnel killed, copy script finished). Nexus server/host untouched.
- **Decided by the user:** keep the VM copy as a side-by-side test; do NOT switch now ("later, I'll finish other things");
  no cosmetic changes to either page (banner reverted); `agy` credentials may be copied (done); 2nd host on nexus
  NOT requested for now.
- **Done:** sections 1-4 below (VM server + host + HTTPS address + agy + verified copy + runbook).
- **Open, waiting on the user:** (a) the go for the real switch, (b) address after the switch: new name (A) vs nexus URL
  kept alive by a forward (B), (c) which CLIs to install on the VM (Claude Code, Muse, Codex; separate logins),
  (d) copy `openai-key`/`openai-budget-token`/`gemini.env` at the switch, (e) fix the `model-options` 500 or leave it,
  (f) delete the inert `omnigent-host-vm.service` on nexus or keep it, (g) Hipólito's host.
- **Exact next step:** when the user says "switch", start with the preconditions of section 6 (lanes idle + compacted,
  `tailscale ping desktop-3chlku2` calm, `df` on both machines), then section 6 step 1 (code sync), asking for the go on
  each outward step. If they ask for the side-by-side test instead, section 5: they run the throwaway-HOME login
  (`! mkdir -p /tmp/omni-login-home && HOME=/tmp/omni-login-home /home/nexus/wt/friendly-layer/.venv/bin/omni login https://omnigent-vm.tail0994ab.ts.net`),
  then I merge the token via `omnigent.cli_auth` and start `omnigent-host-vm`.
- **Identifiers:** VM `ssh explora-vm`; Tailscale node `omnigent-vm` 100.84.207.115; VM host `ubuntu-gpu`
  `35d18f5e5f434f168f45553c3ef45c54`; nexus host id `8661c0d6534c4314923985bdaa550a10`; copy `chat.db` sha256 `9fb2c5d26258a945`;
  secret `~/.omnigent/artifacts/accounts-cookie-secret`; VM units `~/.config/systemd/user/omnigent-{server,host}.service`.

Goal (user's words): the server mirrored on VM 103, used exactly like the one on nexus; test it first with both
running, switch definitively later. The Hub router choosing the machine is a separate, still-open item
(`HUB_MULTIMACHINE_HANDOFF_2026-10-26.md`).

## 1. What exists right now (checked 2026-10-27 18:12 -03)

| piece | where | state |
| --- | --- | --- |
| live server | nexus (L40S), `omnigent-server.service`, `0.0.0.0:6767`, `https://nexus.tail0994ab.ts.net` via `tailscale serve` | untouched, healthy |
| live host | nexus, `omnigent-host.service` -> `http://127.0.0.1:6767` | untouched (58 child procs, 1.4 GB after 22 h) |
| copy of the server | VM 103 `ubuntu-gpu` (`ssh explora-vm`, reached through the `pve` jump), `omnigent-server.service` (user unit, enabled, linger on), `127.0.0.1:6767`, `OMNIGENT_AUTH_ENABLED=1` | active, 287 MB RSS |
| VM host | VM 103, `omnigent-host.service` -> `http://127.0.0.1:6767`, host name `ubuntu-gpu`, id `35d18f5e5f434f168f45553c3ef45c54` | active, online, 128 MB |
| HTTPS address of the copy | Tailscale node `omnigent-vm` (100.84.207.115), `sudo tailscale serve --bg 6767` -> **https://omnigent-vm.tail0994ab.ts.net**, tailnet only | working, valid cert |
| agy on the VM | `~/.local/bin/agy` (1.4.8) + `~/.gemini/antigravity-cli/antigravity-oauth-token` | `agy models` works; no real model call made |

The copy's data is a snapshot taken 2026-10-27 17:42 (`chat.db` sha256 `9fb2c5d26258a945`, 4,575,117,312 bytes,
378,204 items, 822 conversations, 7 Hub projects). **Anything created on the VM instance is a test copy and is
overwritten at the switch.** The Hub projects/lanes in the copy are bound to host `nexus`, which is offline there.

VM units live in `~/.config/systemd/user/` on the VM; the original (unedited) server unit is kept as
`omnigent-server.service.rehearsal-orig`. Changes vs nexus's unit: `--host 127.0.0.1` instead of `0.0.0.0`,
plus `Environment=OMNIGENT_AUTH_ENABLED=1`, plus a different Description.

### Copied to the VM
Code: `~/wt/friendly-layer` (without `.venv`, `node_modules`, `.git`) and `~/omnigent-fork/examples/debby`
(snapshot of ~15:10, uncommitted files included). Venv: the EXACT 238 pins of the live venv.
State in `~/.omnigent`: `chat.db`, `artifacts/` (277 MB, includes the cookie secret), `hub/`, `companion/`,
`android/`, `models/`, the usage/quota ledgers and `plan-limits-*.json`, `voice.md`, `dictation-vocab.txt`.
agy: the 219 MB program, `antigravity-oauth-token`, `bin/`, and a `config.json` with only
`queuedMessageDeliveryStrategy` (no history, no installation id, no `remoteControlHostname`).

### NOT copied (decide at the switch)
- Credentials the server uses: `~/.omnigent/openai-key`, `openai-budget-token`, `gemini.env` (Live voice, the
  OpenAI budget gateway). The server serves them, so they MUST move at the switch or those features break.
- `config.yaml` (it holds nexus's host id; never put that file on a machine that is not that host). The
  `providers: openai-budget` block has `base_url: http://127.0.0.1:6767/v1/openai-budget/v1`.
- Host-owned dirs (`claude-history`, `codex-native`, `antigravity-native`, `codex-home`, `daemons`, `logs`).
- Claude Code, Codex, Muse, other CLIs and their logins. Only `agy` works on the VM; the host *lists* every
  harness name but that is a catalog, not what is installed. Use SEPARATE interactive logins there, never copy
  credential files (rotating refresh tokens can log the other machine out).
- The Whisper model cache for server-side dictation (`OMNIGENT_DICTATION_ENGINE=whisper`) downloads on first use.

## 2. Measured facts (use these, don't re-derive)

- Data: `chat.db` 4.57 GB (`conversation_items` ~2.6 GB, FTS ~1.6 GB), `artifacts` 277 MB, ~1 GB/week growth.
  zstd compresses the DB 7:1 (653 MB). A consistent snapshot takes ~9-13 s (SQLite backup API, `quick_check` ok,
  FTS `integrity-check` ok on snapshot 2).
- **Delta sync is cheap:** snapshot 2 (25 min newer) -> 4.5 s, 18 MB literal; snapshot 3 -> ~1 min incl. a
  fresh snapshot, 20 MB literal. sha256 matched every time. So the server downtime of a switch is ~1-2 min.
- **All Tailscale traffic from nexus rides the DERP relay `sao`** (hard NAT: `MappingVariesByDestIP: true`, no
  direct path to any peer) and one TCP stream is capped around 2.8 MB/s total. A bulk transfer queues everything
  behind it: on 10-27 Matheus's rsync of `~/hiveboard_hf_staging` (4 senders, ~24 Mbit/s) pushed pings to the
  user's PC to 1.3 s while nexus itself was idle. **Always `--bwlimit=1800`** for our copies and check
  `tailscale ping desktop-3chlku2` afterwards (15-35 ms is calm).
- Nexus root fills fast (`~/manip_fail_hiveboard/demos`, ~100 GB in 2 days; 128 GB free at 10-27 17:00).
- VM: 350 GB free, 8 cores, A2000 12 GB (idle at 17 MiB when checked), shared with the Explora benchmark and the
  FR3 sim. Keep VM work niced. DHCP lease `10.181.64.191` was stable all day but is NOT reserved. The VM disk has no
  `discard=on` (deleted temp files stay in the Proxmox thin pool).
- The server restart is safe for lanes (6 s restart left every lane unchanged); a HOST restart interrupts runners.

## 3. Traps found (all verified in code or live)

1. **Accounts (login) mode turns on by itself when the server binds a non-loopback address** with no explicit auth
   (`cli.py` ~1450, `resolve_auth_source`). Loopback bind = single-user mode, `/v1/me` = `local`, no login, and
   an empty `Hub` conversation for `local` is created. The VM unit binds loopback and sets `OMNIGENT_AUTH_ENABLED=1`.
2. **The cookie/token signing secret is `~/.omnigent/artifacts/accounts-cookie-secret`** (cli.py passes the
   artifacts dir). `~/.omnigent/accounts-cookie-secret` (Oct 21) is a different, unused file. With the right
   secret copied, a nexus CLI token works on the copy as `morys5`; without it every call is 401.
3. **Stale `chat.db-wal`/`-shm` next to a replaced DB corrupt it.** Stop the VM server, delete both, then swap the
   file. (Even a read-only open recreates them.)
4. **`omni login <url>` rewrites `server:` in the real `~/.omnigent/config.yaml`** (no flag to skip) and would
   repoint every tool on that machine. For a side-by-side test log in with a throwaway home:
   `HOME=/tmp/omni-login-home .venv/bin/omni login <url>`, then merge the token entry into the real
   `auth_tokens.json` through `omnigent.cli_auth` (it takes the lock). At the real switch the config default SHOULD
   change, so log in normally there.
5. The VM server mints its own 8 h token for `morys5` into the VM's `auth_tokens.json` (no refresh token). The VM
   host runs on it; for a long-lived one the user runs `omni login http://127.0.0.1:6767` there and types the password.
6. A host daemon connects to ONE server (the host class takes one `server_url`); daemon records are per target
   (`~/.omnigent/daemons/<target-hash>.json`), so a second daemon for a second server is supported.
7. The orphan sweep (`reap_orphaned_native_bridge_dirs`) is local and pid-based; it never asks a server which
   sessions exist. (An earlier note of mine claimed a stale copy could make live lanes look orphaned: wrong.)
8. A conversation id that exists on both servers must not be driven from both pages at once: both would start a
   runner for it on the same machine (not tested, but that is what the code paths imply).
9. Bug seen on the copy: `GET /v1/hosts/{id}/harnesses/{h}/model-options` returns 500 (`hosts.py:784`, the
   `error` field is a string where the response model wants a list) when the host lacks that CLI. Harmless to
   data; not fixed.
10. The Gemini login is shared by file copy; if `agy` on nexus ever asks to log in again, that is the likely cause.
11. Do not move the `nexus` tailnet name: it also fronts `:8443` and `:8447` (FR3 pad / lab dashboard).

## 4. Verified vs not verified

Verified (with numbers above): snapshot integrity, byte-identical copy, throwaway server start, login mode,
copied secret accepts the nexus token, 7 Hub projects match, FTS search works, delta sync, the new VM server and host
start clean (0 warnings), host registers online, HTTPS address works from nexus, web bundle hash equals nexus's
(`index-D5JtgRZV.js`), `agy models` works on the VM.
NOT verified: browser login and the live-updates WebSocket on the VM address, any real lane running on the VM,
how long hosts/runners tolerate a server gap of ~1 min, the Android app on a new address (is its address
configurable?), Hipólito's host against the VM.

## 5. Prepared, not used: second host on nexus for a side-by-side test

`~/.config/systemd/user/omnigent-host-vm.service` on nexus (inactive, disabled):
`omni host --server https://omnigent-vm.tail0994ab.ts.net --non-interactive`, same host identity as the live host,
so the VM page would show `nexus` online with all its tools. A fresh daemon costs ~130 MB + ~100 MB.
Needs a login to the VM server first (trap 4, throwaway HOME). Start only with the user's go; delete the unit
file if the idea is dropped. Obey trap 8.

## 6. Runbook for the real switch (needs the user's explicit go; nothing here is rehearsed end to end)

Preconditions: quiet moment, no lane mid-turn; compact lanes first (a host restart interrupts runners and burns
quota, see the `compact-before-host-restart` memory); `tailscale ping desktop-3chlku2` calm; check `df` on both
machines; decide the address (below).

1. **Sync the code** (nexus is still serving): `rsync -a --exclude=/.venv --exclude=/node_modules --exclude=/.git
   --exclude=__pycache__ ~/wt/friendly-layer/ explora-vm:wt/friendly-layer/` and `examples/debby`. Compare
   `uv pip freeze` of both venvs; if they differ, reinstall the nexus pins with `uv pip install --no-deps -r`.
   (`uv.lock` is stale, `uv sync --frozen` cannot rebuild this venv.)
2. **Stop the VM server and host**, `rm -f ~/.omnigent/chat.db-wal ~/.omnigent/chat.db-shm` on the VM.
3. **Stop the nexus server** (`systemctl --user stop omnigent-server`, then `mask` it so nothing restarts it; untested: the
   host unit has `Wants=omnigent-server.service`, check what systemd does with a masked one). Lanes keep running;
   the UI is down until step 5.
4. **Final snapshot + delta** (≈1-2 min), with `XDG_RUNTIME_DIR=/run/user/1000`:
   ```
   cd ~/wt/friendly-layer && nice -n 10 ionice -c2 -n7 .venv/bin/python -I - <<'EOF'
   import sqlite3
   src = sqlite3.connect("file:/home/nexus/.omnigent/chat.db?mode=ro", uri=True, timeout=60)
   dst = sqlite3.connect("<scratch>/chat.db"); src.backup(dst, pages=-1); dst.close(); src.close()
   EOF
   rsync -a --inplace --no-whole-file -z --bwlimit=1800 --stats --rsync-path="nice -n 19 rsync" \
     <scratch>/chat.db explora-vm:.omnigent/chat.db        # then sha256sum on both ends
   rsync -a -z --bwlimit=1800 ~/.omnigent/{artifacts,hub,companion,android,models} explora-vm:.omnigent/
   ```
   plus the ledgers/`plan-limits-*.json`/`voice.md` files, and (user's OK) `openai-key`, `openai-budget-token`,
   `gemini.env` (trap in section 1). Re-apply the `config.yaml` `providers:` block on the VM WITHOUT the `host:` section.
5. **Start the VM server**, check `/health`, `/v1/me`, hub projects, host list (use the VM-minted token).
6. **Repoint the hosts** (this restarts the host and interrupts runners):
   - nexus host: `omni login https://omnigent-vm.tail0994ab.ts.net` (password; here the config default MAY change),
     edit `omnigent-host.service` `--server`, `daemon-reload`, restart. Update `server:` and the `openai-budget`
     `base_url` in `~/.omnigent/config.yaml`. Grep for other consumers of `127.0.0.1:6767` (timers such as
     `omnigent-claude-pro-transition.timer`, voice/Unmute relays, scripts) and fix or forward them.
   - Hipólito's tower (`omnigent-nexus-host.service`, his user `murilo`, ssh key per the machine-sessions
     memory): same `--server`, needs a login from there.
   - VM host `ubuntu-gpu` stays as is. Install Claude Code/Codex/Muse on the VM only if lanes should run there.
7. **Address.** Option A: everyone uses `https://omnigent-vm.tail0994ab.ts.net` (phone app needs the new
   address; unchecked whether it can be changed). Option B (untested): keep the old nexus URL alive by running an
   ssh forward on nexus `127.0.0.1:6767 -> VM 127.0.0.1:6767`, so `tailscale serve` on nexus, hard-coded local
   clients and Hipólito's host need no change (cost: one more hop through the relay).
8. **Verify:** open the page, log in, run a lane on `nexus` and one on `ubuntu-gpu`, Hub message and placement,
   usage page, plan rings, Live voice, dictation (first use downloads the Whisper model onto the A2000), phone.
9. **Keep nexus's data ≥7 days** as the fallback; reserve the VM's IP; consider `discard=on` on `scsi0` + `fstrim`.

**Rollback** (clean within the first hour; the nexus DB is frozen at step 3): stop the VM server, repoint the hosts
back, `unmask` + start the nexus server. If the VM took data worth keeping, snapshot the VM DB with the same
backup API and delta it back to nexus (stop the nexus server and delete its `-wal`/`-shm` first).

## 7. Stop or remove the test instance (not done; costs ~415 MB RAM on the VM)

On the VM: `systemctl --user disable --now omnigent-host omnigent-server` and `sudo tailscale serve --https=443 off`;
to leave the tailnet: `sudo tailscale logout` and delete the `omnigent-vm` device in the admin console.
