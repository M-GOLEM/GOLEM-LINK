"""Read and place text on the logged-in desktop's clipboard.

A clipboard belongs to a desktop session, and the agent usually runs outside
one (a Windows service in session 0, a system user under systemd). This module
reads and writes into the interactive session when the platform allows it and
raises :class:`ClipboardUnavailable` with a readable reason when it does not, so
the relay can offer a manual fallback instead of pretending it worked.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
TOKEN_ALL_ACCESS = 0xF01FF
SECURITY_IMPERSONATION = 2
TOKEN_PRIMARY = 1
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000
INVALID_SESSION = 0xFFFFFFFF


class ClipboardUnavailable(RuntimeError):
    """The text was stored, but this machine's desktop clipboard was not reachable."""


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def set_clipboard_script(path: str) -> str:
    """PowerShell that copies a UTF-8 file to the clipboard, then deletes it.

    The text travels through a file instead of the command line: clipboard
    text can contain quotes, newlines and non-ASCII characters, and the service
    err file lets the caller report the real Win32/.NET failure.
    """
    p = _ps_quote(path)
    return (
        "$ErrorActionPreference='Stop';"
        f"$p={p};"
        "try{"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$t=[IO.File]::ReadAllText($p,[Text.Encoding]::UTF8);"
        "$done=$false;"
        "for($i=0;$i -lt 10 -and -not $done;$i++){"
        "try{[System.Windows.Forms.Clipboard]::SetText($t);$done=$true}"
        "catch{Start-Sleep -Milliseconds 120}"
        "};"
        "if(-not $done){throw 'the clipboard is locked by another program'}"
        "}catch{"
        "$msg=$_.Exception.Message;"
        "try{$msg|Set-Content -LiteralPath ($p+'.err') -Encoding UTF8}catch{};"
        "Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue;"
        "exit 1"
        "};"
        "Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue;"
        "exit 0"
    )


def get_clipboard_script(path: str) -> str:
    """PowerShell that writes the clipboard text to a UTF-8 file."""
    p = _ps_quote(path)
    return (
        "$ErrorActionPreference='Stop';"
        f"$p={p};"
        "try{"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$t=$null;"
        "for($i=0;$i -lt 10 -and $null -eq $t;$i++){"
        "try{$t=[System.Windows.Forms.Clipboard]::GetText()}"
        "catch{Start-Sleep -Milliseconds 120}"
        "};"
        "if($null -eq $t){throw 'the clipboard is locked by another program'};"
        "[IO.File]::WriteAllText($p,$t,[Text.UTF8Encoding]::new($false))"
        "}catch{"
        "$msg=$_.Exception.Message;"
        "try{$msg|Set-Content -LiteralPath ($p+'.err') -Encoding UTF8}catch{};"
        "Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue;"
        "exit 1"
        "};"
        "exit 0"
    )


def _windows_direct(text: str) -> None:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    if not user32.OpenClipboard(None):
        raise ClipboardUnavailable(f"OpenClipboard failed (error {ctypes.get_last_error()})")
    try:
        user32.EmptyClipboard()
        payload = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not handle:
            raise ClipboardUnavailable("GlobalAlloc failed")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            raise ClipboardUnavailable("GlobalLock failed")
        ctypes.memmove(pointer, payload, len(payload))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise ClipboardUnavailable(f"SetClipboardData failed (error {ctypes.get_last_error()})")
    finally:
        user32.CloseClipboard()


def _windows_direct_read() -> str:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]

    if not user32.OpenClipboard(None):
        raise ClipboardUnavailable(f"OpenClipboard failed (error {ctypes.get_last_error()})")
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            raise ClipboardUnavailable("the clipboard contains no text")
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            raise ClipboardUnavailable(f"GetClipboardData failed (error {ctypes.get_last_error()})")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ClipboardUnavailable("GlobalLock failed")
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.c_ulong),
        ("dwY", ctypes.c_ulong),
        ("dwXSize", ctypes.c_ulong),
        ("dwYSize", ctypes.c_ulong),
        ("dwXCountChars", ctypes.c_ulong),
        ("dwYCountChars", ctypes.c_ulong),
        ("dwFillAttribute", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("wShowWindow", ctypes.c_ushort),
        ("cbReserved2", ctypes.c_ushort),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_ulong),
        ("dwThreadId", ctypes.c_ulong),
    ]


def _windows_session_id() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcessId.restype = ctypes.c_ulong
    kernel32.ProcessIdToSessionId.argtypes = [ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    session = ctypes.c_ulong(0)
    if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)):
        return 0
    return int(session.value)


def _powershell() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("powershell") or shutil.which("pwsh")
    if not found:
        raise ClipboardUnavailable("PowerShell was not found")
    return found


