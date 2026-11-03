"""Pack a checkout's source tree into one archive and apply such an archive to another checkout.

The server builds the bundle from the tree it runs; a host applies it so both hold the same
files. Applying keeps a backup of everything it replaces and rolls back if it fails, and it
never touches ``.git``, ignored files or the machine-local files in
:data:`omnigent.code_version.HOST_LOCAL_FILES`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from omnigent import code_version

MANIFEST = ".omnigent-bundle.json"
BACKUP_MANIFEST = ".omnigent-backup.json"
#: Changing either means the environment may need a ``uv sync``.
DEPENDENCY_FILES = ("pyproject.toml", "uv.lock")


class BundleError(Exception):
    """The bundle is unusable or applying it failed (the checkout was restored)."""


@dataclass(frozen=True)
class Applied:
    """What :func:`apply` did.

    :param written: Files created or replaced.
    :param removed: Files deleted because the bundle no longer has them.
    :param dependencies_changed: Whether a dependency file changed.
    :param backup: Archive holding the previous versions, for rollback by hand.
    :param version: The checkout's version afterwards.
    """

    written: int
    removed: int
    dependencies_changed: bool
    backup: Path
    version: code_version.CodeVersion


def _safe(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and bool(path.parts)
        and path.parts[0] != ".git"
        and name not in code_version.HOST_LOCAL_FILES
    )


def build(root: Path, dest: Path) -> code_version.CodeVersion:
    """Write the tree under *root* to a gzip tar at *dest*.

    :param root: Checkout directory.
    :param dest: Archive to create.
    :returns: The version of exactly what the archive holds.
    :raises BundleError: If *root* is not a checkout git can list.
    """
    names = code_version.file_list(root)
    if names is None:
        raise BundleError(f"{root} is not a git checkout")
    digest = hashlib.sha256()
    files: dict[str, str] = {}
    with tarfile.open(dest, "w:gz") as tar:
        for name in names:
            path = root / name
            data = code_version.entry_bytes(path)
            if data is None:
                continue
            sha1 = hashlib.sha1(data)
            digest.update(name.encode() + b"\0" + sha1.digest())
            files[name] = sha1.hexdigest()
            info = tarfile.TarInfo(name)
            info.mtime = int(time.time())
            if path.is_symlink():
                info.type = tarfile.SYMTYPE
                info.linkname = os.readlink(path)
                tar.addfile(info)
            else:
                info.size = len(data)
                info.mode = path.stat().st_mode & 0o777
                tar.addfile(info, io.BytesIO(data))
        version = code_version.CodeVersion(id=digest.hexdigest()[:16], label="", root=str(root))
        manifest = json.dumps({"id": version.id, "files": files}).encode()
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(manifest)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(manifest))
    return version


def read_manifest(bundle: Path) -> dict[str, object]:
    """Return the manifest (``id`` and ``files``) of a bundle.

    :param bundle: Archive made by :func:`build`.
    :raises BundleError: If the archive is unreadable or has no valid manifest.
    """
    try:
        with tarfile.open(bundle) as tar:
            member = tar.extractfile(MANIFEST)
            manifest = json.load(member) if member else None
    except (OSError, KeyError, tarfile.TarError, ValueError) as exc:
        raise BundleError(f"unreadable bundle: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise BundleError("bundle has no manifest")
    return manifest


def _sha1(path: Path) -> str | None:
    data = code_version.entry_bytes(path)
    return hashlib.sha1(data).hexdigest() if data is not None else None


def _prune(root: Path, name: str) -> None:
    parent = (root / name).parent
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def restore(root: Path, backup: Path) -> None:
    """Put back what *backup* holds and delete what the failed apply created."""
    with tarfile.open(backup) as tar:
        member = tar.extractfile(BACKUP_MANIFEST)
        created = json.load(member)["created"] if member else []
        for name in created:
            (root / name).unlink(missing_ok=True)
            _prune(root, name)
        for info in tar.getmembers():
            if info.name == BACKUP_MANIFEST or not _safe(info.name):
                continue
            target = root / info.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            if info.issym():
                os.symlink(info.linkname, target)
            else:
                source = tar.extractfile(info)
                if source:
                    target.write_bytes(source.read())
                    target.chmod(info.mode)


def apply(root: Path, bundle: Path, backup_dir: Path) -> Applied:
    """Make the checkout at *root* hold exactly the files in *bundle*.

    :param root: Checkout directory to update.
    :param bundle: Archive made by :func:`build`.
    :param backup_dir: Directory for the backup archive of replaced files.
    :returns: What changed.
    :raises BundleError: If the bundle is bad or the result does not match it; the
        checkout is restored first.
    """
    manifest = read_manifest(bundle)
    wanted: dict[str, str] = {str(k): str(v) for k, v in manifest["files"].items()}  # type: ignore[union-attr]
    if not all(_safe(n) for n in wanted):
        raise BundleError("bundle names a path outside the checkout")
    current = code_version.file_list(root)
    if current is None:
        raise BundleError(f"{root} is not a git checkout")
    to_write = [n for n, h in wanted.items() if _sha1(root / n) != h]
    to_remove = [n for n in current if n not in wanted]
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-before.tar.gz"
    created = [n for n in to_write if not (root / n).is_symlink() and not (root / n).exists()]
    with tarfile.open(backup, "w:gz") as tar:
        for name in [*to_write, *to_remove]:
            if (root / name).exists() or (root / name).is_symlink():
                tar.add(root / name, arcname=name, recursive=False)
        record = json.dumps({"created": created}).encode()
        info = tarfile.TarInfo(BACKUP_MANIFEST)
        info.size = len(record)
        tar.addfile(info, io.BytesIO(record))
    try:
        with tarfile.open(bundle) as tar:
            for name in to_write:
                member = tar.getmember(name)
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.issym():
                    target.unlink(missing_ok=True)
                    os.symlink(member.linkname, target)
                    continue
                source = tar.extractfile(member)
                if source is None:
                    raise BundleError(f"{name} is not a regular file in the bundle")
                data = source.read()
                if hashlib.sha1(data).hexdigest() != wanted[name]:
                    raise BundleError(f"{name} does not match its checksum")
                fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".update-")
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                os.chmod(tmp, member.mode & 0o777)
                os.replace(tmp, target)
        for name in to_remove:
            (root / name).unlink(missing_ok=True)
            _prune(root, name)
        after = code_version.compute(root)
        if after is None or after.id != manifest.get("id"):
            raise BundleError("the checkout does not match the bundle after applying it")
    except Exception as exc:
        restore(root, backup)
        if isinstance(exc, BundleError):
            raise
        raise BundleError(f"applying the bundle failed: {exc}") from exc
    changed = {*to_write, *to_remove}
    return Applied(
        written=len(to_write),
        removed=len(to_remove),
        dependencies_changed=any(n in changed for n in DEPENDENCY_FILES),
        backup=backup,
        version=after,
    )
