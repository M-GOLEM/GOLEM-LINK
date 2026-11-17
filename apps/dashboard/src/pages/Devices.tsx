import {
  Activity,
  ArrowRight,
  Cpu,
  FileText,
  HardDrive,
  KeyRound,
  Laptop,
  MemoryStick,
  MonitorSmartphone,
  Network,
  Power,
  Radar,
  RefreshCw,
  ShieldCheck,
  Thermometer,
  Trash2,
  Undo2,
  Wifi,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  del,
  fmtBytes,
  fmtDuration,
  fmtPct,
  get,
  patch,
  post,
  shortId,
  timeAgo,
  type DevicePayload,
  type NodeDiagnostics,
  type Peer,
} from "../api";
import type { PageKey } from "../components/Shell";
import { maxTemp } from "../components/NodeCard";
import { CopyText, Empty, Modal, Panel, Spinner, StatusDot, Toggle, UsageBar } from "../components/ui";
import { usePolling } from "../hooks";
import { useApp } from "../store";

type PermissionKey = "allow_tasks" | "allow_storage" | "allow_updates" | "allow_control" | "auto_connect";

function PermissionRow({
  peer,
  busy,
  onChange,
}: {
  peer: Peer;
  busy: string | null;
  onChange: (key: PermissionKey, value: boolean) => void;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
      <Toggle
        label="Accept tasks from this device"
        hint={`${peer.name} may send work that runs on this node.`}
        checked={peer.allow_tasks}
        disabled={busy === peer.node_id}
        onChange={(value) => onChange("allow_tasks", value)}
      />
      <Toggle
        label="Share files with this device"
        hint={`${peer.name} may browse and transfer files in approved shares.`}
        checked={peer.allow_storage}
        disabled={busy === peer.node_id}
        onChange={(value) => onChange("allow_storage", value)}
      />
      <Toggle
        label="Let this device pull updates"
        hint={`${peer.name} may read this node's release manifest and download its verified installer.`}
        checked={peer.allow_updates}
        disabled={busy === peer.node_id}
        onChange={(value) => onChange("allow_updates", value)}
      />
      <Toggle
        label="Allow remote maintenance"
        hint={`${peer.name} may ask this node to check for updates, install a staged build or restart. Off by default.`}
        checked={peer.allow_control}
        disabled={busy === peer.node_id}
        onChange={(value) => onChange("allow_control", value)}
      />
      <Toggle
        label="Auto reconnect"
        hint="Keep retrying the peer connection in the background."
        checked={peer.auto_connect}
        disabled={busy === peer.node_id}
        onChange={(value) => onChange("auto_connect", value)}
      />
    </div>
  );
}

type OpsTab = "overview" | "logs" | "actions";