def _windows_launch_in_session(script: str, timeout: float) -> int | None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)

    kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
    wtsapi32.WTSQueryUserToken.argtypes = [wintypes.ULONG, ctypes.POINTER(wintypes.HANDLE)]
    wtsapi32.WTSQueryUserToken.restype = wintypes.BOOL
    advapi32.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_int, ctypes.c_int, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.DuplicateTokenEx.restype = wintypes.BOOL
    userenv.CreateEnvironmentBlock.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.BOOL,
    ]
    userenv.CreateEnvironmentBlock.restype = wintypes.BOOL
    userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
    kernel32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p,
        wintypes.LPCWSTR, ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInfo),
    ]
    kernel32.CreateProcessAsUserW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    session_id = kernel32.WTSGetActiveConsoleSessionId()
    if session_id == INVALID_SESSION:
        raise ClipboardUnavailable("no user is logged on at this machine")

    token = wintypes.HANDLE()
    if not wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(token)):
        raise ClipboardUnavailable(
            f"cannot open the desktop session token (error {ctypes.get_last_error()})"
        )
    primary = wintypes.HANDLE()
    try:
        if not advapi32.DuplicateTokenEx(
            token, TOKEN_ALL_ACCESS, None, SECURITY_IMPERSONATION, TOKEN_PRIMARY,
            ctypes.byref(primary),
        ):
            raise ClipboardUnavailable(
                f"cannot duplicate the session token (error {ctypes.get_last_error()})"
            )
        env = ctypes.c_void_p()
        have_env = bool(userenv.CreateEnvironmentBlock(ctypes.byref(env), primary, False))
        command = f'"{_powershell()}" -NoProfile -NonInteractive -WindowStyle Hidden -STA -Command "{script}"'
        startup = _StartupInfo()
        startup.cb = ctypes.sizeof(_StartupInfo)
        process = _ProcessInfo()
        buffer = ctypes.create_unicode_buffer(command)
        try:
            created = kernel32.CreateProcessAsUserW(
                primary, None, buffer, None, None, False,
                CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
                env if have_env else None, None,
                ctypes.byref(startup), ctypes.byref(process),
            )
            if not created:
                raise ClipboardUnavailable(
                    f"creating the clipboard helper failed (error {ctypes.get_last_error()})"
                )
            waited = kernel32.WaitForSingleObject(process.hProcess, int(timeout * 1000))
            if waited != 0:
                return None
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code)):
                return None
            return int(code.value)
        finally:
            if process.hProcess:
                kernel32.CloseHandle(process.hProcess)
            if process.hThread:
                kernel32.CloseHandle(process.hThread)
            if have_env:
                try:
                    userenv.DestroyEnvironmentBlock(env)
                except Exception:
                    pass
    finally:
        kernel32.CloseHandle(token)
        if primary:
            kernel32.CloseHandle(primary)


def _helper_error(target: Path) -> str:
    error_file = target.with_name(target.name + ".err")
    detail = ""
    if error_file.is_file():
        try:
            detail = error_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            detail = ""
        try:
            error_file.unlink()
        except OSError:
            pass
    return detail


def _windows_copy(text: str, data_dir: Path, timeout: float) -> str:
    if _windows_session_id() != 0:
        _windows_direct(text)
        return "desktop-session"

    folder = Path(data_dir) / "clipboard"
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("clip-*.txt*"):
        try:
            if time.time() - stale.stat().st_mtime > 3600:
                stale.unlink()
        except OSError:
            pass
    target = folder / f"clip-{uuid.uuid4().hex}.txt"
    target.write_text(text, encoding="utf-8")
    script = set_clipboard_script(str(target))
    code = _windows_launch_in_session(script, timeout)
    detail = _helper_error(target)
    if code == 0:
        return "console-session"
    if code is None:
        raise ClipboardUnavailable("the clipboard helper did not finish in time")
    raise ClipboardUnavailable(detail or f"the clipboard helper exited with code {code}")


def _windows_read(data_dir: Path, timeout: float) -> str:
    if _windows_session_id() != 0:
        return _windows_direct_read()

    folder = Path(data_dir) / "clipboard"
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("read-*.txt*"):
        try:
            if time.time() - stale.stat().st_mtime > 3600:
                stale.unlink()
        except OSError:
            pass
    target = folder / f"read-{uuid.uuid4().hex}.txt"
    script = get_clipboard_script(str(target))
    code = _windows_launch_in_session(script, timeout)
    detail = _helper_error(target)
    try:
        if code == 0:
            if not target.is_file():
                raise ClipboardUnavailable("the clipboard helper returned no text")
            return target.read_text(encoding="utf-8")
        if code is None:
            raise ClipboardUnavailable("the clipboard helper did not finish in time")
        raise ClipboardUnavailable(detail or f"the clipboard helper exited with code {code}")
    finally:
        try:
            target.unlink()
        except OSError:
            pass


