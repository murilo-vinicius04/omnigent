#!/bin/bash
# run_plain.sh <task> <arm:fable|opus>   (runs in foreground; call with & or from a background task)
set -u
# Portable: ARENA = this harness, REPO = checkout under test, WORK = scratch for run trees.
ARENA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${ARENA_REPO:-$(cd "$ARENA/../../.." && pwd)}"
WORK="${ARENA_WORK:-${TMPDIR:-/tmp}/nexus-arena}"
mkdir -p "$WORK"
T=$1; A=$2; declare -A M=([fable]=claude-fable-5-1 [opus]=claude-opus-5)
# Never silently redo a finished run: its tree and grade are the evidence.
RUNDIR="$(dirname "$WORK/runs/$T-$A/x")"
if [ -e "$RUNDIR/exit.txt" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "refusing: $T-$A already completed (FORCE=1 to redo)" >&2; exit 3
fi
$WORK/mktree.sh $T $A || exit 1
D=$WORK/runs/$T-$A; cd $D/tree
date -Iseconds > $D/start.txt
timeout 5400 claude -p --model ${M[$A]} --effort high --permission-mode bypassPermissions \
  --output-format stream-json --verbose < $D/task.md > $D/run.jsonl 2> $D/err.txt
echo $? > $D/exit.txt; date -Iseconds > $D/end.txt
