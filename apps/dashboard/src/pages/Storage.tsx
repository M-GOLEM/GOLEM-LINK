import {
  ArrowDown,
  ArrowUp,
  ChevronRight,
  Download,
  File,
  Folder,
  FolderOpen,
  HardDrive,
  Loader2,
  Plus,
  RefreshCw,
  Server,
  ShieldAlert,
  TerminalSquare,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  del,
  fmtBytes,
  fmtRate,
  get,
  patch,
  post,
  timeAgo,
  type Cluster,
  type SettingsPayload,
  type Share,
  type StorageEntry,
  type Transfer,
  type Volume,
} from "../api";
import { CopyText, Empty, Field, Modal, Panel, Spinner, StatusDot, Toggle, UsageBar } from "../components/ui";
import { usePolling } from "../hooks";
import { useApp } from "../store";

type Tab = "shares" | "browse" | "transfers" | "smb";

interface EntryResponse {
  entries: StorageEntry[];
}

function Breadcrumbs({ path, onNavigate }: { path: string; onNavigate: (path: string) => void }) {
  const parts = path.split("/").filter(Boolean);
  return (
    <div className="flex flex-wrap items-center gap-1 text-[11px]">
      <button className="rounded px-1.5 py-0.5 font-mono text-cyan-400/80 hover:bg-cyan-400/10" onClick={() => onNavigate("")}>
        /
      </button>
      {parts.map((part, index) => (
        <span key={`${part}-${index}`} className="flex items-center gap-1">
          <ChevronRight size={10} className="text-slate-600" />
          <button
            className="max-w-[160px] truncate rounded px-1.5 py-0.5 font-mono text-slate-300 hover:bg-ink-800"
            onClick={() => onNavigate(parts.slice(0, index + 1).join("/"))}
          >
            {part}
          </button>
        </span>
      ))}
    </div>
  );
}

