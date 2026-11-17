# Test report — GOLEM LINK 1.8.0

Date: 2026-11-17
Build under test: 1.7.0 (working tree of `F:\GOLEM-LINK`, not a git checkout)
Host: Windows 10 22H2 (19045) x64, Python 3.11.0 / bundled 3.13 target, Node.js 24.22.1
Test type: automated suite + frozen-bundle payload check + live 1.7.0 → 1.8.0 install of the Windows node, broadcast and inbox endpoints exercised live

## 1.8.0 — broadcast relay and inbox cleanup

| Behaviour | Implementation | Verified by |
| --- | --- | --- |
| One send reaches every usable device | `POST /api/clipboard/send-all` iterates online peers with control granted and calls the existing single-send path | `test_send_all_reaches_every_online_peer`; live call below |
| One failed device does not block the rest | Each failure becomes a row with `ok=false` and its error; the loop continues | `test_send_all_keeps_going_when_one_peer_fails` |
| No usable device is rejected before any request | Empty/oversized text, relay disabled and an empty target list raise a 400 with a readable reason | `test_send_all_requires_a_usable_peer` |
| The inbox can remove one item or all | `DELETE /api/clipboard/{id}` and `DELETE /api/clipboard` on the bridge and SQLite, admin session + CSRF, audited | `test_inbox_delete_and_clear`; live clear below |
| Dashboard exposes both features | Send to all (n), per-item remove, Clear with confirmation; `npm run build` / `tsc` clean | Dashboard bundle rebuilt and served by the live node |

Automated suite: **103 passed** (99 from 1.7.0 plus 4 in `tests/test_clipboard.py`).

Artifacts (1.8.0 build):

| Artifact | Result |
| --- | --- |
| `GOLEM-LINK-Setup-Windows.exe` | 37,561,708 B, SHA-256 `5f802e3226f00d375f871c268faa77fc1574fd56ae093d5236dbc43c062ea099` |
| `GOLEM-LINK-Debian13.run` | 41,481,812 B, SHA-256 `2f57b9424f08713f87db5005bfa3848b05f6f5fde5ab6b254149a3d9b33eca10` |
| `GOLEM-LINK-Portable-Windows.zip` | 22,764,964 B |
| `dist/updates/manifest.json` | 1.8.0, hashes above |
| Live Windows node | `golem-link.exe --version` reports 1.8.0; API `/api/update` returns `up_to_date`, current 1.8.0; service `golem-link` RUNNING |
| Live broadcast evidence | `POST /api/clipboard/send-all` with the 1.5.0 laptop online returned `ok:true`, `sent:0` and one isolated row: `GOLEM runs an older GOLEM LINK without text relay` — the endpoint answered 200 instead of failing |
| Live clear evidence | `DELETE /api/clipboard` returned `{"cleared":0}`; inbox empty, status intact (enabled, auto-copy, 262,144 max, keep 50) |

**Incident during deployment (real defect, fixed manually):** the service-driven
1.8.0 apply stopped the service, moved the 1.7.0 folder to
`C:\Program Files\GOLEM LINK.previous`, then failed extracting the payload
(`PermissionError: _internal\_asyncio.pyd`). The installer did not re-register
the service, so the node stayed down. Rerunning the staged installer elevated
(`/Update /Silent /NoPair /NoBrowser`) exited 0, registered the service and
started 1.8.0. The old 1.7.0 folder was intact and no data was lost. The
installer's extraction/rollback behaviour is the target of the next release.

## 1.7.0 — paste through the agent instead of the browser

| Behaviour | Implementation | Verified by |
| --- | --- | --- |
| The compose box can read this machine's clipboard over plain HTTP | `POST /api/clipboard/read` → `ClipboardBridge.read_local` → `clipboard_session.read_text`; the page tries `navigator.clipboard` first and falls back to the agent when it is unavailable | `test_read_local_returns_this_machine_clipboard`, `test_read_script_targets_gettext_and_utf8`; live browser test below |
| The Windows service reads the logged-in desktop | Direct `GetClipboardData(CF_UNICODETEXT)` inside the session; from session 0 the same console-session token helper runs a hidden STA PowerShell script that writes the text to a UTF-8 file | Live: `POST /api/clipboard/read` returned the exact 32-character Arabic text placed by the interactive session (em dash intact) |
| Linux reads through the active session | `wl-paste --no-newline` / `xclip -o` / `xsel --output` with the same session detection and runuser prefix as copy | `_linux_session` parsing test (unchanged), shared `_linux_prefix`; not runnable on the Windows host |
| Failures are explained | No desktop session, no text in the clipboard (empty string), oversized clipboard → 400 with the reason | `test_read_local_reports_empty_long_and_disabled`, `test_read_local_explains_an_unreachable_desktop` |
| Reads are audited and guarded | Admin session + CSRF + relay switch; audit row `clipboard-read local N chars` | Live audit after each call |

Automated suite: **99 passed** (95 from 1.6.1 plus 4 read tests in
`tests/test_clipboard.py`, 16 there in total).

Artifacts (final 1.7.0 build):

| Artifact | Result |
| --- | --- |
| Dashboard bundle | `index-DxBm4m_k.js` (824,781 B) / `index-CbSXwsZm.css` (41,697 B), `npx tsc --noEmit` clean; served by the live node |
| `GOLEM-LINK-Setup-Windows.exe` | 37,562,158 B, SHA-256 `42c53c5b26476823b8d3967b57de0eb7a00dde936232deee217df996c7ff5ca9`; `/ExtractOnly` payload reports `GOLEM LINK 1.7.0` with the new assets |
| `GOLEM-LINK-Debian13.run` | 41,480,981 B, SHA-256 `5d2e33de7404171a81a79327f9765776e2dd23ff2c4174aaab0869e9a23aeb0f`; `--check` reports installer 1.7.0 |
| `GOLEM-LINK-Portable-Windows.zip` | 22,765,633 B |
| `dist/updates/manifest.json` | 1.7.0, hashes above |
| Live Windows node | Self-updated 1.6.1 → 1.7.0 through its own check/download/apply API; `version 1.7.0`, state `up_to_date`, revert `C:\Program Files\GOLEM LINK.previous` available |
| Live read evidence | `POST /api/clipboard/read` after `Set-Clipboard` in the interactive session: 32 characters, exact match including the Arabic text and the em dash. Copy → read round trip: `console-session`, then 30/30 characters exact |
| Live browser evidence | Dashboard opened at `http://192.168.1.3:8080` (`isSecureContext=false`, no `navigator.clipboard`): Text Relay → **Paste from clipboard** filled the box with the exact 36-character clipboard text and the counter showed `36 / 262,144` |
| Live older-peer handling | **Send** to `GOLEM` (1.5.0) answered 400 `GOLEM runs an older GOLEM LINK without text relay`; the item stayed in the box, nothing was lost |

**Not yet proven:** Linux clipboard reading (needs the operator to install the
1.7.0 `.run` and `xclip`/`wl-clipboard`), and a real peer→Windows receive
still cannot be injected because the only paired peer is older. Both paths
are covered by the unit suite; the service-side Windows path is proven.

## 1.6.x — text relay between paired machines

The request behind this release: copy a text on one computer and paste it on
the other.

