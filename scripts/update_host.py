#!/usr/bin/env python3
"""Bring an Omnigent checkout to the code a server runs, once, from a host too old to do it itself.

Standard library only. It downloads the server's code bundle, loads the bundle's own
``code_version`` and ``code_bundle`` modules, and applies it with the same backup and rollback
the host uses. Run it as the user that owns the checkout:

    python3 update_host.py --server https://omnigent-vm.tail0994ab.ts.net [--root PATH] [--restart]
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import urllib.error
import urllib.request
from pathlib import Path

_MODULES = ("omnigent/code_version.py", "omnigent/code_bundle.py")
_UNIT = "omnigent-host.service"


def stored_token(server: str, data_dir: Path) -> str | None:
    """Return the login saved by ``omni login`` for exactly this server URL."""
    try:
        entry = json.loads((data_dir / "auth_tokens.json").read_text()).get(server.rstrip("/"))
    except (OSError, ValueError):
        return None
    return entry.get("token") if isinstance(entry, dict) else None


def checkout_from_unit(unit_text: str) -> Path | None:
    """Pick the checkout out of ``systemctl cat`` output of the host unit."""
    for pattern in (r"^Environment=OMNIGENT_CHECKOUT=(\S+)", r"^WorkingDirectory=(\S+)"):
        found = re.search(pattern, unit_text, re.MULTILINE)
        if found:
            return Path(found.group(1)).expanduser()
    return None


def find_root(explicit: str | None) -> Path:
    """Locate the checkout: the flag, then the host unit, then the usual folder names."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    cat = subprocess.run(
        ["systemctl", "--user", "cat", _UNIT], capture_output=True, text=True, check=False
    )
    from_unit = checkout_from_unit(cat.stdout) if cat.returncode == 0 else None
    for candidate in (
        from_unit,
        *(Path.home() / n for n in ("omnigent-friendly-layer", "omnigent")),
    ):
        if candidate and (candidate / ".git").exists():
            return candidate.resolve()
    sys.exit("Could not find the checkout; pass --root PATH.")


def download(server: str, token: str | None, dest: Path) -> None:
    request = urllib.request.Request(server.rstrip("/") + "/v1/code/bundle")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=300) as resp, dest.open("wb") as out:
            shutil.copyfileobj(resp, out)
    except urllib.error.HTTPError as exc:
        hint = " Run `omni login` for this server first." if exc.code in (401, 403) else ""
        sys.exit(f"Download failed: HTTP {exc.code}.{hint}")


def load_bundle_modules(bundle: Path, work: Path) -> types.ModuleType:
    """Import ``code_bundle`` from the bundle itself, under a stand-in ``omnigent`` package."""
    with tarfile.open(bundle) as tar:
        for name in _MODULES:
            member = tar.extractfile(name)
            if member is None:
                sys.exit(f"{name} is missing from the bundle; the server is too old.")
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(member.read())
    package = types.ModuleType("omnigent")
    package.__path__ = [str(work / "omnigent")]  # type: ignore[attr-defined]
    sys.modules["omnigent"] = package
    return importlib.import_module("omnigent.code_bundle")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", required=True, help="server URL, as used with `omni login`")
    parser.add_argument("--root", help="checkout to update (default: found from the host unit)")
    parser.add_argument("--bundle", help="apply this bundle file instead of downloading one")
    parser.add_argument("--restart", action="store_true", help="restart the host unit afterwards")
    args = parser.parse_args(argv)

    root = find_root(args.root)
    data_dir = Path(os.environ.get("OMNIGENT_DATA_DIR", Path.home() / ".omnigent")).expanduser()
    work = Path(tempfile.mkdtemp(prefix="omnigent-update-"))
    try:
        bundle = Path(args.bundle) if args.bundle else work / "bundle.tar.gz"
        if not args.bundle:
            download(args.server, stored_token(args.server, data_dir), bundle)
        code_bundle = load_bundle_modules(bundle, work)
        try:
            done = code_bundle.apply(root, bundle, data_dir / "update-backups")
        except code_bundle.BundleError as exc:
            sys.exit(f"Not updated: {exc}")
        print(f"Updated {root}: {done.written} files written, {done.removed} removed.")
        print(f"Previous versions are in {done.backup}")
        if done.dependencies_changed:
            uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
            synced = Path(uv).exists() and (
                subprocess.run([uv, "sync", "--frozen"], cwd=root, check=False).returncode == 0
            )
            if not synced:
                code_bundle.restore(root, done.backup)
                sys.exit("Dependencies changed and `uv sync --frozen` did not succeed; restored.")
        if args.restart:
            subprocess.run(["systemctl", "--user", "restart", _UNIT], check=False)
            print(f"Restarted {_UNIT}.")
        else:
            print(f"Restart the host to load it: systemctl --user restart {_UNIT}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