function EntryTable({
  entries,
  onOpenDir,
  onDownload,
  onCopy,
  emptyHint,
}: {
  entries: StorageEntry[];
  onOpenDir: (name: string) => void;
  onDownload?: (entry: StorageEntry) => void;
  onCopy?: (entry: StorageEntry) => void;
  emptyHint: string;
}) {
  if (!entries.length) {
    return <div className="empty m-3">{emptyHint}</div>;
  }
  return (
    <div className="scroll-thin max-h-[380px] overflow-y-auto">
      <table className="w-full border-collapse">
        <thead className="sticky top-0 bg-ink-850/95 backdrop-blur">
          <tr className="border-b border-ink-700/60">
            <th className="th">Name</th>
            <th className="th">Size</th>
            <th className="th">Modified</th>
            <th className="th"></th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.name} className="row-hover border-b border-ink-700/40 last:border-0">
              <td className="td">
                <button
                  className="flex items-center gap-2 text-left"
                  onClick={() => (entry.is_dir ? onOpenDir(entry.name) : onCopy?.(entry))}
                >
                  {entry.is_dir ? (
                    <Folder size={14} className="text-cyan-400/80" />
                  ) : (
                    <File size={14} className="text-slate-500" />
                  )}
                  <span className="max-w-[260px] truncate font-mono text-xs text-slate-200">{entry.name}</span>
                </button>
              </td>
              <td className="td font-mono text-[11px] text-slate-500">{entry.is_dir ? "—" : fmtBytes(entry.size)}</td>
              <td className="td text-[11px] text-slate-500">{timeAgo(entry.mtime)}</td>
              <td className="td text-right">
                {!entry.is_dir && onDownload && (
                  <button className="btn-ghost btn-xs" onClick={() => onDownload(entry)} title="Download to this node">
                    <Download size={12} />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function dirJoin(base: string, name: string) {
  return base ? `${base}/${name}` : name;
}

export function StoragePage() {
  const { me, toast, refresh, refreshToken } = useApp();
  const [tab, setTab] = useState<Tab>("shares");
  const sharesQ = usePolling(() => get<{ shares: Share[] }>("/api/storage/shares"), 8000);
  const volumesQ = usePolling(() => get<{ volumes: Volume[] }>("/api/storage/volumes"), 30000);
  const transfersQ = usePolling(() => get<{ transfers: Transfer[] }>("/api/storage/transfers?limit=100"), 2500);
  const clusterQ = usePolling(() => get<Cluster>("/api/cluster"), 8000);
  const settingsQ = usePolling(() => get<SettingsPayload>("/api/settings"), 30000);

  const canWrite = me?.role === "admin";
  const shares = sharesQ.data?.shares ?? [];
  const volumes = volumesQ.data?.volumes ?? [];
  const transfers = transfersQ.data?.transfers ?? [];
  const peers = useMemo(
    () => (clusterQ.data?.peers ?? []).filter((peer) => peer.status === "online"),
    [clusterQ.data?.peers],
  );
  const storagePeers = peers.filter((peer) => peer.remote_accepts_storage);
  const dataDir = settingsQ.data?.data_dir;

  const [shareModal, setShareModal] = useState(false);
  const [shareForm, setShareForm] = useState({ name: "", path: "", read_only: false, allow_delete: false, confirm_root: false });
  const [shareError, setShareError] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<Share | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [localShare, setLocalShare] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [localEntries, setLocalEntries] = useState<StorageEntry[]>([]);
  const [localError, setLocalError] = useState<string | null>(null);
  const [localLoading, setLocalLoading] = useState(false);

  const [remoteNode, setRemoteNode] = useState("");
  const [remoteShares, setRemoteShares] = useState<Share[]>([]);
  const [remoteShare, setRemoteShare] = useState("");
  const [remotePath, setRemotePath] = useState("");
  const [remoteEntries, setRemoteEntries] = useState<StorageEntry[]>([]);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const [remoteLoading, setRemoteLoading] = useState(false);

  const [upload, setUpload] = useState({ path: "", local_path: "", overwrite: false });
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadBusy, setUploadBusy] = useState(false);

  const [smbHost, setSmbHost] = useState("");
  const [smbScripts, setSmbScripts] = useState<{ windows: string; debian: string; windows_mount: string | null; debian_mount: string | null; warning: string } | null>(null);
  const [smbTab, setSmbTab] = useState<"windows" | "debian" | "windows_mount" | "debian_mount">("windows");
  const [smbBusy, setSmbBusy] = useState(false);

  useEffect(() => {
    if (!localShare) {
      setLocalEntries([]);
      return;
    }
    let cancelled = false;
    setLocalLoading(true);
    get<EntryResponse>(`/api/storage/browse?share_id=${encodeURIComponent(localShare)}&path=${encodeURIComponent(localPath)}`)
      .then((data) => {
        if (!cancelled) {
          setLocalEntries(data.entries);
          setLocalError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setLocalError(err instanceof Error ? err.message : "browse failed");
      })
      .finally(() => {
        if (!cancelled) setLocalLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [localShare, localPath, refreshToken]);

  useEffect(() => {
    if (!remoteNode) {
      setRemoteShares([]);
      setRemoteShare("");
      setRemoteEntries([]);
      return;
    }
    let cancelled = false;
    setRemoteLoading(true);
    get<{ shares: Share[] }>(`/api/storage/remote/${remoteNode}/shares`)
      .then((data) => {
        if (cancelled) return;
        setRemoteShares(data.shares);
        setRemoteError(null);
        if (!data.shares.some((share) => share.id === remoteShare)) {
          setRemoteShare(data.shares[0]?.id ?? "");
          setRemotePath("");
        }
      })
      .catch((err) => {
        if (!cancelled) setRemoteError(err instanceof Error ? err.message : "remote shares unavailable");
      })
      .finally(() => {
        if (!cancelled) setRemoteLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // remoteShare is intentionally excluded: this refreshes the share list only.
  }, [remoteNode]);

  useEffect(() => {
    if (!remoteNode || !remoteShare) {
      setRemoteEntries([]);
      return;
    }
    let cancelled = false;
    setRemoteLoading(true);
    get<EntryResponse>(
      `/api/storage/remote/${remoteNode}/list?share_id=${encodeURIComponent(remoteShare)}&path=${encodeURIComponent(remotePath)}`,
    )
      .then((data) => {
        if (!cancelled) {
          setRemoteEntries(data.entries);
          setRemoteError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setRemoteError(err instanceof Error ? err.message : "remote browse failed");
      })
      .finally(() => {
        if (!cancelled) setRemoteLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [remoteNode, remoteShare, remotePath, refreshToken]);

  const addShare = async () => {
    setShareError(null);
    setBusy("share");
    try {
      await post("/api/storage/shares", {
        ...shareForm,
        name: shareForm.name.trim(),
        path: shareForm.path.trim(),
      });
      setShareModal(false);
      setShareForm({ name: "", path: "", read_only: false, allow_delete: false, confirm_root: false });
      sharesQ.reload();
      toast("Share approved", "ok");
      refresh();
    } catch (err) {
      setShareError(err instanceof Error ? err.message : "could not add share");
    } finally {
      setBusy(null);
    }
  };

  const updateShare = async (share: Share, fields: { read_only?: boolean; allow_delete?: boolean }) => {
    setBusy(share.id);
    try {
      await patch(`/api/storage/shares/${share.id}`, fields);
      sharesQ.reload();
      toast(`Updated ${share.name}`, "ok");
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "update failed", "error");
    } finally {
      setBusy(null);
    }
  };

  const removeShare = async () => {
    if (!removeTarget) return;
    setBusy(removeTarget.id);
    try {
      await del(`/api/storage/shares/${removeTarget.id}`);
      toast(`Removed ${removeTarget.name}`, "info");
      setRemoveTarget(null);
      sharesQ.reload();
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not remove share", "error");
    } finally {
      setBusy(null);
    }
  };

  const download = async (entry: StorageEntry) => {
    if (!remoteNode) return;
    setRemoteLoading(true);
    try {
      await post("/api/storage/download", { node_id: remoteNode, share_id: remoteShare, path: dirJoin(remotePath, entry.name) });
      toast(`Downloading ${entry.name}`, "ok");
      transfersQ.reload();
      setTab("transfers");
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "download could not start", "error");
    } finally {
      setRemoteLoading(false);
    }
  };

  const startUpload = async () => {
    setUploadError(null);
    if (!remoteNode || !remoteShare || !upload.local_path.trim() || !upload.path.trim()) {
      setUploadError("Choose a remote peer and share, then fill both paths.");
      return;
    }
    setUploadBusy(true);
    try {
      await post("/api/storage/upload", {
        node_id: remoteNode,
        share_id: remoteShare,
        path: upload.path.trim(),
        local_path: upload.local_path.trim(),
        overwrite: upload.overwrite,
      });
      toast("Upload started", "ok");
      setTab("transfers");
      transfersQ.reload();
      refresh();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "upload could not start");
    } finally {
      setUploadBusy(false);
    }
  };

  const cancelTransfer = async (transfer: Transfer) => {
    try {
      await post(`/api/storage/transfers/${transfer.id}/cancel`);
      toast("Cancellation requested", "ok");
      transfersQ.reload();
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "cancel failed", "error");
    }
  };

  const generateSmb = async () => {
    setSmbBusy(true);
    try {
      const result = await post<{ windows: string; debian: string; windows_mount: string | null; debian_mount: string | null; warning: string }>(
        "/api/storage/smb/scripts",
        { remote_host: smbHost.trim() },
      );
      setSmbScripts(result);
      setSmbTab("windows");
      toast("Helper scripts generated — review before running", "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not generate scripts", "error");
    } finally {
      setSmbBusy(false);
    }
  };

  const downloadScript = (name: string, content: string) => {
    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
  };

  const smbContent = smbScripts && smbTab ? smbScripts[smbTab] : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1 border-b border-ink-700/60 pb-1">
        {(
          [
            ["shares", "Approved shares"],
            ["browse", "Browse & transfer"],
            ["transfers", "Transfers"],
            ["smb", "SMB helpers"],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-t-lg px-3 py-2 text-xs font-medium transition ${
              tab === key ? "border border-b-0 border-ink-700/60 bg-ink-800/60 text-cyan-200" : "text-slate-500 hover:text-slate-200"
            }`}
          >
            {label}
            {key === "transfers" && transfers.some((transfer) => transfer.status === "running") && (
              <span className="ml-1.5 inline-block h-1.5 w-1.5 animate-pulseSoft rounded-full bg-cyan-400" />
            )}
          </button>
        ))}
      </div>

      {tab === "shares" && (
        <div className="space-y-4">
          <Panel
            title="Approved shares on this node"
            subtitle="Nothing is exposed implicitly. Each share has explicit read-only and delete permissions."
            actions={
              <button className="btn-primary btn-xs" onClick={() => setShareModal(true)} disabled={!canWrite}>
                <Plus size={12} /> Approve share
              </button>
            }
            bodyClassName="p-0"
          >
            {shares.length ? (
              <div className="divide-y divide-ink-700/40">
                {shares.map((share) => (
                  <div key={share.id} className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-3">
                      <FolderOpen size={16} className={share.exists ? "text-cyan-400/80" : "text-rose-400/80"} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-sm font-medium text-slate-100">{share.name}</span>
                          {share.read_only && <span className="badge-idle">read-only</span>}
                          {share.allow_delete && <span className="badge-warn">delete enabled</span>}
                          {!share.exists && <span className="badge-bad">path missing</span>}
                        </div>
                        <div className="truncate font-mono text-[10px] text-slate-500">{share.path}</div>
                      </div>
                      <div className="w-40">
                        {share.usage ? (
                          <>
                            <UsageBar percent={share.usage.percent} />
                            <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-600">
                              <span>{fmtBytes(share.usage.free)} free</span>
                              <span>{fmtBytes(share.usage.total)}</span>
                            </div>
                          </>
                        ) : (
                          <span className="text-[10px] text-slate-600">usage unavailable</span>
                        )}
                      </div>
                      <button className="btn-ghost btn-xs" onClick={() => setRemoveTarget(share)} disabled={!canWrite}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                    <div className="mt-3 grid gap-2 sm:grid-cols-2">
                      <Toggle
                        label="Read-only"
                        hint="Remote peers cannot upload or delete inside this share."
                        checked={share.read_only}
                        disabled={!canWrite || busy === share.id}
                        onChange={(value) => void updateShare(share, { read_only: value, allow_delete: value ? false : share.allow_delete })}
                      />
                      <Toggle
                        label="Allow delete"
                        hint="Required for remote deletion; ignored while the share is read-only."
                        checked={share.allow_delete}
                        disabled={!canWrite || share.read_only || busy === share.id}
                        onChange={(value) => void updateShare(share, { allow_delete: value })}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-4">
                <Empty icon={HardDrive} title="No shares approved" hint="Approving a directory enables remote browsing and transfers. C:\\ and / are never shared implicitly." />
              </div>
            )}
          </Panel>

          <Panel title="Detected volumes" subtitle="Mount points reported by the OS — useful when choosing a share root." bodyClassName="p-0">
            {volumes.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse">
                  <thead>
                    <tr className="border-b border-ink-700/60">
                      <th className="th">Mount</th>
                      <th className="th">Device</th>
                      <th className="th">Free</th>
                      <th className="th">Usage</th>
                      <th className="th"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {volumes.map((volume) => (
                      <tr key={volume.mountpoint} className="border-b border-ink-700/40 last:border-0">
                        <td className="td">
                          <div className="flex items-center gap-1.5 font-mono text-xs text-slate-300">
                            <HardDrive size={12} className="text-slate-600" /> {volume.mountpoint}
                            {volume.removable && <span className="badge-idle">removable</span>}
                          </div>
                          <div className="font-mono text-[10px] text-slate-600">{volume.fstype}</div>
                        </td>
                        <td className="td font-mono text-[10px] text-slate-500">{volume.device}</td>
                        <td className="td font-mono text-[11px] text-slate-400">{fmtBytes(volume.free)}</td>
                        <td className="td w-36"><UsageBar percent={volume.percent} /></td>
                        <td className="td text-right">
                          <button
                            className="btn-ghost btn-xs"
                            onClick={() =>
                              setShareForm((current) => ({ ...current, path: volume.mountpoint, name: current.name || volume.mountpoint }))
                            }
                          >
                            Use
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-4"><Spinner label="Reading volumes…" /></div>
            )}
          </Panel>
        </div>
      )}

      {tab === "browse" && (
        <div className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="This node" subtitle="Server-side view of approved shares">
              <div className="space-y-3">
                <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                  <select className="select" value={localShare} onChange={(event) => { setLocalShare(event.target.value); setLocalPath(""); }}>
                    <option value="">Choose a share…</option>
                    {shares.map((share) => (
                      <option key={share.id} value={share.id}>{share.name} — {share.path}</option>
                    ))}
                  </select>
                  <button className="btn-ghost btn-xs" onClick={() => setLocalPath("")} disabled={!localShare}>Root</button>
                </div>
                {localShare && <Breadcrumbs path={localPath} onNavigate={setLocalPath} />}
                {localError && <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{localError}</div>}
                {localLoading ? (
                  <Spinner label="Listing…" />
                ) : (
                  <EntryTable
                    entries={localEntries}
                    onOpenDir={(name) => setLocalPath((current) => dirJoin(current, name))}
                    onCopy={(entry) => {
                      void navigator.clipboard.writeText(dirJoin(localPath, entry.name)).catch(() => undefined);
                      toast("Relative path copied", "info");
                    }}
                    emptyHint={localShare ? "This folder is empty." : "Select an approved share to browse it."}
                  />
                )}
                <p className="text-[10px] leading-relaxed text-slate-600">
                  Local file actions stay in the OS. Use the remote panel to transfer files between nodes with chunking, resume and
                  SHA-256 verification.
                </p>
              </div>
            </Panel>

            <Panel title="Remote peer" subtitle="Goes through the authenticated peer API, not SMB">
              <div className="space-y-3">
                {storagePeers.length ? (
                  <>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <select className="select" value={remoteNode} onChange={(event) => { setRemoteNode(event.target.value); setRemotePath(""); }}>
                        <option value="">Choose a peer…</option>
                        {storagePeers.map((peer) => (
                          <option key={peer.node_id} value={peer.node_id}>{peer.name}</option>
                        ))}
                      </select>
                      <select
                        className="select"
                        value={remoteShare}
                        onChange={(event) => { setRemoteShare(event.target.value); setRemotePath(""); }}
                        disabled={!remoteNode}
                      >
                        <option value="">Choose a remote share…</option>
                        {remoteShares.map((share) => (
                          <option key={share.id} value={share.id}>{share.name}{share.read_only ? " (read-only)" : ""}</option>
                        ))}
                      </select>
                    </div>
                    {remoteShare && <Breadcrumbs path={remotePath} onNavigate={setRemotePath} />}
                    {remoteError && <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{remoteError}</div>}
                    {remoteLoading ? (
                      <Spinner label="Contacting peer…" />
                    ) : (
                      <EntryTable
                        entries={remoteEntries}
                        onOpenDir={(name) => setRemotePath((current) => dirJoin(current, name))}
                        onDownload={(entry) => void download(entry)}
                        emptyHint={remoteNode ? "This folder is empty or unavailable." : "Select an online peer that grants storage access."}
                      />
                    )}
                    <p className="text-[10px] leading-relaxed text-slate-600">
                      Downloads land under <span className="font-mono text-slate-500">{dataDir ?? "<data dir>"}/downloads/&lt;node&gt;/&lt;share&gt;/…</span>{" "}
                      on this machine. Partial files use a .glpart suffix and resume automatically.
                    </p>
                  </>
                ) : (
                  <Empty
                    icon={Server}
                    title="No peer grants storage"
                    hint="A paired node must enable “Use my shares” for this node in Devices. SMB scripts are an optional OS-level alternative."
                  />
                )}

                {storagePeers.length > 0 && (
                  <div className="border-t border-ink-700/60 pt-3">
                    <div className="label mb-2">Upload a local file into the selected remote share</div>
                    {remoteNode && remoteShare ? (
                      (() => {
                        const targetShare = remoteShares.find((share) => share.id === remoteShare);
                        if (targetShare?.read_only) {
                          return <p className="text-xs text-amber-300/90">The selected remote share is read-only; choose a writable share to upload.</p>;
                        }
                        return (
                          <div className="grid gap-2">
                            <p className="text-[11px] text-slate-500">
                              Destination: <span className="font-mono text-slate-300">{targetShare?.name ?? remoteShare}</span> on{" "}
                              <span className="text-slate-300">{storagePeers.find((peer) => peer.node_id === remoteNode)?.name}</span>
                            </p>
                            <input
                              className="input font-mono"
                              placeholder="Remote path (e.g. incoming/archive.zip)"
                              value={upload.path}
                              onChange={(event) => setUpload((current) => ({ ...current, path: event.target.value }))}
                            />
                            <input
                              className="input font-mono"
                              placeholder="Local file path on this node"
                              value={upload.local_path}
                              onChange={(event) => setUpload((current) => ({ ...current, local_path: event.target.value }))}
                            />
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <label className="flex items-center gap-2 text-[11px] text-slate-500">
                                <input
                                  type="checkbox"
                                  className="h-3.5 w-3.5 rounded border-ink-600 bg-ink-950"
                                  checked={upload.overwrite}
                                  onChange={(event) => setUpload((current) => ({ ...current, overwrite: event.target.checked }))}
                                />
                                Overwrite remote file if it exists
                              </label>
                              <button className="btn-primary btn-xs" onClick={() => void startUpload()} disabled={uploadBusy}>
                                {uploadBusy ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />} Upload
                              </button>
                            </div>
                            {uploadError && <div className="text-xs text-rose-300">{uploadError}</div>}
                          </div>
                        );
                      })()
                    ) : (
                      <p className="text-[11px] text-slate-600">Select a peer and a writable share above to enable uploads.</p>
                    )}
                  </div>
                )}
              </div>
            </Panel>
          </div>
        </div>
      )}

      {tab === "transfers" && (
        <Panel
          title="Transfer queue"
          subtitle="4 MiB chunks, resumable partials, SHA-256 verified end to end."
          actions={
            <button className="btn-ghost btn-xs" onClick={() => transfersQ.reload()}>
              <RefreshCw size={12} /> Refresh
            </button>
          }
          bodyClassName="p-0"
        >
          {transfers.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] border-collapse">
                <thead>
                  <tr className="border-b border-ink-700/60">
                    <th className="th">Direction</th>
                    <th className="th">Peer</th>
                    <th className="th">Path</th>
                    <th className="th">Progress</th>
                    <th className="th">Speed</th>
                    <th className="th">Status</th>
                    <th className="th">Started</th>
                    <th className="th"></th>
                  </tr>
                </thead>
                <tbody>
                  {transfers.map((transfer) => (
                    <tr key={transfer.id} className="border-b border-ink-700/40 last:border-0">
                      <td className="td">
                        <span className="inline-flex items-center gap-1.5 text-xs text-slate-300">
                          {transfer.direction === "download" ? <ArrowDown size={13} className="text-cyan-400" /> : <ArrowUp size={13} className="text-blue-400" />}
                          {transfer.direction}
                        </span>
                      </td>
                      <td className="td text-xs text-slate-300">{transfer.peer_name || transfer.peer_node}</td>
                      <td className="td">
                        <div className="max-w-[240px] truncate font-mono text-[11px] text-slate-400">{transfer.rel_path}</div>
                        <div className="font-mono text-[10px] text-slate-600">{transfer.human_transferred} / {transfer.human_size}</div>
                      </td>
                      <td className="td w-40">
                        <UsageBar percent={transfer.size ? (transfer.transferred / transfer.size) * 100 : 0} tone="cyan" />
                        <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-600">
                          <span>{transfer.size ? Math.round((transfer.transferred / transfer.size) * 100) : 0}%</span>
                          {transfer.sha256 && <CopyText value={transfer.sha256} className="max-w-[70px]" />}
                        </div>
                      </td>
                      <td className="td font-mono text-[11px] text-slate-400">{transfer.status === "running" ? fmtRate(transfer.speed_bps) : "—"}</td>
                      <td className="td">
                        <span className={transfer.status === "completed" ? "badge-ok" : transfer.status === "failed" ? "badge-bad" : transfer.status === "running" ? "badge-warn" : "badge-idle"}>
                          <StatusDot state={transfer.status} /> {transfer.status}
                        </span>
                        {transfer.error && <div className="mt-1 max-w-[180px] truncate text-[10px] text-rose-300">{transfer.error}</div>}
                      </td>
                      <td className="td text-[11px] text-slate-500">{timeAgo(transfer.started_at)}</td>
                      <td className="td text-right">
                        {(transfer.status === "running" || transfer.status === "cancelling") && (
                          <button className="btn-ghost btn-xs" onClick={() => void cancelTransfer(transfer)} disabled={!canWrite}>
                            <X size={12} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-4">
              <Empty icon={ArrowDown} title="No transfers yet" hint="Start a download or upload from the Browse tab. Interrupted transfers resume from the last confirmed chunk." />
            </div>
          )}
        </Panel>
      )}

      {tab === "smb" && (
        <div className="space-y-4">
          <Panel title="Optional SMB / Samba helpers" subtitle="GOLEM LINK transfers do not need SMB. These scripts are generated for review only — nothing is executed.">
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Remote LAN address (for mount scripts)">
                <input className="input w-64 font-mono" placeholder="192.168.1.42" value={smbHost} onChange={(event) => setSmbHost(event.target.value)} />
              </Field>
              <button className="btn-primary btn-xs" onClick={() => void generateSmb()} disabled={smbBusy || !shares.length}>
                {smbBusy ? <Loader2 size={12} className="animate-spin" /> : <TerminalSquare size={12} />} Generate scripts
              </button>
            </div>
            {!shares.length && <p className="mt-2 text-xs text-amber-300/90">Approve at least one share first.</p>}
            {smbScripts && (
              <>
                <div className="mt-3 flex flex-wrap items-center gap-1">
                  {(
                    [
                      ["windows", "Windows share script"],
                      ["debian", "Debian Samba setup"],
                      ["windows_mount", "Windows mount"],
                      ["debian_mount", "Debian mount"],
                    ] as [typeof smbTab, string][]
                  ).map(([key, label]) => {
                    const available = key === "windows" || key === "debian" || !!smbScripts[key];
                    return (
                      <button
                        key={key}
                        disabled={!available}
                        onClick={() => setSmbTab(key)}
                        className={`rounded-lg px-2.5 py-1.5 text-[11px] transition ${
                          smbTab === key ? "bg-cyan-400/15 text-cyan-200" : "text-slate-500 hover:bg-ink-800 hover:text-slate-300"
                        } ${available ? "" : "cursor-not-allowed opacity-40"}`}
                      >
                        {label}
                      </button>
                    );
                  })}
                  <button
                    className="btn-ghost btn-xs ml-auto"
                    disabled={!smbContent}
                    onClick={() => downloadScript(`${smbTab}.txt`, smbContent ?? "")}
                  >
                    <Download size={12} /> Save
                  </button>
                  <CopyText value={smbContent ?? ""} />
                </div>
                <div className="mt-2 rounded-xl border border-amber-400/25 bg-amber-400/[0.06] px-3 py-2 text-[11px] leading-relaxed text-amber-100/80">
                  <ShieldAlert size={12} className="mr-1 inline" /> {smbScripts.warning}
                </div>
                <pre className="scroll-thin mt-3 max-h-80 overflow-auto rounded-xl border border-ink-700/60 bg-ink-950/80 p-4 font-mono text-[10px] leading-relaxed text-slate-400">
                  {smbContent ?? "—"}
                </pre>
              </>
            )}
          </Panel>
        </div>
      )}

      <Modal
        open={shareModal}
        title="Approve a directory as a share"
        subtitle="Only this directory (and its children) becomes reachable to authorized peers."
        onClose={() => setShareModal(false)}
        width="max-w-xl"
        footer={
          <>
            <button className="btn-ghost btn-xs" onClick={() => setShareModal(false)}>Cancel</button>
            <button className="btn-primary btn-xs" onClick={() => void addShare()} disabled={busy === "share" || !shareForm.path.trim()}>
              {busy === "share" ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />} Approve share
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Label">
            <input className="input" value={shareForm.name} onChange={(event) => setShareForm((current) => ({ ...current, name: event.target.value }))} placeholder="Projects" />
          </Field>
          <Field label="Absolute directory path" hint="Must already exist on this node.">
            <input className="input font-mono" value={shareForm.path} onChange={(event) => setShareForm((current) => ({ ...current, path: event.target.value }))} placeholder="D:\\Projects" />
          </Field>
          <div className="grid gap-2 sm:grid-cols-2">
            <Toggle label="Read-only" checked={shareForm.read_only} onChange={(value) => setShareForm((current) => ({ ...current, read_only: value, allow_delete: value ? false : current.allow_delete }))} />
            <Toggle label="Allow delete" checked={shareForm.allow_delete} disabled={shareForm.read_only} onChange={(value) => setShareForm((current) => ({ ...current, allow_delete: value }))} />
          </div>
          <label className="flex items-start gap-2 rounded-lg border border-rose-400/20 bg-rose-500/[0.06] px-3 py-2 text-[11px] text-rose-200/90">
            <input
              type="checkbox"
              className="mt-0.5 h-3.5 w-3.5 rounded border-ink-600 bg-ink-950"
              checked={shareForm.confirm_root}
              onChange={(event) => setShareForm((current) => ({ ...current, confirm_root: event.target.checked }))}
            />
            I explicitly confirm this is a system root or entire drive. The agent refuses these by default.
          </label>
          {shareError && <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{shareError}</div>}
        </div>
      </Modal>

      <Modal
        open={removeTarget !== null}
        title="Remove share"
        subtitle={removeTarget?.name}
        onClose={() => setRemoveTarget(null)}
        width="max-w-md"
        footer={
          <>
            <button className="btn-ghost btn-xs" onClick={() => setRemoveTarget(null)}>Cancel</button>
            <button className="btn-danger btn-xs" onClick={() => void removeShare()} disabled={busy === removeTarget?.id}>
              Remove share
            </button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-slate-400">
          Removing a share revokes peer access immediately. Files on disk are untouched. In-flight transfers that target this share
          will fail.
        </p>
      </Modal>
    </div>
  );
}