| Behaviour | Implementation | Verified by |
| --- | --- | --- |
| A copied text reaches the other machine | Text Relay page → `POST /api/clipboard/send` → peer `POST /peer/clipboard` (HMAC, `allow_control` on both ends) → item stored and copied into the receiver's desktop clipboard | `test_receive_stores_and_auto_copies`, `test_send_posts_to_the_peer`, `test_send_to_older_peer_explains_it` (12 tests in `tests/test_clipboard.py`) |
| The service can write the desktop clipboard | Session 0 detects itself with `ProcessIdToSessionId`; otherwise it opens the active console session token (`WTSGetActiveConsoleSessionId` from **kernel32**, `WTSQueryUserToken`, `DuplicateTokenEx`, `CreateEnvironmentBlock`) and runs a hidden STA PowerShell helper via `CreateProcessAsUserW` | Live: `POST /api/clipboard/copy` → `{"copied": true, "method": "console-session"}`; `Get-Clipboard` in the interactive session matched the Arabic text byte-for-byte |
| Nothing is lost when auto-copy is impossible | Item is stored first; failure records `copy_method=NULL` plus the exact error and keeps a per-item Copy button | `test_copy_failure_is_recorded`, `test_copy_can_be_retried` |
| Sending is guarded | Empty/oversized text rejected, relay switch enforced, peer must be online and grant control; a pre-1.6 peer maps to "runs an older GOLEM LINK without text relay" | `test_send_requires_an_online_peer`, `test_send_requires_the_peer_control_grant`, `test_receive_is_refused_when_disabled` |
| Old items do not accumulate | `prune_clipboard_items(keep=50)` after every receive | `test_receive_prunes_to_keep_items` |
| 1.6.1 fix | `WTSGetActiveConsoleSessionId` resolved from `kernel32` (1.6.0 asked `wtsapi32` and answered 500); a failed local copy now returns the error instead of raising | Live before/after: 1.6.0 → Internal Server Error in the log; 1.6.1 → `console-session` |

Automated suite: **95 passed** (83 from 1.5.0 plus 12 in
`tests/test_clipboard.py`).

Artifacts (final 1.6.1 build):

| Artifact | Result |
| --- | --- |
| Dashboard bundle | `index--a_PGEse.js` (824.01 kB) / `index-CbSXwsZm.css` (41.70 kB), `npx tsc --noEmit` clean; served by the live node |
| `GOLEM-LINK-Setup-Windows.exe` | 37,558,580 B, SHA-256 `52726b90df03a62dc7a04a06f07f949698691fbb8616096f148c494377cda142`; `/ExtractOnly` payload reports `GOLEM LINK 1.6.1` |
| `GOLEM-LINK-Debian13.run` | 41,480,480 B, SHA-256 `8ab8a12c07d911e2d1181b0ab8e271c8fcab05bfa0cfe3d91c531ccd94695a49`; `--check` reports installer 1.6.1 |
| `GOLEM-LINK-Portable-Windows.zip` | 22,762,882 B |
| `dist/updates/manifest.json` | 1.6.1, Windows and Linux hashes above |
| Live Windows node | Self-updated 1.5.0 → 1.6.0 → 1.6.1 through its own check/download/apply API; now reports `version 1.6.1`, state `updated`, revert `C:\Program Files\GOLEM LINK.previous` available |
| Live relay evidence | `/api/clipboard/copy` returned `console-session`; `Get-Clipboard` content matched the 51-character Arabic test string exactly (sha256 prefix `8b753f014d4c3172`); audit row `clipboard-copy local 51 chars`; `/api/diagnostics.clipboard` reports enabled, auto-copy, 262,144 max, keep 50 |
| Live older-peer handling | Sending to `GOLEM` (now 1.5.0) returned `GOLEM runs an older GOLEM LINK without text relay` instead of a raw 404 (peer online, 2.1 ms) |

**Not yet proven:** the receive half on the laptop — it must first run 1.6.1,
and Linux auto-copy depends on the service user reaching the GUI session
(else the inbox Copy button is the path). A peer→Windows receive was not
injected live because the only paired peer is older; the receive route is
covered by the unit suite.

## 1.5.0 — automatic processing, memory relief, preemption, ranking

The request behind this release: the product was not auto-processing, not
relieving RAM by itself, froze the screen/CPU under load, and did not move or
re-rank tasks on its own.

| Behaviour | Implementation | Verified by |
| --- | --- | --- |
| Queued work climbs the ladder by itself | `_rank_queued()` adds `min(rank_aging_max, waited_min × rank_aging_per_min)` to every queued task's priority before sorting; ties break on `created_at`, then id | `test_aging_lifts_an_old_low_priority_task`, `test_aging_is_capped_and_fifo_inside_the_cap`, `test_aging_can_be_disabled` |
| A frozen host hands its own running task back | `_preempt_if_frozen()` watches sustained light-guard/overload pressure; after `preempt_after_s` it asks `_preempt_one()` to stop one own running task, requeue it (`status=queued`, executor cleared, `preempt_count+1`) and let placement move it to a capable peer | `test_preemption_moves_a_running_task_to_a_peer`, `test_preemption_does_nothing_without_a_peer`, `test_preemption_respects_cap_pin_and_short_runs`, `test_idle_time_resets_the_busy_timer`, `test_relief_status_reports_effective_config` |
| The agent gives RAM back instead of growing | `HostGuard` triggers at `relieve_ram_percent` (80%), `gc.collect()` plus `psapi.EmptyWorkingSet` on Windows or `malloc_trim(0)` on Linux, with a 120 s cooldown; publishes a `host_guard` event and exposes counters | `test_host_guard_releases_memory_under_pressure`, `test_host_guard_ignores_a_comfortable_host`, `test_host_guard_can_be_disabled` |
| Workers do not fight the desktop | Windows workers launch with `BELOW_NORMAL_PRIORITY_CLASS` and `CREATE_NO_WINDOW`; POSIX workers are wrapped in `nice -n 10` when available | `test_worker_gets_low_priority_flags`, `test_worker_gets_nice_wrapper`, `test_worker_priority_can_be_disabled` |
| Monitoring backs off during a spike | `adaptive_sampling` doubles the sample interval once CPU ≥ `cpu_warn` and recovers only below `warn − 10`, capped at `max_sample_interval_s` (6 s) | `test_adaptive_interval_slows_and_recovers`, `test_adaptive_interval_has_hysteresis`, `test_fixed_interval_when_adaptive_is_off` |
| The controls are visible and adjustable | Settings page: Protect this machine automatically, sustained busy, move limit, Release agent memory, RAM threshold, cooldown, low-priority workers, aging rate/cap, adaptive sampling; Diagnostics shows relief runs/bytes, busy time, monitor interval | `npx tsc --noEmit` clean; live `/api/settings` and `/api/diagnostics` checked after the self-update (below) |

Automated suite: **83 passed** (69 from 1.4.1 plus 14 in
`tests/test_auto_relief.py`).

Artifacts (final 1.5.0 build):

