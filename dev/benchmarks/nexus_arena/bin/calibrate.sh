#!/bin/bash
# usage: calibrate.sh <gold-sha> [<start-sha> <task>]  -> tests that fail at start and pass at gold
# One-commit tasks (T1-T4): start = gold^, tests = the gold commit's own test files.
# A task built from several commits (T5) passes its start and its name: the tests
# are then graded/<task>'s, copied onto both trees.
set -u
# Portable: ARENA = this harness, REPO = checkout under test, WORK = scratch for run trees.
ARENA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${ARENA_REPO:-$(cd "$ARENA/../../.." && pwd)}"
WORK="${ARENA_WORK:-${TMPDIR:-/tmp}/nexus-arena}"
mkdir -p "$WORK"
H=$1; S=${2:-$H^}; TASK=${3:-}; G=$ARENA/graded/$TASK; D=$WORK/cal-$H
# With a task, load the import guard too: a module missing from the start tree must
# not resolve to the editable install (today's code, i.e. the solution).
GUARD=; [ -n "$TASK" ] && GUARD="-p benchguard" && export PYTHONPATH="$ARENA/bin"
cd "$REPO"
if [ -n "$TASK" ]; then
  TESTS=$(cd "$G" && find tests -name 'test_*.py' | sort | tr '\n' ' ')
else
  TESTS=$(git show --name-only --format= $H | grep '^tests/.*test_.*\.py$' | grep -v "${EXCLUDE:-^$}" | tr '\n' ' ')
fi
put_tests() {
  for t in $TESTS; do
    mkdir -p $D/$(dirname $t)
    if [ -n "$TASK" ]; then cp "$G/$t" $D/$t; else git -C "$REPO" show $H:$t > $D/$t; fi
  done
}
# With a task, every test runs in its own process, as grade.py grades it: module-level
# state is named by the solution, and a whole-file run would share it across tests.
per_test() {
  for i in $(.venv/bin/python -m pytest $GUARD $TESTS --collect-only -q -p no:randomly --continue-on-collection-errors 2>/dev/null | grep "::"); do
    if timeout 300 .venv/bin/python -m pytest $GUARD "$i" -q -p no:randomly -p no:cacheprovider >/dev/null 2>&1
    then echo "PASSED $i"; else echo "FAILED $i"; fi
  done | sort
}
rm -rf $D; git worktree add --detach $D $S -q && ln -s $REPO/.venv $D/.venv
put_tests
cd $D
if [ -n "$TASK" ]; then per_test > $WORK/$H.parent.txt; else
timeout 900 .venv/bin/python -m pytest $GUARD $TESTS -q -p no:randomly --no-header -rA --tb=no --continue-on-collection-errors 2>&1 | grep -E "^(PASSED|FAILED|ERROR)" | sort > $WORK/$H.parent.txt
fi
git checkout -q $H -- . 2>/dev/null; git checkout -q --detach $H 2>/dev/null
[ -n "$TASK" ] && put_tests
SECONDS=0
if [ -n "$TASK" ]; then per_test > $WORK/$H.gold.txt; else
timeout 900 .venv/bin/python -m pytest $GUARD $TESTS -q -p no:randomly --no-header -rA --tb=no --continue-on-collection-errors 2>&1 | grep -E "^(PASSED|FAILED|ERROR)" | sort > $WORK/$H.gold.txt
fi
T=$SECONDS
python3 - "$WORK/$H.parent.txt" "$WORK/$H.gold.txt" "$H" "$T" <<'PY'
import sys
p={l.split()[1]:l.split()[0] for l in open(sys.argv[1])}
g={l.split()[1]:l.split()[0] for l in open(sys.argv[2])}
f2p=[t for t,s in g.items() if s=="PASSED" and p.get(t,"ERROR")!="PASSED"]
p2p=[t for t,s in g.items() if s=="PASSED" and p.get(t)=="PASSED"]
gf=[t for t,s in g.items() if s!="PASSED"]
print(f"{sys.argv[3]}: tests={len(g)} fail_to_pass={len(f2p)} pass_to_pass={len(p2p)} gold_failures={len(gf)} gold_runtime={sys.argv[4]}s")
for t in gf[:5]: print("   GOLD FAIL", t)
open(sys.argv[1].replace(".parent.txt",".f2p.txt"),"w").write("\n".join(f2p)+"\n")
PY
cd "$REPO"; git worktree remove --force $D
