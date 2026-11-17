# GOLEM LINK 1.6.0 — text relay between paired machines

Copy a text on one computer and put it on the other's clipboard from the
dashboard. The link now carries short text as well as tasks and files.

## Send a copied text

1. Open **Text Relay** in the dashboard of the sending machine.
2. Paste, or press **Paste from clipboard**.
3. Pick the paired device and press **Send**.

The receiving node stores the text and, when its service can reach the
logged-in desktop, places it straight on the clipboard — on the other machine
you only press Ctrl+V. If it cannot reach the desktop (for example the Linux
agent runs as a service user without access to the X session), the text waits
in its **Received texts** list with a **Copy** button.

**Copy here** puts the composed text on the local clipboard, which is handy on
the laptop where the browser cannot write the clipboard over plain HTTP.

## What is enforced

- Only paired devices that this node grants **control** to can send text, and
  the sender must also be granted control by the receiver. Both ends check.
- Text only; files and images are not relayed. 256 KB per text by default,
  configurable, and the last 50 items are kept.
- Every received item records whether it was copied automatically, with which
  method, or the exact reason it could not be.
- Sending and copying are written to the audit log.

## Platform notes

- **Windows:** the service runs in session 0, so it opens the active console
  session token and runs a hidden, STA PowerShell helper that sets the
  clipboard. If nobody is logged on, the helper is not started and the item
  stays in the inbox.
- **Linux:** the agent copies through `xclip`, `xsel` or `wl-copy` in the
  active X11/Wayland session when it can reach it. When it cannot, the
  dashboard's Copy button copies in the browser's own session.
- Peers older than 1.6.0 have no relay route; the sender reports
  "runs an older GOLEM LINK without text relay" instead of a raw HTTP error.

## Settings and diagnostics

Settings → **Text relay** has two switches: allow relay on this node, and copy
received text automatically. `/api/diagnostics` reports the relay state and
the last item.