| Artifact | Result |
| --- | --- |
| Dashboard bundle | `index-DgAubNn8.js` / `index-jk_vsX25.css`, TypeScript clean |
| `GOLEM-LINK-Setup-Windows.exe` | 37,535,147 B, SHA-256 `f87d7f74f0e2fa17eeb08f64a9353a8b0db92a5325c95185b39a24e50a234b06` |
| `GOLEM-LINK-Debian13.run` | 41,470,451 B, SHA-256 `0e46d2eea5a39c2b90da13e1442198cde7599bf705f0e6b54a79d91520727fbc` |
| `GOLEM-LINK-Portable-Windows.zip` | 22,740,724 B |
| `dist/updates/manifest.json` | 1.5.0, Windows and Linux hashes above |
| Live Windows node | Self-updated 1.4.1 → 1.5.0 through its own apply API (sha `f87d7f74…` verified); now reports `version 1.5.0`, state `up_to_date`, revert `C:\Program Files\GOLEM LINK.previous` available |
| Live relief evidence | `/api/diagnostics` after the restart: `host_guard.runs=2`, `total_reclaimed_bytes=117,944,320` (~112 MB, method `EmptyWorkingSet`), last reason `RAM 80%`; log line `host relief (RAM 81%): reclaimed 81.7 MB via EmptyWorkingSet` |
| Live scheduler state | `scheduler_relief` shows preemption on, 15 s, cap 2, aging 2.0/min up to 25, `nice_workers=true`; `monitor_interval_s=2.0` at idle (backs off only under CPU ≥ 80%) |
| Live peer link | `gl-14c98f607e5b4542b76f` (GOLEM) online, 2.5 ms, failures 0; it still runs 1.3.1 until the operator installs the new artifact |

**Not yet proven:** preemption of a *running* task has only been exercised in
unit tests; no live task was deliberately frozen on the Windows node to force
a handoff. The laptop still runs 1.3.1 — it cannot execute 1.5.0 relief until
the operator deploys `GOLEM-LINK-Debian13.run`. Windows memory relief is being
observed live, but the ceiling effect depends on other processes; 112 MB over
two runs is real, not the theoretical maximum.

## 1.4.1 — remote operations, link health, heavy results

## 1.4.1 — remote operations, link health, heavy results

| Behaviour | Implementation | Verified by |
| --- | --- | --- |
| A peer can be inspected without a shell | `GET /peer/ops/diagnostics` and `GET /peer/ops/logs?lines=` behind `_peer(request, "control")`; read-only facts plus a 256 KB log tail capped at 1000 lines | cluster test calls both routes over the real pinned-TLS peer server (`diagnostics["node_id"] == b_id`, bounded log list) and asserts `403` after `allow_control=False`; `test_diagnostics_have_stable_shape`, `test_log_tail_is_bounded` |
| Restart is a real remote action | `POST /peer/ops/action {action: "restart", confirm: true}` schedules a clean exit; systemd `Restart=always` or the Windows service brings the node back | `test_linux_restart_is_a_scheduled_exit` (monkeypatched `_exit_soon`); the 1.4.0→1.4.1 rollout restarted the live Windows service by itself |
| Rollback uses the same trust boundary as install | Windows: detached SYSTEM task moves the folders and reverts to the newer build if the restore does not start. Linux: bounded `{action, nonce}` request to the 1.3.1 root helper, refused when the helper predates the action or the backup is missing | `test_rollback_needs_a_backup`, `test_rollback_linux_needs_the_helper`, `test_rollback_refuses_an_old_helper`, `test_rollback_writes_a_bounded_helper_request`; not executed end-to-end on Debian |
| A restored node does not immediately re-upgrade | Windows PowerShell edit (no BOM) and Linux root JSON edit set `update.enabled` / `auto_stage` / `auto_install` false in the restored config | script content assertions in `test_ops.py`; live execution not run |
| The link explains its failures | `last_error`, `reconnects`, `online_since` on `PeerRuntime`, a 10 s application ping, and the same fields in `/api/devices` and the Devices/Network UI | live `/api/devices` showed `failures=0 reconnects=0 last_error=None` on the healthy link; the cluster test's live permission push still updates `remote_accepts_tasks` |
| Inbound capability pushes carry every grant | The WebSocket `capabilities` handler now sets tasks, storage, update-serving and control through `PeerManager.apply_capabilities` | `test_capabilities_carry_every_grant` (the shared helper now used by the HTTP, outbound-WS and inbound paths) |
| Heavy updates and results do not break the socket | Task updates >512 KB use HTTP `POST /peer/tasks/update` (60 s); results >4 MB become an explicit `truncated` marker | `test_large_task_update_skips_the_websocket`, `test_oversized_result_is_marked_not_silently_cut` |
| The Remote panel is honest about an older peer | 404 → “still runs an older GOLEM LINK”; 403 → the grant to turn on; diagnostics and log errors are tracked separately and replace the loader | browser QA against the live 1.3.1 laptop: panel rendered the older-peer message, no spinner |

Automated suite: **69 passed in 44.7 s** (the 1.3.1 set plus 11 remote-ops
tests; the cluster test now exercises `/peer/ops/diagnostics` and
`/peer/ops/logs` and the 403 after revoking control).

Artifacts (final 1.4.1 build):

| Artifact | Result |
| --- | --- |
| Windows onedir bundle | 104 files, 44 MB directory; `golem-link.exe --version` → `GOLEM LINK 1.4.1`; embedded dashboard `index-CLn_5WdD.js` / `index-jk_vsX25.css` |
| `GOLEM-LINK-Setup-Windows.exe` | 37,526,513 B, SHA-256 `59fe569ff6b9e3c348c24c7306dca7e62459d4bcaa36ea1abf1591e43ce17886`; `--extract-only` produced a bundle whose `golem-link.exe --version` → `GOLEM LINK 1.4.1` and whose `index.html` references the same assets |
| `GOLEM-LINK-Debian13.run` | 41,465,534 B, SHA-256 `8fe844a969c9b72d57f031cb476ec6e2a1752fc312a06e9c08f591a870b8ed66`; `--check` → `installer 1.4.1`, 46 payload files, 34 wheels, header parses |
| `GOLEM-LINK-Portable-Windows.zip` | 22,729,254 B, SHA-256 `20a3daf403005495cc7c0b9d30c96c30a9a1f74799f6e8702506b206ffc6b7db` |
| `dist/updates/manifest.json` | 1.4.1, published 2026-11-17T01:18:53Z, SHA-256 `2c558ff2b21fc993b3b0399df690c82fce02087058e23f7790bde7227e0b5aeb`; Windows and Linux hashes above, verified on check and download |
| Live Windows node | Self-updated through its own API 1.3.1 → 1.4.0 → 1.4.1: check → download (sha `59fe569f…` verified) → apply; installed `--version` → 1.4.1, state `updated`, `C:\Program Files\GOLEM LINK.previous` present, dashboard sidebar shows `1.4.1` |
| Live browser QA | Logged in at `127.0.0.1:8080`, Devices → Remote opened; against the 1.3.1 laptop the panel showed the older-peer message with no spinner. Diagnostics success rendering is covered by the cluster test and unit shape tests; the laptop itself has not upgraded yet |

**Not yet proven:** the Linux `rollback` action has not been executed end to
end on the Debian laptop (the 1.3.1 helper there predates the action until the
node upgrades). The laptop has not yet pulled 1.4.1 from the Windows node; its
autopilot check runs on its own interval and was not triggered remotely
because installing on that machine is reserved for the operator. Windows
rollback was not run against the live install either — only its generated
script and guards are tested.

## 1.3.1 — Linux remote maintenance

| Behaviour | Implementation | Verified by |

