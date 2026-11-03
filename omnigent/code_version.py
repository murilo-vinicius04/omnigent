"""Identify the exact source tree a process runs, so hosts can be compared with the server.

The id is a digest over the content of every file git would track or list as untracked
(ignored files excluded), so uncommitted work counts. Files that are local to one machine
are left out of it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: Files each machine keeps its own copy of; they never count toward the id or travel in a bundle.
HOST_LOCAL_FILES = frozenset({"examples/nexus/config.yaml"})

_GIT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class CodeVersion:
    """The source tree of one checkout.

    :param id: 16 hex characters of the content digest, e.g. ``"08163d9f3fdb5e6e"``.
    :param label: Short text for people: commit plus changed-file count, e.g. ``"614f4cd8+35"``.
    :param root: Absolute path of the checkout.
    """

    id: str
    label: str
    root: str


def _git(root: Path, *args: str) -> bytes | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, timeout=_GIT_TIMEOUT_S, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout


def find_root(start: Path | None = None) -> Path | None:
    """Return the git checkout this package runs from, or ``None`` for an installed wheel.

    :param start: Directory to search upward from; defaults to this package's directory.
    """
    here = (start or Path(__file__).resolve().parent).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def file_list(root: Path) -> list[str] | None:
    """Return the sorted paths that make up the tree, or ``None`` when git cannot list them.

    :param root: Checkout directory.
    """
    out = _git(root, "ls-files", "-co", "--exclude-standard", "-z")
    if out is None:
        return None
    names = (n.decode() for n in out.split(b"\0") if n)
    return sorted(
        n
        for n in names
        if n not in HOST_LOCAL_FILES and ((root / n).is_symlink() or (root / n).is_file())
    )


def entry_bytes(path: Path) -> bytes | None:
    """Return what represents *path* in the digest: file content, or the link text of a symlink.

    :param path: A path from :func:`file_list`.
    :returns: The bytes, or ``None`` when the path cannot be read.
    """
    try:
        if path.is_symlink():
            return b"\0link\0" + os.readlink(path).encode()
        return path.read_bytes()
    except OSError:
        return None


def compute(root: Path | None = None) -> CodeVersion | None:
    """Digest the checkout's tree.

    :param root: Checkout directory; defaults to the one this package runs from.
    :returns: The version, or ``None`` when there is no checkout or git cannot read it.
    """
    root = root or find_root()
    if root is None:
        return None
    names = file_list(root)
    if names is None:
        return None
    digest = hashlib.sha256()
    for name in names:
        data = entry_bytes(root / name)
        if data is None:
            continue
        digest.update(name.encode() + b"\0" + hashlib.sha1(data).digest())
    head = _git(root, "rev-parse", "--short=8", "HEAD")
    status = _git(root, "status", "--porcelain", "-z")
    changed = (
        sum(1 for e in status.split(b"\0") if e and e[3:].decode() not in HOST_LOCAL_FILES)
        if status is not None
        else 0
    )
    sha = head.decode().strip() if head else "nogit"
    label = f"{sha}+{changed}" if changed else sha
    return CodeVersion(id=digest.hexdigest()[:16], label=label, root=str(root))


_loaded: CodeVersion | None = None
_loaded_done = False
_lock = threading.Lock()


def loaded() -> CodeVersion | None:
    """Return the version computed the first time this was called, i.e. what the process loaded.

    Call it early (server and host startup) so later edits on disk do not change the answer.
    """
    global _loaded, _loaded_done
    with _lock:
        if not _loaded_done:
            _loaded = compute()
            _loaded_done = True
        return _loaded


_DISK_TTL_S = 30.0
_disk: tuple[float, CodeVersion | None] | None = None


def on_disk(max_age_s: float = _DISK_TTL_S) -> CodeVersion | None:
    """Return the checkout's current tree, reusing an answer younger than *max_age_s*.

    :param max_age_s: How long a computed answer may be reused.
    """
    global _disk
    now = time.monotonic()
    with _lock:
        if _disk is not None and now - _disk[0] < max_age_s:
            return _disk[1]
    fresh = compute()
    with _lock:
        _disk = (time.monotonic(), fresh)
    return fresh


def update_state(
    reported: Mapping[str, str | None] | None,
    *,
    loaded: CodeVersion | None,
    disk: CodeVersion | None,
) -> str | None:
    """Say whether a connected host runs the server's code.

    A host counts as current when it matches what the server loaded or what is on the
    server's disk (the two differ for a while after an edit), or when it runs from the
    server's own checkout and so picks up edits on restart.

    :param reported: The host's ``{"id", "label", "root"}``, or ``None`` when offline.
    :param loaded: The server's loaded version.
    :param disk: The server's version on disk now.
    :returns: ``"current"``, ``"outdated"``, ``"unknown"`` (the host reports no version, so
        it predates this check), or ``None`` when there is nothing to compare.
    """
    if reported is None or (loaded is None and disk is None):
        return None
    host_id = reported.get("id")
    if host_id is None:
        return "unknown"
    mine = {v.id for v in (loaded, disk) if v is not None}
    roots = {v.root for v in (loaded, disk) if v is not None}
    if host_id in mine or reported.get("root") in roots:
        return "current"
    return "outdated"
