#!/bin/bash
# mktree.sh <task> <arm>  -> $WORK/runs/<task>-<arm>/tree : start commit, archived, fresh history (gold unreachable)
set -eu
# Portable: ARENA = this harness, REPO = checkout under test, WORK = scratch for run trees.
ARENA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${ARENA_REPO:-$(cd "$ARENA/../../.." && pwd)}"
WORK="${ARENA_WORK:-${TMPDIR:-/tmp}/nexus-arena}"
mkdir -p "$WORK"
declare -A GOLD=([T1]=722d842da [T2]=ad763b2db [T3]=02628e770 [T4]=46a0c437a)
T=$1; A=$2; D=$WORK/runs/$T-$A; rm -rf $D; mkdir -p $D/tree
git -C "$REPO" archive ${GOLD[$T]}^ | tar -x -C $D/tree
ln -s $REPO/.venv $D/tree/.venv
cd $D/tree && git init -q && git add -A && git -c user.email=b@b -c user.name=bench -c commit.gpgsign=false commit -qm "baseline" && \
  { git cat-file -e ${GOLD[$T]} 2>/dev/null && echo "LEAK $T-$A" && exit 1 || true; }
cp $ARENA/tasks/$T-*.md $D/task.md
# The symlink above hands every run tree the REAL venv. A worker that installs
# anything in its tree repoints it at that snapshot, and the live server then
# imports old code. Catch it at the start of each run, while the cause is still
# the previous run rather than a mystery a week later.
"$ARENA/bin/venv_guard.sh" --repair
echo "$D ready"