## 1.3.1 — Linux remote maintenance

| Behaviour | Implementation | Verified by |
| --- | --- | --- |
| Linux can apply a verified update without sudo | Opt-in root helper: `golem-link-update.path` watches `<data>/updates/remote-update.request`, `golem-link-update.service` runs the fixed helper, which runs the installer with `--upgrade --no-auto-pair --no-firewall` | `test_helper_install_plan_and_request`; helper validation exercised with real files (see below) |
| The helper cannot be steered | It takes no arguments and re-validates everything: nonce/version/sha/size formats, absolute non-symlink artifact inside `staged/<version>/`, size, SHA-256; copies to root-only `/opt/golem-link-update`, re-hashes the copy, executes that copy. Its result file is written inside the root-only directory and renamed into place, so the agent cannot plant a symlink at a predictable temp name | extracted helper verifier run four ways: valid request accepted and copied; wrong hash → exit 3; artifact outside the version folder → exit 3; malformed version → exit 3 |
| A disabled trigger is reported, not hidden | `apply()` writes the request, waits up to 60 s; if the request still exists the path unit never fired → state `error` with `systemctl status golem-link-update.path`; if the helper consumed it, the restart resolves the state | `test_missing_helper_trigger_is_reported`, `test_helper_failure_is_reported` |
| Existing and new pairings follow the grant | Enabling the flag flips `allow_control` for existing rows; `_save_paired_peer` defaults it from `remote.helper_ready()` | `test_new_peer_gets_control_when_helper_enabled`, `test_helper_ready_needs_marker_and_executable` |
| Desktop behaviour is unchanged | Without the marker, `install_plan()` still returns `operator-sudo`, `auto_install_supported` stays false and the dashboard shows the one-sudo card | existing `test_local_check_download_and_apply_stage`, `test_status_reports_autostage` (unchanged, still pass) |
| The release carries the helper | `build_run.py` stages the helper and both units; the installer adds `--allow-remote-updates`, writes the marker, enables the path unit, flips existing peer rows and removes everything on uninstall | `--check` on the built .run passes the new payload assertions (46 entries, 34 wheels); wheel is `golem_link-1.3.1-py3-none-any.whl` with `VERSION = "1.3.1"` and `remote_maintenance` |

Automated suite: **58 passed in 35.8 s** (53 from 1.3.0 plus 5 for the helper
protocol).

Artifacts:

| Artifact | Result |
| --- | --- |
| Windows onedir bundle | 106 files / 30.5 MB payload, `golem-link.exe --version` → `GOLEM LINK 1.3.1`; embedded dashboard `index-KvDtqS4q.js` / `index-CT53Y8SS.css` |
| `GOLEM-LINK-Setup-Windows.exe` | 37,508,910 B, SHA-256 `80165c180defebc9478d4faef67ccaf94b45232c4dee5974f674273b451bd402`; payload SHA-256 `4fd50ff900f43fba740c0c99a9fa1fc39363765bae3ea8301ab4db74dd19aafa` |
| `GOLEM-LINK-Debian13.run` | 41,456,705 B, SHA-256 `ea7b52acdf70fe290f9c447d1134d157d24f90c3109f394063df022eb68b8ec4`; 46 payload entries, 34 wheels, helper + both units present with `@DATA_DIR@` placeholders; `--check` passes; header passes `bash -n` |
| Isolated bundle smoke test | `127.0.0.1:8098`, `current_version: 1.3.1`, `auto_stage/auto_install` as configured, `remote_install_supported: false` on Windows, dashboard serves the new asset |
| Live Windows node | Self-updated 1.3.0 → 1.3.1 through its own dashboard API: check → download (sha `80165c18…` verified) → apply; state `updated`, installed `golem-link.exe --version` → 1.3.1, `C:\Program Files\GOLEM LINK.previous` present, `install.log` confirms success, autopilot still on |

**Not yet proven:** the helper has not been executed end-to-end on a real
Debian system from this machine. The payload, units, validation logic and
installer branches are verified here; the first laptop run is the remaining
check. The `.run` was also not installed here (Windows host); `/Update` ran
only through the node's own verified self-update path.

## 1.3.0 — autopilot updates

| Behaviour | Implementation | Verified by |
| --- | --- | --- |
| A newer release is verified without a click | `UpdateManager.run()` downloads whenever `auto_stage` is on and the manifest state is `available`; the existing size + SHA-256 check still decides `ready` | `test_update_defaults_are_autopilot`, `test_status_reports_autostage`, full suite |
| Windows installs unattended, Linux does not pretend to | With `auto_install` on the loop calls `apply()` only when `platform == "windows"`; Linux stays at `ready` with the operator command | code path in `run()` + `test_linux_apply_only_stages`, `test_status_reports_autostage` (`auto_install_supported` false) |
| Existing installs pick up the new defaults once | `ConfigManager._load()` writes `auto_stage`/`auto_install` true when the stored `update` section predates the key, then never touches it again | `test_pre_130_config_gets_autopilot_once` (migration then explicit opt-out survives reload) |
| Speed of rollout does not hide a failure | A failed automatic pass is logged with a traceback and the loop continues on the normal interval; a Windows apply that reaches `installing` ends the loop so the restart is not raced | inspection of `run()`; `test_interrupted_install_is_resolved` unchanged |
| Fleet trust is one confirmation per machine | *Let peers maintain this node* PATCHes `allow_control: true` for every non-self peer and reports how many were granted | TypeScript build; permission endpoint existing tests; no silent migration of the inbound grant |
| Task distribution needs no clicks | unchanged from 1.2: `submit_local` places `target_node=None` immediately and peers accept tasks by default | cluster test + `test_light_guard_*` |

## 1.2.0 — verified updates and fleet maintenance

| Behaviour | Implementation | Verified by |
| --- | --- | --- |
| A release cannot be installed unless it is genuine | `normalize_manifest()` validates version/channel/file names and requires an sha256 per artifact; `verify_artifact()` checks size + SHA-256 on serve, on receive and again before staging | 11 update tests; live check with a hand-written manifest rejected, then accepted after fixing its shape; tampered artifact test |
| A node only trusts releases it can reach and verify | Peer manifests are read only from online peers that set `serves_updates`; the artifact is downloaded over pinned TLS with progress, hashed and renamed atomically | loopback pair: node B checked, pulled 1.2.1 from node A (`/peer/update/artifact`) and staged it as verified |
| Automatic install is safe on Windows | Verified installer is launched detached through a SYSTEM scheduled task (`GolemLinkUpdate`), the program folder is backed up and restored on failure, and the agent confirms the new version afterwards | unit tests for the plan + script contents; no install was run on this host (policy) |
| Linux never escalates | Platform service runs with `NoNewPrivileges`; `apply()` stays `ready` and shows `sudo <staged> --upgrade --no-auto-pair --no-firewall` | `test_linux_apply_only_stages`, `test_linux_restart_is_operator_only` |
| Remote maintenance is opt-in per device | `allow_control` defaults off, enforced in `/peer/update/control`; remote flags learned through capabilities | live pair: control request before grant returned 403 with a clear message; after `allow_control` the same request performed a check |
| An AI/script cannot silently change a machine | Dashboard requires a confirmation dialog and CSRF; CLI `update apply` / `control … apply|restart` refuse without `--yes`; every action is audited | CLI parsing + dashboard API tests; manual API calls without confirmation returned 400 |
| Interrupted work is reported, not hidden | State persists atomically; an `installing` state with a matching version resolves to `updated`, otherwise it becomes an error naming the unfinished update | `test_interrupted_install_resolves` |
| Release tooling produces a real manifest | `packaging/make_update_manifest.py` hashes both installers and writes `dist/updates/manifest.json` plus copies | generated 1.2.0 manifest re-validated with the runtime validator; both artifacts verified |

