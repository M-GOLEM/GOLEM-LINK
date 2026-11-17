# GOLEM LINK 1.5.0

Automatic host relief and fair scheduling.

## What changed

### The host protects itself

Previously the scheduler moved *queued* work away from a busy node, but a task
that had already started stayed on the frozen machine until it finished. Now,
when this node stays above its host-light CPU/RAM limits for `preempt_after_s`
(default 15 s) while running its own task:

- the lowest-priority running task is stopped (SIGTERM, forced after 5 s);
- it is requeued and the scheduler places it on a healthy paired peer;
- the task records a `preempted` event with the reason;
- each task moves at most `preempt_max_per_task` times (default 2);
- tasks pinned with `target_node` and tasks started by a peer are left alone;
- operating-system processes and other applications are never touched.

If no healthy peer can take the work, nothing is preempted.

### Queue priority ages

The queue is ordered by effective priority = priority + aging, where aging is
`rank_aging_per_min` points per minute of waiting, capped at `rank_aging_max`.
Defaults: 2/minute, cap 25. Arrival time breaks ties, so equal work still runs
FIFO. `rank_aging_per_min = 0` restores strict priority ordering.

### The agent reclaims its own memory

Above `relieve_ram_percent` (default 80%) the agent runs `gc.collect()` and asks
the platform allocator to return free pages (`EmptyWorkingSet` on Windows,
`malloc_trim(0)` on Linux). It runs at most once per `relieve_cooldown_s`
(default 120 s), publishes a `host_guard` event, and reports runs, last reason,
last method and total reclaimed bytes in Settings → Diagnostics.

Only the GOLEM LINK process is affected.

### Workers cannot freeze the desktop

- Windows workers start with `BELOW_NORMAL_PRIORITY_CLASS`; Linux workers run
  under `nice -n 10` when the `nice` utility exists.
- The resource monitor doubles its sample interval while CPU stays at the
  warning threshold and returns to the base cadence below warning − 10 points,
  capped by `max_sample_interval_s` (default 6 s).
- Both behaviours are switchable.

### Dashboard

Settings → Scheduling adds the move delay, per-task cap, memory relief
threshold/cooldown, worker priority and aging rate/cap. Settings → Thresholds
adds adaptive sampling and its ceiling. Diagnostics shows memory-relief totals,
automatic-move state and the current monitor interval.

### Upgrade notes

Defaults are applied automatically; existing configs need no edit. The SQLite
migration adds `tasks.preempt_count` in place. No protocol change is required:
peers on 1.5.0 interoperate with 1.4.x for tasks, storage and updates.

## Verification

- 83 automated tests pass, including 14 new tests for ranking, preemption,
  memory relief, worker priority and adaptive sampling.
- The two-node loopback cluster test still passes end to end.

## Limits

- Preemption only moves tasks that this node submitted. Work accepted from a
  peer is not killed mid-flight; the node stops accepting new remote work while
  it is critical.
- A preempted task restarts from the beginning on the peer; progress inside a
  plugin is not checkpointed.
- Memory relief reclaims the agent's own pages only. It is not a system-wide
  "RAM cleaner" and deliberately leaves other applications alone.
