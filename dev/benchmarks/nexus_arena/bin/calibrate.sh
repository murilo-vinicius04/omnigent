#!/bin/bash
# usage: calibrate.sh <gold-sha>  -> tests that fail at parent and pass at gold
set -u
# Portable: ARENA = this harness, REPO = checkout under test, WORK = scratch for run trees.
ARENA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${ARENA_REPO:-$(cd "$ARENA/../../.." && pwd)}"
WORK="${ARENA_WORK:-${TMPDIR:-/tmp}/nexus-arena}"
mkdir -p "$WORK"
H=$1; D=$WORK/cal-$H
cd "$REPO"
TESTS=$(git show --name-only --format= $H | grep '^tests/.*test_.*\.py$' | grep -v "${EXCLUDE:-^$}" | tr '\n' ' ')
rm -rf $D; git worktree add --detach $D $H^ -q && ln -s $REPO/.venv $D/.venv
for t in $TESTS; do mkdir -p $D/$(dirname $t); git show $H:$t > $D/$t; done
cd $D
timeout 900 .venv/bin/python -m pytest $TESTS -q -p no:randomly --no-header -rA --tb=no --continue-on-collection-errors 2>&1 | grep -E "^(PASSED|FAILED|ERROR)" | sort > $WORK/$H.parent.txt
git checkout -q $H -- . 2>/dev/null; git checkout -q --detach $H 2>/dev/null
SECONDS=0
timeout 900 .venv/bin/python -m pytest $TESTS -q -p no:randomly --no-header -rA --tb=no --continue-on-collection-errors 2>&1 | grep -E "^(PASSED|FAILED|ERROR)" | sort > $WORK/$H.gold.txt
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