Browser QA was done against the built dashboard served by an isolated source
agent on port 8099 paired with a second isolated agent on 8100. The in-app
browser surface was hidden, so screenshots were unavailable; the page DOM was
inspected instead: the Updates page rendered the available → verified states,
the install plan, the auto-update switches (saving through `PATCH
/api/settings` was confirmed in `config.json`) and a two-row fleet table with
the peer's controls enabled after the grant. Devices showed all five permission
switches and four remote-capability badges. The login form itself could not be
driven through the automation layer (value entered but submit did not fire);
the session was established through the same-origin login API and the rest of
the UI was exercised normally.

## 1.1.0 — host-light offload

| Behaviour | Implementation | Verified by |
| --- | --- | --- |
| New work must not start on an already-busy host | `Scheduler._light_guard()` reads the latest sample; at or above `keep_host_cpu_percent` (55) or `keep_host_ram_percent` (70) it forces placement on a healthy peer, overriding `local_first`/`balanced` | `test_light_guard_offloads_new_work_when_host_busy`, `test_light_guard_idle_host_keeps_work_local` |
| No peer ⇒ never starve | The guard only applies when `_candidate_remote` returns a healthy, fitting peer; otherwise local placement proceeds | `test_light_guard_still_runs_locally_when_no_peer_exists` |
| Do not dump work on a busy peer | Remote candidates with pressure score ≥ 80 are dropped | `test_busy_peer_is_not_an_offload_target` |
| The operator can turn it off | `keep_host_light` toggle in Settings and the first-run wizard; limits editable in Settings | TypeScript build; `test_light_guard_can_be_disabled` |
| Placement is immediate, not tick-driven | `submit_local` schedules a placement pass at once; `asyncio.Lock` serialises it against the 1 s loop | code review + full suite (no duplicate starts) |
| Critical host refuses even prepared work | `_schedule_queued` checks `can_accept_tasks()` for local execution, including tasks accepted from a peer | suite + inspection |
| You can see what weighs | `ResourceMonitor._top_processes()` adds `top_cpu`/`top_mem` (pid, name, cpu, rss) to every sample, cached 5 s; Resources renders a “Heavy processes” panel | live `collect()` on this host: `chrome.exe 35%`, `Qoder.exe 686 MB`, second sample 0.04 s; `test_top_processes_sorted_and_excludes_idle` |

Limit that remains true: GOLEM LINK only relocates its own tasks. It never
kills, suspends or migrates arbitrary OS processes (Chrome, Discord, …); the
Resources panel exists so the operator can see them.

## 1.0.2 regression fixes — freeze, lag, errors, “n/a” temperatures

| Symptom reported | Root cause | Fix | Verified by |
| --- | --- | --- | --- |
| Dashboard/app hangs and lags | Every task progress event called `refreshToken++`, which fired every polling hook at once; polls could overlap; `liveMetrics` lived in the main context so every 2 s sample re-rendered the whole app; the service log crash-looped on cp1252 `≥` | Live metrics moved to their own context; refresh events coalesced (600 ms); in-flight guards in `usePolling`, Shell, Tasks; bounded event queue drops oldest instead of raising; stdout/stderr and log file UTF-8 with `errors="replace"` | isolated agent on 18080: 86 s of live polling showed flat request counts (no stacking); browser DOM rendered real telemetry; log contains the “≥” alert line once, no traceback |
| Repeated errors / login screen never returns after session expiry | WS auth rejected *before* accept → browser saw an opaque HTTP 403 and reconnected forever | Accept first, then close with code `4401`; the store recognises 4401, returns to login and toasts “Session expired” | aiohttp WS probe: unauthenticated handshake receives close code 4401; authenticated first frame is `snapshot` |
| mDNS peers never appear | zeroconf runs handlers on its own thread; the async callback was dropped (“coroutine was never awaited”) | `_dispatch_change` schedules `_on_change` on the captured loop via `run_coroutine_threadsafe` and logs failures | `tests/test_discovery.py` (dispatch schedules on loop; no-loop is a no-op; node id parsing) |
| Temperature shows `n/a` | nvidia-smi prints `[N/A]` on a GT 730; `float()` raised and aborted the whole GPU list. Windows exposes no CPU sensors to the process | Tolerant `_gpu_number`/`_gpu_bytes`; GPU fallback in NodeCard/Resources (`71°C GPU`, source note); overload engine scores thermal protection from the GPU when platform sensors are missing; LHM/ACPI probes gated (process/admin) and cached (60 s ok / 900 s fail) | `tests/test_monitor.py` (6 tests incl. end-to-end `_gpu_stats` with `[N/A]`, ACPI decikelvin, LHM parse, event-bus drop-oldest, GPU thermal fallback); live UI showed “71°C GPU” and the Resources “GPU sensor” note; first `collect()` 5.95 s → 0.8 s |

> The two-node QA cluster ran on **one physical host** (`127.0.0.1`, ports
> 8080/8443 and 8090/8444). Real cross-machine latency, Wi-Fi behaviour,
> sleep/wake, and Deskflow input sharing were **not** exercised on physical
> hardware. See “Not verified”.

## 1.0.1 regression fixes

Everything below was found by auditing the live Debian↔Windows pair and the
1.0.0 code, then fixed and covered by tests:

| Defect in 1.0.0 | Fix | Verified by |
| --- | --- | --- |
| `PeerClient.request_pair` copied the *target's* OS/hostname/CPU into the pairing request, so the approving node stored the wrong identity for the new peer (live symptom: the Debian laptop appeared as `Windows · DESKTOP-8KH2T50 · Intel64`, 4 threads) | The request now sends `identity.info()`; the approving node saves real initiator metadata | cluster test asserts the stored row equals the initiator's own `os`/`hostname`/`cpu_model` |
| Rows already corrupted by 1.0.0 never corrected themselves | Every authenticated reconnect (and every `hello`) re-reads `/peer/info` and repairs changed metadata columns; `/api/devices` prefers live handshake data over the stored row | cluster test corrupts the row to `Plan9 / WRONG-HOST / fake`, reconnects, and asserts the repair |
| Automatic load sharing silently impossible: `allow_tasks` was hardcoded to 0 at pairing | New peers are paired with task sharing granted; a `user_version=2` migration re-enables it for existing peers, except peers the operator explicitly disabled (detected from the `peer-permissions` audit entry) | cluster test asserts `allow_tasks == 1` on both sides right after pairing; `test_allow_tasks_migration_keeps_explicit_denial` covers kept vs. explicit denial |
| “Move queued work away from overloaded nodes” was a UI switch wired to `pass` | A task pinned to a node whose pressure score is ≥ 80, or that reports itself blocked, is re-placed by the normal policy; the decision is written as a `rebalanced` task event. Running processes are still never migrated | cluster test forces the local pressure to blocked, submits a task pinned to the local node, and asserts it completes on the peer with a `rebalanced` event |
| Task executors displayed as hex node IDs; permission labels were ambiguous (“Run my tasks”) | Tables show device names; Devices says “Accept tasks from this device”, names which direction is blocked, and shows a green/red load-sharing line | TypeScript build; UI copy review |

