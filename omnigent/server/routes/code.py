"""Route that serves this server's source tree as a bundle, for hosts that update from it."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from omnigent import code_bundle, code_version
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user

_build_lock = threading.Lock()
_built: tuple[str, Path] | None = None


def _bundle_dir() -> Path:
    from omnigent_ui_sdk.terminal._config import state_dir

    return Path(state_dir()) / "code-bundles"


def _current_bundle() -> tuple[str, Path]:
    """Return ``(id, path)`` of a bundle of the tree on disk, reusing the last if unchanged."""
    global _built
    root = code_version.find_root()
    if root is None:
        raise HTTPException(status_code=404, detail="this server is not running from a checkout")
    with _build_lock:
        disk = code_version.compute(root)
        if disk is None:
            raise HTTPException(status_code=503, detail="cannot read the source tree")
        if _built is not None and _built[0] == disk.id and _built[1].exists():
            return _built
        folder = _bundle_dir()
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"code-{disk.id}.tar.gz"
        try:
            built = code_bundle.build(root, dest)
        except code_bundle.BundleError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        for old in folder.glob("code-*.tar.gz"):
            if old != dest:
                old.unlink(missing_ok=True)
        # The tree can change while it is read; the bundle's own id is what hosts verify.
        _built = (built.id, dest)
        return _built


def create_code_router(*, auth_provider: AuthProvider | None = None) -> APIRouter:
    """Build the router that serves the source bundle.

    :param auth_provider: Auth provider; callers must be signed in.
    :returns: The router, to mount under ``/v1``.
    """
    router = APIRouter()

    @router.get("/code/bundle")
    async def code_bundle_download(request: Request) -> FileResponse:
        """Download the source tree this server runs, for a host to apply."""
        require_user(request, auth_provider)
        code_id, path = await asyncio.to_thread(_current_bundle)
        return FileResponse(path, media_type="application/gzip", headers={"X-Code-Id": code_id})

    @router.get("/code/update-script")
    async def code_update_script(request: Request) -> PlainTextResponse:
        """Serve the standalone script that syncs a host too old to update itself."""
        require_user(request, auth_provider)
        root = code_version.find_root()
        script = root / "scripts" / "update_host.py" if root else None
        if script is None or not script.is_file():
            raise HTTPException(status_code=404, detail="update script not available")
        text = await asyncio.to_thread(script.read_text)
        return PlainTextResponse(text, media_type="text/x-python")

    return router
