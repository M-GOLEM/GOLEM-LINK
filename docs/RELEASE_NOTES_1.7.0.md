# GOLEM LINK 1.7.0 — paste into the relay from any browser

The Text Relay page could always *send* text, but on a plain-HTTP dashboard the
browser refuses to read the clipboard, so on the laptop you had to paste into
the box by hand. This release lets the agent read the desktop clipboard itself.

## What changed

- **"Paste from clipboard" now works over plain HTTP.** The page first tries
  the browser API; when the browser declines (no secure context on the LAN),
  it asks the agent with `POST /api/clipboard/read`. The agent reads the
  clipboard of the logged-in desktop — directly when it runs inside that
  session, through the console-session token when it runs as a Windows service,
  and through `xclip` / `xsel` / `wl-paste` on Linux.
- **The same session rules as automatic copy.** If nobody is logged on, the
  service cannot reach the desktop and the page says so instead of silently
  leaving the box empty.
- **Reads are explicit and audited.** The call needs the admin session, the
  CSRF token and the relay switch; each successful read is written to the
  audit log with its character count. Text longer than `max_chars` is refused
  rather than truncated.
- **Linux session handling was shared** between copy and read, so both use the
  same detected X11/Wayland session and the same "service cannot reach the
  desktop" answer.

## Platform notes

- **Windows:** the desktop-session read uses `GetClipboardData(CF_UNICODETEXT)`;
  from session 0 it launches the same hidden, STA PowerShell helper used for
  writing and brings the text back through a UTF-8 file.
- **Linux:** `wl-paste --no-newline` on Wayland, otherwise `xclip -o` or
  `xsel --output`, run as the session user when the agent has root. Without
  access to the session the request fails with a readable reason.
- A clipboard containing no text (only image/file) answers with an empty
  string; the page reports "The clipboard has no text".

## Upgrade

Same as before: the Windows node updates itself through its own API and keeps
`<install_dir>.previous`; the laptop needs the 1.7.0 `.run` from the operator.
Text relay between two nodes still needs both ends on 1.6.0 or newer.