## Automated tests

Command: `.venv\Scripts\python.exe -m pytest -q`
Result: **83 passed** in the 1.5.0 tree (69 from 1.4.1 plus 14 auto-relief
tests). Earlier milestones: 69 passed (1.4.1), 53 passed (1.3.0 tree: the 1.2.0
50-test set plus autopilot defaults, status and config-migration tests).
After the reconnect fix the 20-test suite also ran clean five times in a row
plus 12 standalone cluster runs; the link tests were then added and the full
suite passed again after the automatic-pairing changes.

An intermittent cluster failure (“peer is offline” when starting the second
transfer) was traced to the reconnect loop: one transient websocket drop marked
a live peer offline for the ~1–2 s retry window. The loop now requires two
consecutive connection failures before publishing `offline`, a successful
HTTPS connect resets the streak, and transfer starts wait up to 5 s for a
recently-seen peer to reconnect. Regression tests cover the hysteresis and the
grace period. The suite is still timing-sensitive under extreme CPU load; run
it without other heavy jobs.

| File | Tests | Coverage |
| --- | --- | --- |
| `tests/test_core.py` | 7 | Config roundtrip/atomic write, persistent Ed25519 identity + TLS files, scrypt password and HMAC tokens, X25519 pairing symmetry and six-digit code, database peer/task/alert flow, `allow_tasks` migration with explicit-denial protection |
| `tests/test_monitor.py` | 6 | Real psutil hardware sampling, metric publishing/history, overload alerts and task blocking, sustained thermal policy, missing temperature sensors, disk/network alerts |
| `tests/test_hardening.py` | 7 | Alert de-duplication across acknowledgement, serializable validation errors, idempotent pairing polling with expiry, offline hysteresis for transient reconnects, transfer-start reconnect grace period, bounded remote-dispatch failure (retry then terminal failure), generated KVM scripts with LF endings, left/right links, clipboard flag and auto-reconnect loop on/off |
| `tests/test_link.py` | 9 | Six-digit code matching rejects expired/missing/ambiguous requests; `init` prints the code and waits for approval; denial raises; `approve` refuses a mismatched code without approving, then approves the matching one; first-run setup accepts the password from `GOLEM_ADMIN_PASSWORD`; automatic pairing initiates with the only discovered machine, refuses empty/ambiguous LANs, and shows an incoming request instead of creating a duplicate |
| `tests/test_cluster.py` | 1 | Full two-node cluster on loopback (details below) |
| `tests/test_auto_relief.py` | 14 | Priority aging (lift, cap + FIFO, disable), running-task preemption (move to peer, no peer, cap/pin/short-run guards), idle busy-timer reset, relief status shape, HostGuard (pressure, comfort, disabled), worker priority flags/`nice`, adaptive sampling (slow, hysteresis, recover, fixed) |

The cluster test starts two real agents (monitor, scheduler, peer manager,
HTTPS peer servers, worker subprocesses) and asserts:

- distinct identities, pairing with a matching six-digit code, approval from
  the receiving node, and successful polling;
- the approving node stored the initiator’s **own** OS, hostname and CPU model
  (the 1.0.0 pairing bug), and `allow_tasks == 1` on both sides by default;
- a deliberately corrupted peer row (`Plan9`, `WRONG-HOST`, fake CPU) is
  repaired by the next reconnect;
- mutual online state with pinned certificates, exchanged metrics and latency;
- task submission is refused after the executing node revokes permission
  (the default grant can be withdrawn and the revocation is pushed live);
- a forced remote `disk_benchmark` runs in a real subprocess on node B
  (1 MiB, SHA-256, write throughput recorded) and the owner syncs the result;
- a task pinned to an overloaded local node is rebalanced to node B and the
  event log records `rebalanced`;
- a 3 MiB upload is chunked, renamed from `.glpart`, and byte-identical by
  SHA-256; a download lands under `downloads/<node>/<share>/…` with the exact
  content;
- stopping node B’s peer server marks it offline, and starting it again
  reconnects with the original pairing secret.

Dashboard build: `npm run build` → TypeScript + Vite succeeded, 2194 modules,
`dist/assets/index-kcW2XMKZ.js` 768 kB (about 210 kB gzip) and 40.8 kB CSS. Vite
warns the chunk exceeds 500 kB; it is served from the LAN and was accepted for
1.0.1.

## End-to-end QA log (live two-node deployment, 1.0.0)

The 1.0.0 deployment (Windows `DESKTOP-8KH2T50` @192.168.1.3 ↔ Debian
`GOLEM` @192.168.1.13, 33 ms) is what exposed the 1.0.1 defects:

- Three untargeted `disk_benchmark` tasks were dispatched to the Debian node
  and completed only after permission was granted by hand on that machine.
- `/api/devices` showed the Debian peer as `Windows`, host
  `DESKTOP-8KH2T50`, CPU `Intel64 Family 6 …` while the live `/peer/info` and
  the metrics websocket correctly reported `Linux`, `GOLEM`, `i5-2450M`;
  runtime core counts and latency were correct.
- With live `/peer/info` preferred over the stored row and the self-heal in
  1.0.1, both symptoms disappear on the first reconnect after upgrading; no
  re-pairing is required.

## End-to-end QA log (loopback cluster, browser + API, 1.0.0)

Pairing and transport:

- Pairing request from the QA node to the peer produced the same six-digit code
  on both sides (**491367**) and the peer fingerprint matched.
- After approval, the link came online at **392 ms** measured latency, later
  steady at **160–167 ms**.
- Restarting the primary agent reconnected to the peer automatically; metrics
  resumed without re-pairing.
- Idempotent polling regression confirmed: overlapping `/api/pairing/poll`
  calls no longer produce “unknown pairing request” after approval.

Distributed task execution:

- A remote `disk_benchmark` of 8 MiB ran on the peer: write **20.5 MB/s**,
  read **116.8 MB/s**, SHA-256 `9beb6dca…`. Task events showed
  `remote-running` → `remote-completed`; the peer’s task history records the
  primary as `source_node`.
- Negative permission test: after revoking task permission, dispatch was
  refused with “task execution is not authorized by the remote node”. The task
  went through `retry-scheduled` attempts 2/3 and 3/3 and ended `failed`
  (`t-a9e78b5118bb`, `retries=2`) instead of looping in `queued`.
- Permission restored and verified in the Devices UI (1.0.0 labels: “peer
  accepts my tasks” / “peer grants storage”; 1.0.1 groups the same state under
  “N runs my tasks” and a load-sharing line).

Storage:

- Download peer → primary: `peer.txt`, **12 B**, SHA-256 `d80b441f…950`,
  content byte-verified.
- Upload primary → peer: `qa-upload.bin`, **2,097,152 B**, SHA-256
  `638682fc…d2ef683ddac8fabe7194`; the peer’s own file hash matched, and no
  `.glpart` file remained.
- Remote browse + download from the Storage page worked; Transfers showed live
  progress and completion.

Dashboard pages exercised in a real browser (Chromium):

