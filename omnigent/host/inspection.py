"""Read-only Hub operations, executed on the machine that owns the files."""

from __future__ import annotations

import asyncio
import os
import pathlib
import socket
from typing import Any


async def inspect(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        action = payload.get("action")
        if action == "identity":
            import getpass

            return {
                "hostname": socket.gethostname(),
                "user": getpass.getuser(),
                "home": str(pathlib.Path.home()),
            }
        workspace = str(payload.get("workspace") or "")
        if not os.path.isabs(workspace) or not pathlib.Path(workspace).is_dir():
            raise ValueError("workspace does not exist on the selected machine")
        if action == "command":
            from omnigent.server.companion_commands import run

            result = await run(
                str(payload.get("command") or ""),
                cwd=workspace,
                timeout_s=min(30, max(1, float(payload.get("timeout_s") or 15))),
            )
            if result.exit_code and result.output.lstrip().startswith("bwrap:"):
                raise ValueError(
                    "read-only sandbox unavailable on this machine: " + result.output[:300]
                )
            from dataclasses import asdict

            return asdict(result)
        if action == "repos":
            from omnigent.server.hub_lanes import repo_root

            paths = payload.get("paths") or [workspace]
            return {
                "repos": await asyncio.to_thread(
                    lambda: {str(p): repo_root(str(p)) for p in paths[:100]}
                )
            }
        if action == "report":
            from omnigent.server.hub import anchor_dirs
            from omnigent.server.hub_chat import handed_files

            def report():
                anchors = anchor_dirs(payload.get("touched") or [], workspace)
                links = handed_files(
                    str(payload.get("reply") or "")[:40_000],
                    workspace,
                    anchors,
                    since=payload.get("since"),
                )
                return {"anchors": anchors, "links": links}

            return await asyncio.to_thread(report)
        raise ValueError("unsupported host inspection")
    except Exception as exc:  # noqa: BLE001 - transport errors must return to the caller
        return {"error": str(exc)[:500]}
