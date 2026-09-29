#!/bin/bash
# Samples the omnigent host cgroup's task charge next to its real thread count.
# A gap between them is the leak. Correlate timestamps with ~/.omnigent/logs/host/.
D=/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/omnigent-host.service
OUT=$HOME/.omnigent/logs/pidwatch.log
while true; do
  if [ -r "$D/pids.current" ]; then
    printf '%s charged=%s real_threads=%s procs=%s runners=%s\n' \
      "$(date +%F_%T)" "$(cat $D/pids.current)" "$(wc -l < $D/cgroup.threads)" \
      "$(wc -l < $D/cgroup.procs)" \
      "$(grep -c . <(ls ~/.omnigent/logs/runner/ 2>/dev/null))" >> "$OUT"
  fi
  sleep 60
done