- **Overview** — real CPU/RAM, approved-storage free **21.9 GB of 465 GB**,
  cluster totals labelled as aggregates (not merged resources).
- **Devices** — peer card showed the 1.0.0 metadata corruption (Linux peer
  rendered as `Windows`, `DESKTOP-8KH2T50`, Intel CPU) while live metrics were
  correct; 1.0.1 prefers the live handshake and shows “Accept tasks from this
  device”, “N runs my tasks” and a load-sharing status line instead of the old
  “Run my tasks” chips.
- **Resources**, **Tasks** (submit, live progress, cancel, retry, history,
  events), **Storage** (shares, browse, download, upload, transfers),
  **Network**, **Alerts** (acknowledge), **Settings** (policy, thresholds,
  viewer password, input layout).
- **First-run wizard** — all 8 steps completed: OS/network detection,
  discovery, pairing, screen layout, share approval, scheduling policy,
  autostart, real connection test, summary with honest limitations.

Security checks observed during QA:

- Unauthenticated API calls return 401; writes without `X-CSRF-Token` return
  403; viewer role cannot perform admin actions.
- Requests from non-LAN addresses are rejected by the LAN guard (code path
  verified in tests and by inspection; no public-interface listener was
  exposed during QA).
- Peer requests without a valid HMAC token are rejected; the `/peer/info`
  endpoint exposes only public identity/capability data.
- A stale/denied pairing request cannot be approved twice; terminal results
  are cached for 300 s.

## Packaging validation

| Artifact | Status on this host |
| --- | --- |
| Windows onedir bundle (`dist\GOLEM-LINK`) | **Built and smoke-tested**, 45.5 MB / 104 files, `golem-link.exe --version` → `GOLEM LINK 1.3.0`, embedded dashboard asset `index-DawkEdiG.js` |
| `GOLEM-LINK-Portable-Windows.zip` | **Built** from the same bundle, 22,709,162 B, SHA-256 `0e5e7063b6d10633beb4609dd8433ba0cdc82d935eb2e5ca26d90f07915b8731` |
| `GOLEM-LINK-Setup-Windows.exe` | **Built** (onefile PyInstaller, 37,506,130 B, SHA-256 `3dc9be5fa5c94aeeee753da993cd16934a8b644ef4b39812063247b535ab81f9`). Embedded payload: 106 files / 30.5 MB, SHA-256 `6e9afb9bfa0c037f995fd042030d24bf15dca6a7973a9665e04793572a3b0b1f`; WinSW v2.12.0 SHA-256 `05b82d46…`. Verified: `--extract-only=<dir>` (no admin, no service) extracted the bundle, `golem-link.exe --version` → `GOLEM LINK 1.3.0`; the extracted agent ran isolated on 127.0.0.1:8098 and answered `/api/update` with `current_version: 1.3.0`, `auto_stage: true`, `auto_install: true`, `auto_install_supported: true`; its migration wrote `auto_stage`/`auto_install` into the sandbox `config.json`; the served dashboard references `index-DawkEdiG.js`. `/Update` itself was **not** run on this host (an installed service exists here; host-protection policy). |
| `GOLEM-LINK-Debian13.run` | **Built** (41,449,130 B, SHA-256 `110880d402b7fe01ef14eae861d943db00e28c819603d81d6339b163ac67de46`). Payload: portable CPython 3.13 + 34 CPython 3.13 manylinux x86_64 wheels + dashboard + systemd unit, 39 payload files / 41.4 MB. The embedded `golem_link-1.3.0-py3-none-any.whl` contains `VERSION = "1.3.0"`, `auto_stage` in `config.py` and the autopilot loop in `update.py`; payload dashboard asset `index-DawkEdiG.js`; `--check` passes (43 payload files, 34 wheels, bundled CPython 3.13) and the 508-line header passes `bash -n`. **Not executed on Debian yet** — the `--upgrade` path still needs one real install on the laptop. |
| `dist/updates/manifest.json` | **Generated** by `packaging/make_update_manifest.py`, 1,389 B, SHA-256 `5ad6c772df068dc9846857f1cdf449e9c543f03ca920d984d0b7647598859746`; re-validated with the runtime `normalize_manifest()` and both listed artifacts verified byte-for-byte against the copies beside it (size + SHA-256). |
| Debian `.deb` | **Not built** — `dpkg-deb` does not exist on Windows. `build-deb.sh` is syntax-checked (`bash -n`) and assembled for Debian 13. |
| systemd unit | Written at `packaging/linux/golem-link.service`; not installed on this host (host-protection policy). |
| Windows service | WinSW config and install/uninstall scripts written; **not registered on this host** (host-protection policy). |
| PowerShell scripts | Parsed with the PowerShell AST parser: 3/3 OK. |
| Shell scripts | `bash -n` / `sh -n` on the plain headers: OK (a self-extracting file cannot be `bash -n`-checked past its binary payload). |
| Python wheel | `pip wheel` succeeded: `golem_link-1.3.0-py3-none-any.whl`, built fresh into the Linux payload; it contains the autopilot update manager, peer update routes and the fleet/control CLI. |

Linux installer flow simulation (no Linux runtime available):

- The exact header was run on Windows with a sandbox root and stubs for
  `adduser`/`runuser`/`systemctl`/`ufw`/`ip` (apt is no longer called at all),
  with the real header payload appended. It exercised: payload extraction and
  SHA-256 verification, directory/user creation, the first-run setup against a
  real data directory, the "setup already completed" branch on a second run,
  generated-password output and 0600 file, the no-systemd branch, and
  `--uninstall` / `--uninstall --purge`.
- After the portable-runtime change the header was re-run in the sandbox with
  a stub interpreter standing in for the ELF CPython (Windows cannot execute
  it) and a checksummed fake runtime tarball: it unpacked the runtime,
  installed 34 wheels into `/opt/golem-link/site`, wrote the launcher
  `PYTHONPATH=… exec …/python/bin/python3.13 -m golem_link.cli "$@"`, installed
  the unit, wrote `/etc/golem-link/install.env`, applied the four ufw rules,
  restarted the service and ran `link auto`. A second run skipped the runtime
  unpack and wheel install (version marker matched), and
  `--uninstall --purge` removed `/opt/golem-link` and the data directory.
- The real runtime tarball was checked structurally with Python's `tarfile`:
  `python/bin/python3.13` is an ELF x86-64 executable with mode 0755 and
  RPATH `$ORIGIN/../lib`, contains `_ssl` and `_sqlite3`, ships `encodings`,
  and has no `test`, `tkinter`, `tcl9`, `ensurepip` or `pip` entries.
- After the no-typing change, the same sandbox confirmed the call sequence:
  no arguments → `setup` then `link auto --discovery-wait 12 --port 8080
  --wait 300`; `--no-auto-pair` → `setup` only; `--peer 192.168.1.20` →
  `link init --peer …`; `--approve 123456` → `link approve --code …`.
- Argument guards verified: `--approve 12ab`, `--approve 12345`, `--bogus`,
  `--password short`, and `--peer … --no-start` are all refused before any
  change.
- Not exercised: real systemd start, ufw rule application, `runuser`, and
  pairing against a live peer. These require the Debian laptop.

Automatic pairing with two real agents (this host, mDNS + UDP beacon on,
distinct ports 192.168.119.1):

