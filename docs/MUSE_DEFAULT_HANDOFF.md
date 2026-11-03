# Muse default handoff

User requested Muse Spark 1.2 as the default instead of Spark 1.3 to reduce consumption.

In `omnigent/server/hub_lanes.py`, changed `MUSE_DEFAULT_MODEL` to `muse-spark-1.2` and changed `muse_model` so an unversioned Spark request selects 1.2 even when 1.3 is in the catalog. Explicit version pins still work; if a reachable catalog lacks 1.2, selection returns pending instead of silently choosing another model. Updated relevant expectations in `tests/server/test_hub_lanes.py`.

Validation: `.venv/bin/python -m pytest tests/server/test_hub_lanes.py -q` passed 71 tests. Changes are in the existing dirty worktree `/home/nexus/wt/friendly-layer`; preserve unrelated changes. The other lane, “Migração de modelos para GPT”, said it did not own these files and agreed this lane could edit them.

Remaining: The running server was not restarted, so the source change may not yet be active. Check the server deployment/reload procedure and coordinate with active lanes before restarting. Verify a newly created unversioned Muse lane uses Spark 1.2 after activation. Existing lanes with explicit model settings were not migrated. Review other Muse launch paths if the user expects this default beyond Hub-created named lanes.
