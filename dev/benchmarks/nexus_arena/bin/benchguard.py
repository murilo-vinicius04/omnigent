"""pytest -p benchguard: drop the shared venv's editable-install finder, so `omnigent.*` resolves ONLY
inside the tree under test. Without this, a module missing from the tree silently loads from
the repo the venv was installed from (today's code, i.e. the gold solution)."""
import sys
sys.meta_path[:] = [f for f in sys.meta_path if "_EditableFinder" not in type(f).__name__
                    and "editable" not in getattr(f, "__module__", "").lower()
                    and "editable" not in getattr(type(f), "__module__", "").lower()]
for name in [m for m in sys.modules if m == "omnigent" or m.startswith("omnigent.")]:
    del sys.modules[name]
