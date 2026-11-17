import {
  ArrowRight,
  ClipboardCopy,
  ClipboardPaste,
  CloudOff,
  Copy,
  Laptop,
  Monitor,
  Send,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  del,
  get,
  post,
  timeAgo,
  type ClipboardBroadcastResult,
  type ClipboardCopyResult,
  type ClipboardInbox,
  type ClipboardReadResult,
  type ClipboardSendResult,
  type Cluster,
} from "../api";
import { Empty, Panel, StatusDot } from "../components/ui";
import { usePolling } from "../hooks";
import { useApp } from "../store";

export function ClipboardPage() {
  const { me, toast, refresh } = useApp();
  const inboxQ = usePolling(() => get<ClipboardInbox>("/api/clipboard?limit=20"), 4000);
  const clusterQ = usePolling(() => get<Cluster>("/api/cluster"), 5000);
  const [text, setText] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const canWrite = me?.role === "admin";
  const status = inboxQ.data?.status;
  const items = inboxQ.data?.items ?? [];

  const peers = useMemo(() => clusterQ.data?.peers ?? [], [clusterQ.data]);
  const usable = peers.filter((peer) => peer.status === "online" && peer.remote_allows_control);

  useEffect(() => {
    if (!target && usable.length) setTarget(usable[0].node_id);
    if (target && !usable.some((peer) => peer.node_id === target)) setTarget(usable[0]?.node_id ?? "");
  }, [usable, target]);

  const paste = async () => {
    setBusy("paste");
    try {
      if (navigator.clipboard && window.isSecureContext) {
        try {
          const value = await navigator.clipboard.readText();
          if (value) {
            setText(value);
            return;
          }
        } catch {
          // Plain HTTP on the LAN has no clipboard permission; ask the agent.
        }
      }
      const result = await post<ClipboardReadResult>("/api/clipboard/read");
      if (!result.text) {
        toast("The clipboard has no text", "info");
        return;
      }
      setText(result.text);
      toast(`Pasted ${result.chars.toLocaleString()} characters from this machine`, "ok");
    } catch (err) {
      toast(
        err instanceof Error ? err.message : "could not read the clipboard — paste into the box instead",
        "error",
      );
    } finally {
      setBusy(null);
    }
  };

  const send = async () => {
    if (!target) {
      toast("Choose a target device first", "error");
      return;
    }
    setBusy("send");
    try {
      const result = await post<ClipboardSendResult>("/api/clipboard/send", {
        peer_id: target,
        text,
      });
      toast(
        result.copied
          ? `${result.peer} copied it (${result.copy_method ?? "desktop clipboard"})`
          : `${result.peer} received it; copy it from its inbox`,
        result.copied ? "ok" : "info",
      );
      setText("");
      inboxQ.reload();
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not send the text", "error");
    } finally {
      setBusy(null);
    }
  };

  const sendAll = async () => {
    setBusy("send-all");
    try {
      const result = await post<ClipboardBroadcastResult>("/api/clipboard/send-all", { text });
      const failed = result.results.filter((row) => !row.ok);
      const summary = `${result.sent} of ${result.results.length} devices received ${result.chars.toLocaleString()} chars · ${result.copied} copied automatically`;
      toast(
        failed.length ? `${summary}. Failed: ${failed.map((row) => row.peer).join(", ")}` : summary,
        failed.length ? "info" : "ok",
      );
      if (result.sent > 0) setText("");
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not send the text", "error");
    } finally {
      setBusy(null);
    }
  };

  const copyLocal = async () => {
    setBusy("copy");
    try {
      const result = await post<ClipboardCopyResult>("/api/clipboard/copy", { text });
      if (result.copied) toast(`Copied on this machine (${result.method})`, "ok");
      else toast(result.error ?? "this machine's clipboard was not reachable", "error");
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not copy the text", "error");
    } finally {
      setBusy(null);
    }
  };

  const copyItem = async (id: string) => {
    setBusy(id);
    try {
      const result = await post<ClipboardCopyResult>(`/api/clipboard/${id}/copy`);
      if (result.copied) toast(`Copied on this machine (${result.method})`, "ok");
      else toast(result.error ?? "this machine's clipboard was not reachable", "error");
      inboxQ.reload();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not copy the item", "error");
    } finally {
      setBusy(null);
    }
  };

  const removeItem = async (id: string) => {
    setBusy(id);
    try {
      await del(`/api/clipboard/${id}`);
      if (expanded === id) setExpanded(null);
      inboxQ.reload();
      toast("Removed from the inbox", "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not remove the item", "error");
    } finally {
      setBusy(null);
    }
  };

  const clearAll = async () => {
    if (!window.confirm("Remove every received text from this machine?")) return;
    setBusy("clear");
    try {
      const result = await del<{ cleared: number }>("/api/clipboard");
      setExpanded(null);
      inboxQ.reload();
      toast(`Cleared ${result.cleared} item${result.cleared === 1 ? "" : "s"}`, "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not clear the inbox", "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      {inboxQ.error && (
        <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          Cannot load the text relay: {inboxQ.error}
        </div>
      )}

      {status && !status.enabled && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-400/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
          <ShieldAlert size={16} className="mt-0.5 shrink-0" />
          <span>
            Text relay is off on this node. Turn it on in <strong>Settings → Text relay</strong> to send or receive
            text.
          </span>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Send a text"
          subtitle="Copy on this machine, then send it to the other computer's clipboard."
          actions={
            status && (
              <span className="flex items-center gap-2 text-[11px] text-slate-500">
                <StatusDot state={status.enabled ? "online" : "offline"} />
                {status.enabled ? (status.auto_copy ? "auto-copy on" : "manual copy") : "relay off"}
              </span>
            )
          }
        >
          <div className="space-y-3">
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={7}
              placeholder="Paste the text you copied…"
              className="w-full resize-y rounded-xl border border-ink-700 bg-ink-950/60 px-3 py-2 font-mono text-[12px] leading-relaxed text-slate-200 outline-none transition focus:border-cyan-400/60"
            />
            <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
              <span>
                {text.length.toLocaleString()} / {(status?.max_chars ?? 0).toLocaleString()} characters
              </span>
              <button
                type="button"
                className="btn-ghost"
                onClick={paste}
                disabled={!canWrite || busy !== null}
                title="Read this machine's clipboard (browser permission or through the agent)"
              >
                <ClipboardPaste size={13} /> {busy === "paste" ? "Reading…" : "Paste from clipboard"}
              </button>
            </div>

            <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
              <label className="block">
                <span className="label">Target device</span>
                <select
                  value={target}
                  onChange={(event) => setTarget(event.target.value)}
                  className="input mt-1 w-full"
                >
                  {usable.length === 0 && <option value="">No online device accepts control</option>}
                  {peers.map((peer) => (
                    <option
                      key={peer.node_id}
                      value={peer.node_id}
                      disabled={peer.status !== "online" || !peer.remote_allows_control}
                    >
                      {peer.name}
                      {peer.status !== "online" ? " (offline)" : !peer.remote_allows_control ? " (control off)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex items-end gap-2">
                <button
                  type="button"
                  className="btn-primary"
                  onClick={send}
                  disabled={!canWrite || !text.trim() || !target || busy !== null || !status?.enabled}
                >
                  <Send size={14} /> {busy === "send" ? "Sending…" : "Send"}
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={copyLocal}
                  disabled={!canWrite || !text.trim() || busy !== null}
                  title="Copy this text on this machine"
                >
                  <Copy size={14} /> {busy === "copy" ? "Copying…" : "Copy here"}
                </button>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <button
                type="button"
                className="btn-ghost"
                onClick={sendAll}
                disabled={!canWrite || !text.trim() || usable.length === 0 || busy !== null || !status?.enabled}
                title="Send this text to every online device that grants control"
              >
                <Send size={14} /> {busy === "send-all" ? "Sending to all…" : `Send to all (${usable.length})`}
              </button>
              <span className="text-[11px] text-slate-500">One unreachable device does not stop the others.</span>
            </div>

            <p className="text-[11px] leading-relaxed text-slate-500">
              The receiving node needs the same release and must grant control to this device. When its service cannot
              reach the desktop, the text waits in its inbox below with a Copy button.
            </p>
            {peers.some((peer) => peer.status === "online" && !peer.remote_allows_control) && (
              <p className="flex items-start gap-2 text-[11px] text-amber-200/80">
                <CloudOff size={13} className="mt-0.5 shrink-0" />
                A paired device is online but does not grant control — enable it in Devices → permissions.
              </p>
            )}
          </div>
        </Panel>

        <Panel
          title="Received texts"
          subtitle="Newest first; the last 50 are kept on this machine."
          actions={
            <div className="flex items-center gap-2">
              <button type="button" className="btn-ghost" onClick={() => inboxQ.reload()}>
                Refresh
              </button>
              <button
                type="button"
                className="btn-ghost text-rose-300/90"
                onClick={clearAll}
                disabled={!canWrite || items.length === 0 || busy !== null}
                title="Remove every received text from this machine"
              >
                <Trash2 size={13} /> {busy === "clear" ? "Clearing…" : "Clear"}
              </button>
            </div>
          }
        >
          {items.length === 0 ? (
            <Empty
              icon={ClipboardCopy}
              title="Nothing received yet"
              hint="Text sent from another paired computer appears here, ready to paste."
            />
          ) : (
            <ul className="space-y-2">
              {items.map((item) => (
                <li
                  key={item.id}
                  className="rounded-xl border border-ink-700/60 bg-ink-900/40 px-3 py-2.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
                    <span className="flex items-center gap-1.5">
                      <Laptop size={12} /> {item.from_name}
                      <ArrowRight size={11} className="opacity-50" />
                      <Monitor size={12} /> this node
                    </span>
                    <span>{timeAgo(item.ts)}</span>
                  </div>
                  <p
                    className="mt-1.5 cursor-pointer whitespace-pre-wrap break-words font-mono text-[12px] text-slate-200"
                    onClick={() => setExpanded(expanded === item.id ? null : item.id)}
                    title={expanded === item.id ? "Collapse" : "Show the full text"}
                  >
                    {expanded === item.id
                      ? item.text ?? item.preview
                      : item.preview.length >= 160
                        ? `${item.preview.slice(0, 160)}…`
                        : item.preview}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[11px] text-slate-500">
                      {item.chars.toLocaleString()} chars ·{" "}
                      {item.copied ? (
                        <span className="text-emerald-300">copied automatically ({item.copy_method})</span>
                      ) : (
                        <span className="text-amber-300">{item.copy_error ?? "not copied"}</span>
                      )}
                    </span>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        className="btn-ghost"
                        onClick={() => copyItem(item.id)}
                        disabled={!canWrite || busy !== null}
                      >
                        <Copy size={13} /> {busy === item.id ? "Copying…" : "Copy"}
                      </button>
                      <button
                        type="button"
                        className="btn-ghost text-rose-300/90"
                        onClick={() => removeItem(item.id)}
                        disabled={!canWrite || busy !== null}
                        title="Remove this text from the inbox"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
