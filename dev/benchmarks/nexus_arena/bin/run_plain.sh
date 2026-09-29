#!/bin/bash
# run_plain.sh <task> <arm:fable|opus|opus55|sonnet55>   (runs in foreground; call with & or from a background task)
set -u
# Portable: ARENA = this harness, REPO = checkout under test, WORK = scratch for run trees.
ARENA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${ARENA_REPO:-$(cd "$ARENA/../../.." && pwd)}"
WORK="${ARENA_WORK:-${TMPDIR:-/tmp}/nexus-arena}"
mkdir -p "$WORK"
# An arm may carry a suffix after '-' (opus55-r2 runs opus55 again).
T=$1; A=$2; declare -A M=([fable]=claude-fable-5-1 [opus]=claude-opus-5 [opus55]=claude-opus-5-5
  [sonnet55]=claude-sonnet-5-5)
# ARENA_PLAIN_EFFORT picks the effort (default high, what every earlier plain arm ran).
EFFORT=${ARENA_PLAIN_EFFORT:-high}
# Never silently redo a finished run: its tree and grade are the evidence.
RUNDIR="$(dirname "$WORK/runs/$T-$A/x")"
if [ -e "$RUNDIR/exit.txt" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "refusing: $T-$A already completed (FORCE=1 to redo)" >&2; exit 3
fi
"$ARENA/bin/mktree.sh" $T $A || exit 1
D=$WORK/runs/$T-$A; cd $D/tree
date -Iseconds > $D/start.txt
# ARENA_SANDBOX names a wrapper (<wrapper> <tree> <cmd...>) that confines the arm to its tree.
timeout 5400 ${ARENA_SANDBOX:+$ARENA_SANDBOX $D/tree} claude -p --model ${M[${A%%-*}]} --effort $EFFORT --permission-mode bypassPermissions \
  --output-format stream-json --verbose < $D/task.md > $D/run.jsonl 2> $D/err.txt
echo $? > $D/exit.txt; date -Iseconds > $D/end.txt
