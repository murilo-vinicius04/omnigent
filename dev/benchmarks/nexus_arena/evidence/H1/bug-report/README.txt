What I captured around the host dying on 2026-09-25. Times are local (-03).
Paths, ids and the machine name are anonymized; nothing else was edited.

host-20260924-202305.log            the host daemon's own log for the run that died
                                    (started 09-24 20:23, restarted 09-25 22:02)
journal-omnigent-host-2026-09-25.txt  systemd journal of the omnigent-host unit, 09-25 until 22:05
pidwatch.sh / pidwatch.log          a sampler I run every minute: the host unit's cgroup task
                                    charge (pids.current) next to its real thread count; the
                                    log covers the evening of 09-23 to 22:05 on 09-25
host-check-2026-09-25-1510.txt      state of the host at 15:10, when nothing new would start
restart-2026-09-25-2202.txt         the restart at 22:02 and the unit state after it
after-restart-2026-09-25-2205.txt   process listings a few minutes after the restart
