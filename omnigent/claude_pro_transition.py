"""Prepare the local Hub budget for Max -> Pro without calling a model.

Run with ``python3 -m omnigent.claude_pro_transition --apply``. Without
``--apply`` this only reports the proposed configuration and plan metadata.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path.name}")
    return value


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else None


def plan_metadata(credentials: Path) -> dict[str, str | None]:
    """Return subscription names only; never persist or print authentication."""
    try:
        oauth = _read(credentials).get("claudeAiOauth", {})
    except (OSError, ValueError):
        oauth = {}
    if not isinstance(oauth, dict):
        oauth = {}
    return {
        key: value if isinstance(value := oauth.get(key), str) else None
        for key in ("subscriptionType", "rateLimitTier")
    }


def prepare(
    config: dict[str, Any],
    metadata: dict[str, Any],
    meter: dict[str, Any],
    previous: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep Max simulation until Pro has a new, complete meter reading."""
    updated = json.loads(json.dumps(config))
    free = updated.setdefault("spend_freely_until", {})
    free.pop("claude", None)
    mocks = updated.setdefault("plan_mocks", {})
    status: dict[str, Any] = {
        "checked_at": now.isoformat(),
        "plan": metadata,
        "max_spending_exception": False,
        "daily_budget_pct": (100 - config.get("buffer_pct", 15)) / 7,
        "weekly_buffer_pct": config.get("buffer_pct", 15),
        "five_hour_guard_pct": config.get("five_hour_guard_pct", 85),
        "scope": "Automatic provider selection for new lanes; existing sessions are not gated.",
    }
    if metadata.get("subscriptionType") != "pro":
        status["phase"] = (
            "waiting_for_pro" if metadata.get("subscriptionType") == "max" else "plan_unknown"
        )
        return updated, status

    observed = _time(previous.get("pro_observed_at")) or now
    status["pro_observed_at"] = observed.isoformat()
    as_of = _time(meter.get("as_of"))
    windows = {row.get("kind"): row for row in meter.get("windows", []) if isinstance(row, dict)}
    complete = all(
        isinstance((pct := windows.get(kind, {}).get("percent")), int | float)
        and not isinstance(pct, bool)
        and 0 <= pct <= 100
        and (reset := _time(windows[kind].get("resets_at"))) is not None
        and reset > now
        for kind in ("session", "weekly")
    )
    fresh = as_of is not None and observed <= as_of <= now and (now - as_of).total_seconds() <= 300
    # Stale Max percentages must not be treated as a fresh Pro allowance.
    if not fresh or not complete:
        status["phase"] = "pro_waiting_for_fresh_meter"
        return updated, status
    mocks.pop("claude", None)
    status.update(
        phase="pro_active",
        meter_as_of=as_of.isoformat(),
        weekly_percent=windows["weekly"]["percent"],
        five_hour_percent=windows["session"]["percent"],
        weekly_resets_at=windows["weekly"]["resets_at"],
    )
    return updated, status


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        json.dump(value, handle, indent=2)
        handle.write("\n")
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".omnigent")
    parser.add_argument(
        "--credentials", type=Path, default=Path.home() / ".claude/.credentials.json"
    )
    args = parser.parse_args()
    folder = args.state_dir / "hub"
    config_path = folder / "budget.json"
    status_path = folder / "claude-pro-transition.json"
    # Serialize this controller; detect another writer before replacing budget.json.
    with (folder / "claude-pro-transition.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = config_path.read_text()
        config = json.loads(original)
        try:
            previous = _read(status_path)
        except (OSError, ValueError):
            previous = {}
        try:
            meter = _read(args.state_dir / "plan-limits-claude.json")
        except (OSError, ValueError):
            meter = {}
        now = datetime.now(UTC)
        updated, status = prepare(config, plan_metadata(args.credentials), meter, previous, now)
        changed = config != updated
        status.update(config_changed=changed, applied=args.apply)
        if args.apply:
            if changed:
                if config_path.read_text() != original:
                    raise RuntimeError("Budget changed during preparation; retry next time")
                backup = folder / f"budget-before-pro-{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
                backup.write_text(original)
                _atomic_json(config_path, updated)
                status["backup"] = str(backup)
            _atomic_json(status_path, status)
        print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
