"""Hermes ``pre_verify`` shell hook: the supervisor's check before a worker finishes.

Hermes runs it once per turn when a worker that edited files is about to end
its turn, with a JSON payload on stdin whose ``extra`` carries
``final_response``, ``changed_paths`` and ``attempt``. It prints
``{"decision": "block", "reason": "..."}`` to keep the worker going, or ``{}``
to let it finish; any failure lets it finish.

Environment variables (set by the wrapper shell script, as for the policy hook):
    _OMNIGENT_SERVER_URL  : Base URL of the Omnigent server.
    _OMNIGENT_SESSION_ID  : The worker's Omnigent session id.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys


async def _check(server_url: str, session_id: str, final_answer: str) -> dict[str, str]:
    import httpx

    from omnigent.native_policy_hook import policy_hook_request_headers
    from omnigent.supervisor_stop import check_worker_stop

    async with httpx.AsyncClient(
        base_url=server_url, headers=policy_hook_request_headers(), timeout=15.0
    ) as client:
        return await check_worker_stop(client, session_id, final_answer)


def main() -> None:
    server_url = os.environ.get("_OMNIGENT_SERVER_URL", "")
    session_id = os.environ.get("_OMNIGENT_SESSION_ID", "")
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        payload = {}
    extra = payload.get("extra") if isinstance(payload, dict) else None
    final_answer = extra.get("final_response") if isinstance(extra, dict) else None
    result: dict[str, str] = {}
    if server_url and session_id:
        try:
            result = asyncio.run(_check(server_url, session_id, str(final_answer or "")))
        except Exception:  # noqa: BLE001 -- a broken check must never trap the worker
            result = {}
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