def _parse_sessions(legend: str) -> list[dict]:
    sessions = []
    for line in legend.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        sessions.append({"id": parts[0], "uid": parts[1], "user": parts[2]})
    return sessions


def _linux_session() -> tuple[dict, str] | None:
    if not shutil.which("loginctl"):
        return None
    try:
        listing = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for session in _parse_sessions(listing.stdout or ""):
        try:
            shown = subprocess.run(
                ["loginctl", "show-session", session["id"], "-p", "Type", "-p", "Active", "-p", "Remote"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        props = {}
        for line in (shown.stdout or "").splitlines():
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
        if props.get("Active") != "yes" or props.get("Remote") == "yes":
            continue
        kind = props.get("Type", "")
        if kind not in ("x11", "wayland"):
            continue
        env = {"XDG_RUNTIME_DIR": f"/run/user/{session['uid']}", "DISPLAY": ":0"}
        if kind == "wayland":
            env["WAYLAND_DISPLAY"] = "wayland-0"
        return session, kind
    return None


def _linux_prefix(session: dict, kind: str) -> list[str]:
    runtime = f"/run/user/{session['uid']}"
    base_env = ["XDG_RUNTIME_DIR=" + runtime, "DISPLAY=:0"]
    if kind == "wayland":
        base_env.append("WAYLAND_DISPLAY=wayland-0")
    if os.geteuid() == 0:
        return ["runuser", "-u", session["user"], "--", "env", *base_env]
    if Path(runtime).owner() == str(session["user"]) and os.environ.get("DISPLAY"):
        # Running as the desktop user (manual foreground run): use our own env.
        return []
    raise ClipboardUnavailable(
        "the service user cannot reach this desktop; run the agent in the session"
    )


def _linux_copy(text: str, timeout: float) -> str:
    found = _linux_session()
    if found is None:
        raise ClipboardUnavailable("no active desktop session was found")
    session, kind = found
    candidates = []
    if kind == "wayland" and shutil.which("wl-copy"):
        candidates.append([shutil.which("wl-copy")])
    if shutil.which("xclip"):
        candidates.append([shutil.which("xclip"), "-selection", "clipboard", "-i"])
    if shutil.which("xsel"):
        candidates.append([shutil.which("xsel"), "--clipboard", "--input"])
    if not candidates:
        raise ClipboardUnavailable("install xclip or wl-copy to copy into the desktop")
    prefix = _linux_prefix(session, kind)
    last_error = ""
    for candidate in candidates:
        try:
            proc = subprocess.run(
                [*prefix, *candidate],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            last_error = str(exc)
            continue
        if proc.returncode == 0:
            return Path(candidate[0]).name
        last_error = (proc.stderr or b"").decode("utf-8", "replace").strip() or f"exit {proc.returncode}"
    raise ClipboardUnavailable(last_error or "the clipboard tool failed")


def _linux_read(timeout: float) -> str:
    found = _linux_session()
    if found is None:
        raise ClipboardUnavailable("no active desktop session was found")
    session, kind = found
    candidates = []
    if kind == "wayland" and shutil.which("wl-paste"):
        candidates.append([shutil.which("wl-paste"), "--no-newline"])
    if shutil.which("xclip"):
        candidates.append([shutil.which("xclip"), "-selection", "clipboard", "-o"])
    if shutil.which("xsel"):
        candidates.append([shutil.which("xsel"), "--clipboard", "--output"])
    if not candidates:
        raise ClipboardUnavailable("install xclip or wl-paste to read the desktop clipboard")
    prefix = _linux_prefix(session, kind)
    last_error = ""
    for candidate in candidates:
        try:
            proc = subprocess.run(
                [*prefix, *candidate],
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            last_error = str(exc)
            continue
        if proc.returncode == 0:
            return (proc.stdout or b"").decode("utf-8", "replace")
        last_error = (proc.stderr or b"").decode("utf-8", "replace").strip() or f"exit {proc.returncode}"
    raise ClipboardUnavailable(last_error or "the clipboard tool failed")


def copy_text(text: str, *, data_dir: Path | str, timeout: float = 15.0) -> str:
    """Copy ``text`` to this machine's desktop clipboard and name the method."""
    if os.name == "nt":
        return _windows_copy(text, Path(data_dir), timeout)
    if os.name == "posix" and os.uname().sysname == "Linux":
        return _linux_copy(text, timeout)
    raise ClipboardUnavailable("clipboard writing is not supported on this platform")


def read_text(*, data_dir: Path | str, timeout: float = 15.0) -> str:
    """Return this machine's desktop clipboard text."""
    if os.name == "nt":
        return _windows_read(Path(data_dir), timeout)
    if os.name == "posix" and os.uname().sysname == "Linux":
        return _linux_read(timeout)
    raise ClipboardUnavailable("clipboard reading is not supported on this platform")
