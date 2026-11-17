GOLEM LINK 1.3.0 — Autopilot

- Updates run themselves. A newer release is checked on schedule, downloaded
  and SHA-256-verified without a click. Windows then installs the verified
  installer detached and restarts the service. Linux stages the artifact and
  shows the single sudo command, because the service never elevates.
- New update switches: auto_stage and auto_install, both on by default.
  Existing configs migrate once; turning a switch off is persisted.
- "Let peers maintain this node" on the Updates page grants remote
  check/apply/restart to every paired device in one confirmation.
- Tasks are unchanged and still hands-free: Auto placement picks the
  least-loaded healthy machine immediately, and peers accept tasks by default.
- Rollback is unchanged: Windows keeps the previous program folder, Linux
  keeps /opt/golem-link.previous and the previous unit.
