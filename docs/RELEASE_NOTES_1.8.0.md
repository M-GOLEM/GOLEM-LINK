# GOLEM LINK 1.8.0

Release date: 2026-11-17

Text relay becomes usable with more than one paired device, and the received
inbox can be cleaned up without editing the database by hand. The peer wire
format is unchanged, so a 1.7 node still receives a broadcast normally; only
its dashboard lacks the new buttons.

## Send to every paired device

- `POST /api/clipboard/send-all` sends the text to each peer that is online
  and grants control. The response lists one row per device with `ok`,
  `copied` and an error string when that device failed.
- Failures are isolated per device: an offline, older (HTTP 404) or
  unreachable peer is reported and skipped, and the remaining devices still
  receive the text. Only a text that is empty, too long or sent while relay is
  disabled is rejected before any request.
- The Text Relay page adds **Send to all (n)** beside Send, where n is the
  number of currently usable devices. The toast says how many devices received
  the text, how many copied it automatically, and names any failures.
- Each broadcast is audited as `clipboard-broadcast` with the device count and
  text length.

## Inbox cleanup

- `DELETE /api/clipboard/{id}` removes one received text;
  `DELETE /api/clipboard` clears the whole inbox and returns the count.
- The dashboard adds a remove button on each item and a Clear action on the
  Received texts panel. Clearing asks for confirmation first.
- Both routes require an admin session and the CSRF token, and write
  `clipboard-delete` / `clipboard-clear` audit rows.
- Removal touches only this machine's inbox; it never contacts a peer.

## Limits and compatibility

- Same limits as 1.6/1.7: 256 KB per text by default (`clipboard.max_chars`),
  50 retained items (`clipboard.keep_items`), and control granted on the
  receiving node for each send.
- Broadcast is sequential and bounded by the same 30 s timeout per device as
  a single send.
- No configuration or database migration is required.

## Upgrade

Windows: Updates → Check → Download → Apply (the installer keeps
`C:\Program Files\GOLEM LINK.previous` as the revert path).

Linux: install the matching `.run` with `--upgrade` (the installer backs up
`/opt/golem-link` and the unit). A node that has no artifact source yet can
use the new manifest under `dist/updates/`.

## Tests

103 tests pass, including four new ones: broadcast to several peers, one
failed peer not blocking the rest, no usable peer rejected before a request,
and inbox delete/clear including an unknown id.
