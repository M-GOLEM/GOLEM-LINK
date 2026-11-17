# GOLEM LINK 1.8.1

Release date: 2026-11-17

This is a fix for a failed self-update observed live on the Windows node during
the 1.8.0 rollout: the installer stopped the service, kept the 1.7.0 folder,
then wrote the new payload over files that the stopping process still held.
Extraction raised `PermissionError` on `_internal\_asyncio.pyd`, the installer
exited before re-registering the service, and the node stayed down until the
staged installer was run again by hand.

## What changed

- **The old folder is removed before extraction.** After the rollback copy is
  made, the installer deletes the previous program folder (retrying for up to
  20 s while the service releases its files) and extracts into an empty
  directory. A new build never overwrites a running or exiting one.
- **Locked files are retried.** `extract_payload` retries the whole extraction
  for up to 30 s on `PermissionError` instead of failing on the first file.
- **Failure restores the previous version.** If the folder still cannot be
  cleared or the payload cannot be written, the installer puts
  `GOLEM LINK.previous` back, re-registers the service and starts it before
  reporting the error. An update can no longer leave the node unregistered.
- **The same recovery is used for a failed start**, as before: the existing
  restore path now also covers extraction failures.

## Upgrade

Windows: Updates → Check → Download → Apply. The revert folder remains
`C:\Program Files\GOLEM LINK.previous`.

Linux: install the matching `.run` with `--upgrade`; the Linux installer is
unchanged in behaviour.

## Tests

103 tests pass (unchanged suite). The installer fix was verified by applying
1.8.1 to the live Windows node through its own update API.