- Node A ran `link auto --discovery-wait 20`; the agent discovered node B,
  started the request and printed `PAIRING CODE: 750448`.
- Node B’s dashboard API showed the same request and code; the approval was
  sent through the same endpoint the dashboard’s Approve button uses.
- A reported `Pairing approved: B is now pinned` and
  `Link is online: B (…98299f3d429f92a2)`; `/api/devices` listed the peer as
  `online` on both nodes. With no other machine present, `link auto` exits 0
  after the discovery window and leaves the dashboard ready.

Frozen bundle smoke test (isolated ports 8180/8543, temporary data dir):

- `golem-link.exe --version` → `GOLEM LINK 1.0.1`.
- Dashboard HTML served at `http://127.0.0.1:8180/` with CSP,
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`.
- `/api/setup/status` returned `needs_setup: true`, the frozen node ID and
  version.
- `/peer/info` over TLS 8543 returned identity, OS 10.0.19045, CPU model and
  core counts.
- The embedded dashboard exists at
  `dist\GOLEM-LINK\_internal\golem_link\web\index.html`.
- Process stopped cleanly by PID; ports released.

WinSW download (`v2.12.0`, 18.2 MB) from GitHub succeeded over three attempts
with a `.partial` file and a 5 MB minimum-size check; the file is then reused
from `build\windows\WinSW-x64.exe`.

## Not verified / honest limitations

- **Physical two-machine tests**: the 1.0.0 installers were deployed by the
  operator on the real pair (Windows 10 `DESKTOP-8KH2T50` ↔ Debian 13 `GOLEM`,
  33 ms): pairing completed from the dashboard API and three tasks ran on the
  Debian node. That run is what exposed the 1.0.1 defects. **The 1.0.1
  artifacts themselves were not deployed in this test session** — the Windows
  service and the laptop install are host-level operations left to the
  operator. Wi-Fi drops, sleep/wake and clock/route changes remain untested.
- **Input sharing was not exercised with Deskflow/Input Leap.** Detection,
  configuration generation, generated Linux/Windows server/client scripts and
  the auto-reconnect loop are covered by an automated test, but no KVM tool was
  installed, so actual keyboard/mouse edge switching, clipboard sync and
  post-sleep relaunch are untested. Wayland remains a documented limitation.
- **mDNS/beacon discovery across physical machines** is still unproven: the
  cluster test disables discovery, and the automatic-pairing smoke test ran two
  agents on one host (multicast on the same interface, discovered via the LAN
  address). Real Wi-Fi behaviour and cross-machine multicast remain to be
  tested with the laptop.
- **Windows service**: this host now runs a real `golem-link` service
  (`golem-link-service.exe` + `golem-link.exe`, dashboard on
  `http://127.0.0.1:8080`), installed by the operator outside this test run,
  so it is not a clean-room validation and this run did not start, stop or
  remove it. An earlier test build did self-elevate and install the service by
  accident: an MSYS-mangled `/ExtractOnly=` argument was not recognised, so the
  installer ran a real install. It was fully reverted — WinSW stop/uninstall,
  all four firewall rules deleted, shortcuts removed,
  `C:\Program Files\GOLEM LINK` and `C:\ProgramData\GolemLink` quarantined to
  `C:\ProgramData\golem-link-accidental-20261116\`. The installer now rejects
  unknown or mangled options before elevation and also accepts
  `--extract-only`.
- **Linux `.run`**: the 1.0.0 build was installed by the operator on the real
  Debian 13 laptop — the bundled CPython booted without system Python, the
  dashboard came up, the service started and pairing produced code `230686`.
  The **1.3.0** build was verified here by payload checksums, wheel-content
  assertions (autopilot update manager, `VERSION = "1.3.0"`, new dashboard
  asset), `--check` and header `bash -n`, but not executed on the laptop yet.
  Because the file carries CPython, it assumes an x86_64 (or x86-64-v2-capable)
  Debian 13 host; the installer refuses other architectures with a clear
  message.
- **The update path itself was not run end-to-end on a real machine.** The
  Windows `/Update` launcher and the Linux `--upgrade` mode are unit-tested
  (commands, backup/restore, version verification) and the loopback cluster
  proved manifest serving, peer artifact download and permission gating, but
  this host was not updated (an installed 1.1.0 service must not be touched
  from here) and the laptop was not touched. The first real rollout should be
  run by the operator with the rollback folder checked afterwards.
- **Autopilot was verified up to `ready`, not through a real install.** The
  isolated 1.3.0 bundle reported `auto_stage`/`auto_install` on, persisted the
  migrated defaults and checked successfully, but the actual Windows apply
  (scheduled task → installer → restart) and the Linux `sudo` upgrade were not
  executed on this host or the laptop. The first rollout should be watched on
  the Updates page and the `.previous` folder checked afterwards.
- **Automatic install after a reboot/restart window** and the dashboard's
  reconnect during a service restart were not observed on physical machines;
  the scheduled-task code paths are unit-tested only.
- **Manifest trust** is transitive: it comes from a paired node over pinned TLS
  and its artifacts are SHA-256 checked, but the manifest has no separate
  signature. Anyone who can pair a machine and get it to serve a manifest is
  trusted for updates; pair only machines you control.
- **Debian package** was not built (no `dpkg-deb`) and therefore not
  installed/upgraded/purged on Debian 13.
- **Code signing** is absent; Windows SmartScreen will warn on the unsigned
  installer.
- A task’s `memory_mb` is a hard address-space limit on Linux (`RLIMIT_AS`) but
  only a scheduling reservation on Windows, where no portable per-process
  limit is applied.
- The web bundle exceeds Vite’s 500 kB chunk warning (768 kB minified,
  210 kB gzip). Acceptable for a LAN dashboard; code-splitting is a future
  improvement.
- Runtime disk telemetry on this host currently warns that `F:\` has 5% free
  (real reading), which is expected behaviour, not a test failure.

## Reproduce

```powershell
cd F:\GOLEM-LINK
.venv\Scripts\python.exe -m pytest -q
cd apps\dashboard; npm run build; cd ..\..
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\windows\golem-link.spec
dist\GOLEM-LINK\golem-link.exe --version
.venv\Scripts\python.exe packaging\windows\build_setup_exe.py      # single-file exe
dist\GOLEM-LINK-Setup-Windows.exe --extract-only=F:\tmp\gl-test    # no admin, no service
.venv\Scripts\python.exe packaging\make_update_manifest.py --version 1.7.0 ^
  --windows dist\GOLEM-LINK-Setup-Windows.exe ^
  --linux dist\GOLEM-LINK-Debian13.run --notes-file docs\RELEASE_NOTES_1.7.0.md
```

```bash
# Any platform with Python 3.11+ (downloads the portable CPython + manylinux wheels once)
python3 packaging/linux/build_run.py
bash -n packaging/linux/golem-link-installer.sh
dist/GOLEM-LINK-Debian13.run --check

# On Debian 13
sudo ./GOLEM-LINK-Debian13.run --check
sudo ./GOLEM-LINK-Debian13.run        # install, find the peer, pair (prints the code)
sudo runuser -u golem-link -- /opt/golem-link/bin/golem-link link auto
sudo ./GOLEM-LINK-Debian13.run --uninstall --purge
# or: bash packaging/linux/build-deb.sh && sudo apt install ./build/GOLEM-LINK-Debian13.deb
```
