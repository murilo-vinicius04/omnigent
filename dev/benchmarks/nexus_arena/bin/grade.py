"""Grade one arm of a bench2 task, as delivered.

usage: grade2.py <task: T1..T4> <tree> [--json out.json]

Writes the task's FAIRNESS-FIXED hidden tests into <tree> at their real paths
(on a copy, never the arm's own tree), then runs every graded test in its own
fresh Python process so no solution depends on the author's private state
names. Also runs the rest of those test files once as a regression guard, and
ruff on the files the arm changed.
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
import os, pathlib
ARENA = pathlib.Path(__file__).resolve().parent.parent
REPO = pathlib.Path(os.environ.get("ARENA_REPO", ARENA.parents[2]))
WORK = pathlib.Path(os.environ.get("ARENA_WORK", os.environ.get("TMPDIR", "/tmp")) ) / "nexus-arena"
WORK.mkdir(parents=True, exist_ok=True)

B = ARENA
GOLD = {"T1": "722d842da", "T2": "ad763b2db", "T3": "02628e770", "T4": "46a0c437a"}
PER_TEST_GUARD = {"T3"}
VENV = str(REPO / ".venv")

GUARD = str(ARENA / "bin")

def run(cmd, cwd, timeout=900):
    env = dict(os.environ, PYTHONPATH=GUARD + os.pathsep + str(cwd))
    if "pytest" in cmd:  # resolve omnigent only inside the tree (see guard/benchguard.py)
        cmd = cmd[:cmd.index("pytest") + 1] + ["-p", "benchguard"] + cmd[cmd.index("pytest") + 1:]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, p.stdout + p.stderr

def main():
    task, src = sys.argv[1], Path(sys.argv[2]).resolve()
    out_json = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    graded = [l.strip() for l in (ARENA / "calibration" / f"{GOLD[task]}.f2p.txt").read_text().splitlines() if l.strip()]
    work = Path(tempfile.mkdtemp(prefix=f"grade-{task}-", dir=WORK))
    tree = work / "tree"
    shutil.copytree(src, tree, symlinks=True, ignore=shutil.ignore_patterns(".venv", "__pycache__", "node_modules"))
    (tree / ".venv").symlink_to(VENV)
    changed = run(["git", "diff", "--name-only", "HEAD"], tree)[1].split() + \
              run(["git", "ls-files", "--others", "--exclude-standard"], tree)[1].split()
    changed_py = [f for f in changed if f.endswith(".py") and (tree / f).exists()]
    py = str(tree / ".venv/bin/python")
    # Lint the arm's own files BEFORE the hidden tests overwrite any of them.
    lint = run([py, "-m", "ruff", "check", *changed_py], tree, 300)[0] == 0 if changed_py else True
    fmt = run([py, "-m", "ruff", "format", "--check", *changed_py], tree, 300)[0] == 0 if changed_py else True
    for f in (ARENA / "graded" / task).rglob("*.py"):
        rel = f.relative_to(ARENA / "graded" / task)
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, tree / rel)
    py = str(tree / ".venv/bin/python")
    results = {}
    for t in graded:
        rc, out = run([py, "-m", "pytest", t, "-q", "-p", "no:randomly", "--no-header", "-p", "no:cacheprovider"], tree, 300)
        results[t] = "pass" if rc == 0 else "fail"
    files = sorted({t.split("::")[0] for t in graded})
    if task in PER_TEST_GUARD:
        # State lives at module level under names the arm chooses: isolate every test.
        _, out = run([py, "-m", "pytest", *files, "--collect-only", "-q", "-p", "no:randomly"], tree)
        ids = [l.strip() for l in out.splitlines() if "::" in l]
        failed = {i for i in ids if i not in set(graded) and
                  run([py, "-m", "pytest", i, "-q", "-p", "no:randomly", "-p", "no:cacheprovider"], tree, 300)[0] != 0}
    else:
        rc, out = run([py, "-m", "pytest", *files, "-q", "-p", "no:randomly", "--no-header", "-rf",
                       "--continue-on-collection-errors", "-p", "no:cacheprovider"], tree)
        failed = {l.split()[1] for l in out.splitlines() if l.startswith(("FAILED ", "ERROR "))}
    # A regression must reproduce when rerun alone (the suite has load-sensitive timing tests).
    start_files = {f for f in files if subprocess.run(["git", "cat-file", "-e", f"{GOLD[task]}^:{f}"],
                   cwd=str(REPO), capture_output=True).returncode == 0}
    regressions = sorted(f for f in failed if f not in set(graded) and f.split("::")[0] in start_files and
                         run([py, "-m", "pytest", f, "-q", "-p", "no:randomly", "-p", "no:cacheprovider"], tree, 300)[0] != 0)
    score = sum(v == "pass" for v in results.values())
    report = {"task": task, "tree": str(src), "score": score, "total": len(graded), "tests": results,
              "regressions": regressions, "ruff_clean": lint, "format_clean": fmt, "changed_files": changed}
    shutil.rmtree(work, ignore_errors=True)
    print(f"{task} {src.name}: {score}/{len(graded)}  regressions={len(regressions)}  ruff={'clean' if lint else 'ERR'}  format={'clean' if fmt else 'ERR'}")
    for t, v in results.items():
        if v != "pass": print("   fail", t.split("::")[1])
    for r in regressions[:5]: print("   REGRESSION", r)
    if out_json: Path(out_json).write_text(json.dumps(report, indent=1))

if __name__ == "__main__":
    main()
