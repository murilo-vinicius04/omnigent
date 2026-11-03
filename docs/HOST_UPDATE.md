# Keeping hosts on the server's code

A host (`omni host`) runs agents with its own checkout of Omnigent. When that checkout differs
from the server's, behaviour differs too (for example agents on an older host have no duet
tools). The server now tells you when that happens and can bring the host up to date.

## What "same code" means

`omnigent/code_version.py` digests the content of every file git would track or list as
untracked (ignored files left out), so uncommitted work counts. Files that belong to one
machine (`HOST_LOCAL_FILES`, today `examples/nexus/config.yaml`) are never part of the digest
and never overwritten.

Each host reports the digest it **loaded** in its connect hello. `GET /v1/hosts` adds, per host:

| field | meaning |
| --- | --- |
| `code` | `{id, label, root}` the host reported, `null` when offline |
| `update.state` | `current`, `outdated`, `unknown` (host too old to report), or `null` (offline) |
| `update.server_label` | the server's label, e.g. `614f4cd8+57` |
| `update.last` | outcome of the last update request (`ok`, `busy`, `current`, `failed`) |

A host is `current` if it matches what the server loaded or what is on the server's disk, or if it
runs from the server's own checkout.

## What you see

The web UI shows a bar above the page for each online machine that is not current:

- **outdated**: "lab-pc runs older code (92692e5e; server 614f4cd8+57)" with an **Update** button.
  If runners are alive the host refuses and the bar offers **Update anyway**, which ends them.
- **unknown**: the machine is too old to update itself, so the bar shows a command to run on it once
  (it fetches `GET /v1/code/update-script` and applies the bundle). Afterwards the button works.

## How an update runs

1. `POST /v1/hosts/{id}/update` (owner only) sends a `host.update` frame down the tunnel.
2. The host downloads `GET /v1/code/bundle` with its own login and checks every file's checksum.
3. It writes the files, after saving the old versions to `<data dir>/update-backups/<time>-before.tar.gz` (`~/.omnigent` unless `OMNIGENT_DATA_DIR` is set).
   If anything fails, or the result does not match the bundle, it restores them.
4. If `pyproject.toml` or `uv.lock` changed it runs `uv sync --frozen` (restoring on failure).
5. It replies, then restarts its systemd unit a few seconds later. Without systemd it says to
   restart by hand.

Restarting ends every session on that host. Updating therefore refuses while runners are alive.

## Rolling back

```
tar -xzf <data dir>/update-backups/<time>-before.tar.gz -C <checkout>   # files that existed
```

Files the update created are listed in `.omnigent-backup.json` inside the archive; remove them.

## Restarting the server

Restart with `systemctl --user restart omnigent-server`. Hosts reconnect on their own and keep
their sessions. The server also writes a CLI login for "the local admin" into
`~/.omnigent/auth_tokens.json` on each boot, which the host re-reads when it reconnects; the unit's
drop-in `local-admin.conf` pins that to `morys5`. With another admin first in line the host was
refused with HTTP 409 and exited, ending its sessions.

## Tests

`tests/test_code_version.py`, `tests/test_code_bundle.py`, `tests/host/test_updater.py`,
`tests/server/test_code_route.py`, `tests/server/integration/test_hosts_update.py`,
`tests/test_update_host_script.py`, and `web/src/lib/hostUpdate.test.ts`.