function RemoteOps({
  peer,
  open,
  onClose,
}: {
  peer: Peer;
  open: boolean;
  onClose: () => void;
}) {
  const { toast } = useApp();
  const [tab, setTab] = useState<OpsTab>("overview");
  const [diag, setDiag] = useState<NodeDiagnostics | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [diagError, setDiagError] = useState<string | null>(null);
  const [logsError, setLogsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const explain = (err: unknown, fallback: string) => {
    const raw = err instanceof Error ? err.message : fallback;
    if (raw.includes("404")) {
      return `${peer.name} still runs an older GOLEM LINK, so it has no remote operations yet. Update it, then reopen this panel.`;
    }
    if (raw.includes("403")) {
      return `${peer.name} has not granted remote maintenance to this node. Turn on "Allow remote maintenance" there.`;
    }
    return raw;
  };

  const loadDiagnostics = async () => {
    try {
      setDiag(await get<NodeDiagnostics>(`/api/devices/${peer.node_id}/diagnostics`));
      setDiagError(null);
    } catch (err) {
      setDiagError(explain(err, "diagnostics failed"));
    }
  };

  const loadLogs = async () => {
    try {
      const data = await get<{ lines: string[] }>(`/api/devices/${peer.node_id}/logs?lines=300`);
      setLines(data.lines);
      setLogsError(null);
    } catch (err) {
      setLogsError(explain(err, "logs failed"));
    }
  };

  useEffect(() => {
    if (!open) return;
    setDiag(null);
    setLines([]);
    setDiagError(null);
    setLogsError(null);
    void loadDiagnostics();
    void loadLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, peer.node_id]);

  const act = async (action: "restart" | "rollback") => {
    const question =
      action === "restart"
        ? `Restart the agent on ${peer.name}? The link drops for a few seconds and reconnects by itself.`
        : `Roll ${peer.name} back to the previous build? The service stops, the previous program folder is restored, and updates stay paused on that node until you turn them back on.`;
    if (!window.confirm(question)) return;
    setBusy(action);
    try {
      await post(`/api/devices/${peer.node_id}/control`, { action, confirm: true });
      toast(
        action === "restart"
          ? `${peer.name} is restarting`
          : `${peer.name} is restoring the previous build (updates paused)`,
        "ok",
      );
      setTab("overview");
      window.setTimeout(() => void loadDiagnostics(), 5000);
    } catch (err) {
      toast(err instanceof Error ? err.message : `${action} failed`, "error");
    } finally {
      setBusy(null);
    }
  };

  const tabs: { key: OpsTab; label: string; icon: typeof Activity }[] = [
    { key: "overview", label: "Overview", icon: Activity },
    { key: "logs", label: "Logs", icon: FileText },
    { key: "actions", label: "Actions", icon: Power },
  ];

  return (
    <Modal
      open={open}
      title={
        <div className="flex items-center gap-2">
          <Activity size={15} className="text-cyan-300" /> Remote operations
          <span className="badge-idle">{peer.name}</span>
        </div>
      }
      subtitle="Read-only diagnostics, the node's own log and the two confirmed maintenance actions."
      onClose={onClose}
      width="max-w-3xl"
      footer={
        <div className="flex justify-end">
          <button className="btn-ghost btn-xs" onClick={onClose}>
            Close
          </button>
        </div>
      }
    >
      <div className="mb-3 flex flex-wrap gap-1.5">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            className={tab === key ? "btn-primary btn-xs" : "btn-ghost btn-xs"}
            onClick={() => setTab(key)}
          >
            <Icon size={12} /> {label}
          </button>
        ))}
        <button
          className="btn-ghost btn-xs ml-auto"
          onClick={() => {
            void loadDiagnostics();
            void loadLogs();
          }}
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {(tab === "logs" ? logsError : diagError) && (
        <div className="mb-3 rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs leading-relaxed text-rose-200">
          {tab === "logs" ? logsError : diagError}
        </div>
      )}

      {tab === "overview" &&
        (diag ? (
          <div className="space-y-3">
            <div className="grid gap-2 sm:grid-cols-3">
              {[
                ["Version", diag.version],
                ["Service", `${diag.service.mode} · ${diag.service.state ?? "unknown"}`],
                ["Uptime", fmtDuration(diag.uptime_s)],
                ["Platform", `${diag.os}`],
                ["Hostname", diag.hostname],
                ["Python", diag.python],
              ].map(([label, value]) => (
                <div key={label} className="panel-quiet px-3 py-2">
                  <div className="label">{label}</div>
                  <div className="mt-1 truncate font-mono text-[11px] text-slate-300">{value}</div>
                </div>
              ))}
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              <div className="panel-quiet px-3 py-2">
                <div className="label">CPU</div>
                <div className="mt-1 font-mono text-sm text-slate-200">
                  {fmtPct(diag.cpu.percent ?? null, 1)}
                </div>
              </div>
              <div className="panel-quiet px-3 py-2">
                <div className="label">RAM</div>
                <div className="mt-1 font-mono text-sm text-slate-200">
                  {fmtPct(diag.ram.percent ?? null, 1)}
                </div>
              </div>
              <div className="panel-quiet px-3 py-2">
                <div className="label">Disk ({diag.disk.path ?? diag.data_dir})</div>
                <div className="mt-1 font-mono text-sm text-slate-200">
                  {diag.disk.free != null ? `${fmtBytes(diag.disk.free)} free` : "—"}
                  {diag.disk.used_percent != null ? ` · ${fmtPct(diag.disk.used_percent, 0)} used` : ""}
                </div>
              </div>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="panel-quiet px-3 py-2">
                <div className="label">Tasks</div>
                <div className="mt-1 font-mono text-[11px] text-slate-300">
                  {diag.tasks.running} running · {diag.tasks.queued} queued · {diag.tasks.failed} failed ·{" "}
                  {diag.tasks.completed} done
                </div>
              </div>
              <div className="panel-quiet px-3 py-2">
                <div className="label">Fleet</div>
                <div className="mt-1 font-mono text-[11px] text-slate-300">
                  {diag.peers.online}/{diag.peers.paired} peers online · db {fmtBytes(diag.db_bytes)} · logs{" "}
                  {fmtBytes(diag.logs_bytes)}
                </div>
              </div>
            </div>
            <div className="panel-quiet px-3 py-2">
              <div className="label">Updates</div>
              <div className="mt-1 text-[11px] leading-relaxed text-slate-300">
                state <span className="font-mono">{diag.update.state ?? "unknown"}</span> · running{" "}
                <span className="font-mono">{diag.update.current_version}</span>
                {diag.update.latest_version ? ` · newest ${diag.update.latest_version}` : ""} ·
                autopilot {diag.update.auto_install ? "on" : "off"} · remote install{" "}
                {diag.update.remote_install ? "yes" : "no"} · rollback{" "}
                {diag.update.rollback_supported ? "available" : "not available"}
                {diag.update.error ? <div className="mt-1 text-rose-300">{diag.update.error}</div> : null}
              </div>
            </div>
            {diag.units.length > 0 && (
              <div className="grid gap-2 sm:grid-cols-2">
                {diag.units.map((unit) => (
                  <div key={unit.unit} className="panel-quiet px-3 py-2">
                    <div className="label">{unit.unit}</div>
                    <div className="mt-1 font-mono text-[11px] text-slate-300">
                      {unit.active ?? "unknown"} · {unit.enabled ?? "unknown"}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : diagError ? null : (
          <div className="flex min-h-[20vh] items-center justify-center">
            <Spinner label="Reading diagnostics…" />
          </div>
        ))}

      {tab === "logs" && (
        <pre className="max-h-[50vh] overflow-auto rounded-lg border border-ink-700/60 bg-ink-950/80 p-3 font-mono text-[11px] leading-relaxed text-slate-300">
          {lines.length ? lines.join("\n") : "no log lines on that node yet"}
        </pre>
      )}

      {tab === "actions" && (
        <div className="space-y-3">
          <div className="rounded-lg border border-ink-700/60 bg-ink-900/50 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm text-slate-200">Restart the agent</div>
                <div className="mt-1 text-[11px] text-slate-500">
                  Windows restarts through the service wrapper. Linux exits cleanly and systemd
                  (Restart=always) starts it again. The link reconnects by itself.
                </div>
              </div>
              <button
                className="btn-primary btn-xs"
                disabled={busy !== null}
                onClick={() => void act("restart")}
              >
                <Power size={12} /> {busy === "restart" ? "Restarting…" : "Restart"}
              </button>
            </div>
          </div>
          <div className="rounded-lg border border-ink-700/60 bg-ink-900/50 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm text-slate-200">Roll back the last update</div>
                <div className="mt-1 text-[11px] text-slate-500">
                  Restores the program folder the installer kept and starts it with updates
                  paused, so autopilot cannot reinstall the release you just rolled back.
                </div>
              </div>
              <button
                className="btn-ghost btn-xs"
                disabled={busy !== null || !diag?.update.rollback_supported}
                onClick={() => void act("rollback")}
              >
                <Undo2 size={12} /> {busy === "rollback" ? "Scheduling…" : "Roll back"}
              </button>
            </div>
            {diag && !diag.update.rollback_supported && (
              <div className="mt-2 text-[11px] text-amber-300/90">
                No usable backup on that node. A rollback needs the program-folder backup the
                installer keeps, and on Linux the opt-in root helper.
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function PeerCard({
  peer,
  canWrite,
  onRemove,
}: {
  peer: Peer;
  canWrite: boolean;
  onRemove: (peer: Peer) => void;
}) {
  const { toast, refresh } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const [remoteOpen, setRemoteOpen] = useState(false);
  const online = peer.status === "online";
  const metrics = peer.metrics;
  const temp = maxTemp(metrics);
  const sharingBlocked = !(peer.allow_tasks && peer.remote_accepts_tasks);

  const update = async (key: PermissionKey, value: boolean) => {
    setBusy(peer.node_id);
    try {
      await patch(`/api/peers/${peer.node_id}/permissions`, { [key]: value });
      toast(`Permission updated for ${peer.name}`, "ok");
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "permission update failed", "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel
      title={
        <div className="flex items-center gap-2.5">
          <StatusDot state={online ? "online" : "offline"} />
          <span className="font-display text-sm font-semibold text-white">{peer.name}</span>
          <span className={online ? "badge-ok" : "badge-idle"}>{peer.status}</span>
        </div>
      }
      subtitle={`${peer.os || "unknown OS"} · ${peer.address}:${peer.peer_port} · seen ${timeAgo(peer.last_seen)}`}
      actions={
        <div className="flex flex-wrap gap-2">
          {peer.remote_allows_control && (
            <button className="btn-ghost btn-xs" onClick={() => setRemoteOpen(true)}>
              <Activity size={12} /> Remote
            </button>
          )}
          {canWrite && (
            <button className="btn-danger btn-xs" onClick={() => onRemove(peer)}>
              <Trash2 size={12} /> Remove
            </button>
          )}
        </div>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="panel-quiet px-3 py-2">
            <div className="label">CPU</div>
            <div className="mt-1 font-mono text-sm text-slate-200">{metrics ? fmtPct(metrics.cpu.percent) : "—"}</div>
          </div>
          <div className="panel-quiet px-3 py-2">
            <div className="label">RAM</div>
            <div className="mt-1 font-mono text-sm text-slate-200">
              {metrics ? `${fmtBytes(metrics.ram.used)} / ${fmtBytes(metrics.ram.total)}` : "—"}
            </div>
          </div>
          <div className="panel-quiet px-3 py-2">
            <div className="label">Temp</div>
            <div className="mt-1 font-mono text-sm text-slate-200">{temp === null ? "n/a" : `${temp.toFixed(0)} °C`}</div>
          </div>
          <div className="panel-quiet px-3 py-2">
            <div className="label">Latency</div>
            <div className="mt-1 font-mono text-sm text-slate-200">
              {peer.latency_ms != null ? `${peer.latency_ms.toFixed(0)} ms` : "—"}
            </div>
          </div>
        </div>

        {!online && peer.last_error && (
          <div className="rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200">
            Last connection error: {peer.last_error}
          </div>
        )}
        {online && (peer.reconnects ?? 0) > 0 && (
          <div className="text-[11px] text-slate-500">
            Link reconnected {peer.reconnects} time{peer.reconnects === 1 ? "" : "s"} since pairing.
          </div>
        )}

        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <div className="flex items-center gap-2 text-slate-500">
            <MonitorSmartphone size={12} /> host <span className="font-mono text-slate-300">{peer.hostname || "—"}</span>
          </div>
          <div className="flex items-center gap-2 text-slate-500">
            <Cpu size={12} /> <span className="truncate text-slate-300">{peer.cpu_model || "CPU unknown"}</span>
            <span className="font-mono text-slate-500">
              {peer.cores_logical ? `${peer.cores_logical} threads` : ""}
            </span>
          </div>
          <div className="flex items-center gap-2 text-slate-500">
            <KeyRound size={12} /> <CopyText value={peer.fingerprint} />
          </div>
          <div className="flex items-center gap-2 text-slate-500">
            <Wifi size={12} /> node <span className="font-mono text-slate-300">{shortId(peer.node_id)}</span>
            {peer.version && <span className="badge-idle">v{peer.version}</span>}
          </div>
        </div>

        {metrics && (
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <div className="flex items-center justify-between text-[11px] text-slate-500">
                <span className="inline-flex items-center gap-1"><Cpu size={11} /> CPU</span>
                <span className="font-mono">{fmtPct(metrics.cpu.percent, 1)}</span>
              </div>
              <UsageBar className="mt-1.5" percent={metrics.cpu.percent} />
            </div>
            <div>
              <div className="flex items-center justify-between text-[11px] text-slate-500">
                <span className="inline-flex items-center gap-1"><MemoryStick size={11} /> RAM</span>
                <span className="font-mono">{fmtPct(metrics.ram.percent, 1)}</span>
              </div>
              <UsageBar className="mt-1.5" percent={metrics.ram.percent} />
            </div>
            {metrics.disk.slice(0, 4).map((disk) => (
              <div key={disk.mountpoint}>
                <div className="flex items-center justify-between text-[11px] text-slate-500">
                  <span className="inline-flex min-w-0 items-center gap-1">
                    <HardDrive size={11} /> <span className="truncate">{disk.mountpoint}</span>
                  </span>
                  <span className="font-mono">{fmtPct(disk.percent, 0)} used</span>
                </div>
                <UsageBar className="mt-1.5" percent={disk.percent} />
              </div>
            ))}
            <div>
              <div className="label">Uptime</div>
              <div className="mt-1 font-mono text-xs text-slate-300">{fmtDuration(metrics.uptime_s)}</div>
            </div>
          </div>
        )}

        {peer.pressure?.blocked_reason && (
          <div className="rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200">
            Peer is refusing new work: {peer.pressure.blocked_reason}
          </div>
        )}

        <div className="rounded-xl border border-ink-700/60 bg-ink-900/50 p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div className="label">Permissions on this node</div>
            <div className="flex flex-wrap gap-1.5">
              <span className={peer.remote_accepts_tasks ? "badge-ok" : "badge-idle"}>
                {peer.remote_accepts_tasks ? `${peer.name} runs my tasks` : `${peer.name} runs no tasks`}
              </span>
              <span className={peer.remote_accepts_storage ? "badge-ok" : "badge-idle"}>
                {peer.remote_accepts_storage ? "file sharing granted" : "no file sharing"}
              </span>
              <span className={peer.remote_allows_updates ? "badge-ok" : "badge-idle"}>
                {peer.remote_allows_updates ? `${peer.name} serves updates` : "no update pull"}
              </span>
              <span className={peer.remote_allows_control ? "badge-ok" : "badge-idle"}>
                {peer.remote_allows_control ? "remote maintenance allowed" : "no remote control"}
              </span>
            </div>
          </div>
          {sharingBlocked ? (
            <div className="mb-2 rounded-lg border border-amber-400/25 bg-amber-400/[0.06] px-3 py-2 text-[11px] leading-relaxed text-amber-200/90">
              {!peer.allow_tasks && !peer.remote_accepts_tasks
                ? "Load sharing is off in both directions."
                : !peer.allow_tasks
                  ? `This node will not run ${peer.name}'s tasks. Turn on "Accept tasks from this device" below.`
                  : `${peer.name} will not run this node's tasks. Turn on "Accept tasks" for this node on that machine's Devices page.`}
            </div>
          ) : (
            <div className="mb-2 rounded-lg border border-emerald-400/20 bg-emerald-400/[0.05] px-3 py-2 text-[11px] text-emerald-200/90">
              Load sharing is active both ways: untargeted tasks may run on either node, whichever is less loaded.
            </div>
          )}
          <PermissionRow peer={peer} busy={busy} onChange={(key, value) => void update(key, value)} />
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[10px] text-slate-600">
          <ShieldCheck size={11} /> Certificate pinned · keys exchanged at pairing · permissions are enforced on the granting node.
        </div>

        <RemoteOps peer={peer} open={remoteOpen} onClose={() => setRemoteOpen(false)} />
      </div>
    </Panel>
  );
}

export function DevicesPage({ onNavigate }: { onNavigate: (page: PageKey) => void }) {
  const { me, toast, refresh } = useApp();
  const devicesQ = usePolling(() => get<DevicePayload>("/api/devices"), 4000);
  const [removeTarget, setRemoveTarget] = useState<Peer | null>(null);
  const [removing, setRemoving] = useState(false);
  const canWrite = me?.role === "admin";
  const devices = devicesQ.data;

  const confirmRemove = async () => {
    if (!removeTarget) return;
    setRemoving(true);
    try {
      await del(`/api/peers/${removeTarget.node_id}`);
      toast(`${removeTarget.name} removed; its stored secret and certificate were deleted`, "info");
      setRemoveTarget(null);
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not remove peer", "error");
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div className="space-y-4">
      {devicesQ.error && (
        <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          Cannot load devices: {devicesQ.error}
        </div>
      )}

      {!devices ? (
        <div className="flex min-h-[40vh] items-center justify-center"><Spinner label="Reading device state…" /></div>
      ) : (
        <>
          <Panel
            title="This computer"
            subtitle="Local agent facts. Metrics update live through the dashboard WebSocket."
          >
            <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <span className="grid h-12 w-12 place-items-center rounded-xl border border-cyan-400/30 bg-cyan-400/10 text-cyan-300">
                    <Laptop size={22} />
                  </span>
                  <div>
                    <div className="font-display text-lg font-semibold text-white">{devices.local.name}</div>
                    <div className="text-xs text-slate-500">
                      {devices.local.os} {devices.local.os_version} · {devices.local.hostname}
                    </div>
                  </div>
                  <span className="badge-ok ml-auto">online</span>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {[
                    ["Node ID", devices.local.node_id],
                    ["Agent version", `${devices.local.version} · Python ${devices.local.python ?? "?"}`],
                    ["CPU", devices.local.cpu_model || "unknown"],
                    ["Cores", `${devices.local.cpu_cores_physical} physical / ${devices.local.cpu_cores_logical} logical`],
                    ["Dashboard / peer port", `${devices.local.dashboard_port} / ${devices.local.peer_port}`],
                    ["Input role", devices.local.input_role],
                  ].map(([label, value]) => (
                    <div key={label} className="panel-quiet px-3 py-2">
                      <div className="label">{label}</div>
                      <div className="mt-1 truncate font-mono text-[11px] text-slate-300">{value}</div>
                    </div>
                  ))}
                </div>
                <div className="panel-quiet px-3 py-2">
                  <div className="label">LAN addresses</div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {devices.local.addresses.map((address) => (
                      <span key={address} className="badge-info font-mono">{address}</span>
                    ))}
                  </div>
                </div>
              </div>
              <div className="space-y-3">
                <div className="panel-quiet p-3">
                  <div className="flex items-center justify-between">
                    <div className="label">Fingerprint</div>
                    <CopyText value={devices.local.fingerprint} />
                  </div>
                  <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
                    Peers pin this certificate. Reinstalling the identity changes the fingerprint and requires re-pairing.
                  </p>
                </div>
                {devices.local.metrics && (
                  <div className="space-y-2.5">
                    <div>
                      <div className="flex items-center justify-between text-[11px] text-slate-500">
                        <span className="inline-flex items-center gap-1"><Cpu size={11} /> CPU</span>
                        <span className="font-mono">{fmtPct(devices.local.metrics.cpu.percent, 1)}</span>
                      </div>
                      <UsageBar className="mt-1.5" percent={devices.local.metrics.cpu.percent} />
                    </div>
                    <div>
                      <div className="flex items-center justify-between text-[11px] text-slate-500">
                        <span className="inline-flex items-center gap-1"><MemoryStick size={11} /> RAM</span>
                        <span className="font-mono">
                          {fmtBytes(devices.local.metrics.ram.used)} / {fmtBytes(devices.local.metrics.ram.total)}
                        </span>
                      </div>
                      <UsageBar className="mt-1.5" percent={devices.local.metrics.ram.percent} />
                    </div>
                    <div className="flex items-center justify-between text-[11px] text-slate-500">
                      <span className="inline-flex items-center gap-1"><Thermometer size={11} /> Hottest sensor</span>
                      <span className="font-mono text-slate-300">
                        {maxTemp(devices.local.metrics) === null ? "not reported" : `${maxTemp(devices.local.metrics)!.toFixed(0)} °C`}
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </Panel>

          <div className="flex flex-wrap items-center justify-between gap-2 px-1">
            <h2 className="section-title">Paired computers</h2>
            <span className="text-[11px] text-slate-500">
              {devices.peers.filter((peer) => peer.status === "online").length} of {devices.peers.length} online
            </span>
          </div>

          {devices.peers.length ? (
            <div className="grid gap-4 xl:grid-cols-2">
              {devices.peers.map((peer) => (
                <PeerCard key={peer.node_id} peer={peer} canWrite={canWrite} onRemove={setRemoveTarget} />
              ))}
            </div>
          ) : (
            <Panel>
              <Empty
                icon={MonitorSmartphone}
                title="No second computer paired"
                hint="Pair the other machine from the Network page. Pairing requires comparing a one-time 6-digit code on both screens."
              />
              <div className="mt-3 flex justify-center">
                <button className="btn-primary btn-xs" onClick={() => onNavigate("network")}>
                  Open pairing <ArrowRight size={12} />
                </button>
              </div>
            </Panel>
          )}

          <Panel
            title={
              <div className="section-title">
                <Radar size={13} className="text-cyan-400" /> Announced but not paired
              </div>
            }
            subtitle="Nodes discovered via mDNS or the UDP beacon that still require a pairing decision."
          >
            {devices.discovered.length ? (
              <div className="space-y-2">
                {devices.discovered.map((node) => (
                  <div key={node.node_id} className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5">
                    <StatusDot state={node.paired ? "online" : "idle"} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-slate-200">{node.name}</div>
                      <div className="truncate font-mono text-[10px] text-slate-500">
                        {node.address}:{node.port} · {node.os} · {node.source} · {timeAgo(node.last_seen)}
                      </div>
                    </div>
                    {node.paired ? (
                      <span className="badge-ok">paired</span>
                    ) : (
                      <button className="btn-ghost btn-xs" onClick={() => onNavigate("network")}>
                        <Network size={12} /> Pair
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-slate-500">Nothing announced right now. Discovery refreshes automatically every few seconds.</p>
            )}
          </Panel>
        </>
      )}

      <Modal
        open={removeTarget !== null}
        title="Remove paired device"
        subtitle={removeTarget ? `${removeTarget.name} · ${shortId(removeTarget.node_id)}` : undefined}
        onClose={() => setRemoveTarget(null)}
        width="max-w-md"
        footer={
          <>
            <button className="btn-ghost btn-xs" onClick={() => setRemoveTarget(null)}>Cancel</button>
            <button className="btn-danger btn-xs" onClick={() => void confirmRemove()} disabled={removing}>
              {removing ? "Removing…" : "Remove device"}
            </button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-slate-400">
          This deletes the stored pairing secret and pinned certificate for the peer. Task and transfer permissions disappear
          immediately; the two nodes would need to pair again to reconnect.
        </p>
        <p className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 text-[11px] text-amber-200/90">
          The other machine keeps its own record until you remove this node there too.
        </p>
      </Modal>
    </div>
  );
}
