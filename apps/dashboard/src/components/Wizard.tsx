import {
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Gauge,
  HardDrive,
  KeyRound,
  Laptop,
  LayoutGrid,
  ListChecks,
  Loader2,
  MonitorSmartphone,
  Network,
  Play,
  Plus,
  Power,
  ShieldCheck,
  TestTube2,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  del,
  fmtBytes,
  fmtPct,
  get,
  patch,
  post,
  type DevicePayload,
  type Diagnostics,
  type InputSharingStatus,
  type Metrics,
  type ScreenConfig,
  type Share,
  type SettingsPayload,
  type Task,
} from "../api";
import { useApp } from "../store";
import { PairingFlow } from "./Pairing";
import { Field, Toggle } from "./ui";

const STEPS = [
  { key: "node", label: "This node", icon: Laptop },
  { key: "pair", label: "Pairing", icon: KeyRound },
  { key: "layout", label: "Screen layout", icon: LayoutGrid },
  { key: "shares", label: "Storage", icon: HardDrive },
  { key: "policy", label: "Policy", icon: Gauge },
  { key: "startup", label: "Autostart", icon: Power },
  { key: "test", label: "Connection test", icon: TestTube2 },
  { key: "summary", label: "Summary", icon: CheckCircle2 },
] as const;

type StepKey = (typeof STEPS)[number]["key"];

interface CheckResult {
  name: string;
  status: "ok" | "warn" | "fail";
  detail: string;
}

function StepShell({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold text-white">{title}</h3>
        <p className="mt-1 max-w-2xl text-xs leading-relaxed text-slate-500">{hint}</p>
      </div>
      {children}
    </div>
  );
}

export function SetupWizard() {
  const { closeWizard, toast, refresh, me } = useApp();
  const [step, setStep] = useState(0);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [devices, setDevices] = useState<DevicePayload | null>(null);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [shares, setShares] = useState<Share[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [checks, setChecks] = useState<CheckResult[] | null>(null);
  const [testing, setTesting] = useState(false);
  const isAdmin = me?.role === "admin";

  const [policy, setPolicy] = useState({
    policy: "balanced",
    allow_remote_execution: true,
    max_concurrent_tasks: 2,
    reserve_cpu_percent: 15,
    reserve_ram_percent: 10,
    rebalance_on_overload: true,
    keep_host_light: true,
    keep_host_cpu_percent: 55,
    keep_host_ram_percent: 70,
  });
  const [thresholds, setThresholds] = useState({
    cpu_warn_percent: 80,
    cpu_crit_percent: 92,
    ram_warn_percent: 80,
    ram_crit_percent: 90,
    temp_warn_c: "",
    temp_crit_c: "",
    disk_free_warn_percent: 10,
    disk_free_crit_percent: 4,
  });
  const [autoStart, setAutoStart] = useState(true);
  const [layout, setLayout] = useState({
    enabled: false,
    tool: "deskflow",
    server_node_id: "" as string | null,
    screens: [] as { node_id: string; name: string; side: "left" | "right"; width: number; height: number }[],
    clipboard_sharing: true,
    auto_reconnect: true,
  });
  const [layoutStatus, setLayoutStatus] = useState<InputSharingStatus | null>(null);
  const [shareForm, setShareForm] = useState({ name: "", path: "", read_only: false, allow_delete: false, confirm_root: false });
  const [shareError, setShareError] = useState<string | null>(null);

  const load = async () => {
    try {
      const [diag, dev, cfg, shareList] = await Promise.all([
        get<Diagnostics>("/api/diagnostics"),
        get<DevicePayload>("/api/devices"),
        get<SettingsPayload>("/api/settings"),
        get<{ shares: Share[] }>("/api/storage/shares"),
      ]);
      setDiagnostics(diag);
      setDevices(dev);
      setSettings(cfg);
      setShares(shareList.shares);
      const sched = cfg.config.scheduling || {};
      setPolicy({
        policy: sched.policy ?? "balanced",
        allow_remote_execution: sched.allow_remote_execution ?? true,
        max_concurrent_tasks: sched.max_concurrent_tasks ?? 2,
        reserve_cpu_percent: sched.reserve_cpu_percent ?? 15,
        reserve_ram_percent: sched.reserve_ram_percent ?? 10,
        rebalance_on_overload: sched.rebalance_on_overload ?? true,
        keep_host_light: sched.keep_host_light ?? true,
        keep_host_cpu_percent: sched.keep_host_cpu_percent ?? 55,
        keep_host_ram_percent: sched.keep_host_ram_percent ?? 70,
      });
      const t = cfg.config.thresholds || {};
      setThresholds({
        cpu_warn_percent: t.cpu_warn_percent ?? 80,
        cpu_crit_percent: t.cpu_crit_percent ?? 92,
        ram_warn_percent: t.ram_warn_percent ?? 80,
        ram_crit_percent: t.ram_crit_percent ?? 90,
        temp_warn_c: t.temp_warn_c ?? "",
        temp_crit_c: t.temp_crit_c ?? "",
        disk_free_warn_percent: t.disk_free_warn_percent ?? 10,
        disk_free_crit_percent: t.disk_free_crit_percent ?? 4,
      });
      setAutoStart(cfg.config.auto_start ?? true);
      setLayoutStatus(cfg.input_sharing_status);
      if (cfg.input_sharing_status.screens.length) {
        setLayout({
          enabled: cfg.input_sharing_status.enabled,
          tool: cfg.input_sharing_status.tool,
          server_node_id: cfg.input_sharing_status.server_node_id,
          screens: cfg.input_sharing_status.screens,
          clipboard_sharing: true,
          auto_reconnect: true,
        });
      }
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "could not load node state");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  // Default screen layout: local on the left, first online peer on the right.
  useEffect(() => {
    if (!devices || layout.screens.length) return;
    const local = devices.local;
    const peer = devices.peers[0];
    const screens: ScreenConfig[] = [
      { node_id: local.node_id, name: local.name, side: "left", width: 1920, height: 1080 },
    ];
    if (peer) screens.push({ node_id: peer.node_id, name: peer.name, side: "right", width: 1920, height: 1080 });
    setLayout((current) => ({
      ...current,
      server_node_id: current.server_node_id || local.node_id,
      screens,
    }));
  }, [devices, layout.screens.length]);

  const savePolicy = async () => {
    setBusy("policy");
    try {
      await patch("/api/settings", {
        scheduling: {
          ...policy,
          max_concurrent_tasks: Number(policy.max_concurrent_tasks),
          reserve_cpu_percent: Number(policy.reserve_cpu_percent),
          reserve_ram_percent: Number(policy.reserve_ram_percent),
        },
        thresholds: {
          ...thresholds,
          temp_warn_c: thresholds.temp_warn_c === "" ? null : Number(thresholds.temp_warn_c),
          temp_crit_c: thresholds.temp_crit_c === "" ? null : Number(thresholds.temp_crit_c),
          disk_free_warn_percent: Number(thresholds.disk_free_warn_percent),
          disk_free_crit_percent: Number(thresholds.disk_free_crit_percent),
        },
      });
      toast("Scheduling and alert thresholds saved", "ok");
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not save policy", "error");
    } finally {
      setBusy(null);
    }
  };

  const saveAutoStart = async (value: boolean) => {
    setAutoStart(value);
    try {
      await patch("/api/settings", { auto_start: value });
      toast(`Autostart preference ${value ? "enabled" : "disabled"}`, "ok");
      refresh();
    } catch (err) {
      setAutoStart(!value);
      toast(err instanceof Error ? err.message : "could not save autostart", "error");
    }
  };

  const saveLayout = async () => {
    setBusy("layout");
    try {
      const result = await post<InputSharingStatus>("/api/input-sharing", layout);
      setLayoutStatus(result);
      toast("Input sharing layout saved", "ok");
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not save layout", "error");
    } finally {
      setBusy(null);
    }
  };

  const addShare = async () => {
    setShareError(null);
    setBusy("share");
    try {
      await post("/api/storage/shares", {
        ...shareForm,
        name: shareForm.name.trim(),
        path: shareForm.path.trim(),
      });
      setShareForm({ name: "", path: "", read_only: false, allow_delete: false, confirm_root: false });
      const result = await get<{ shares: Share[] }>("/api/storage/shares");
      setShares(result.shares);
      toast("Share approved", "ok");
      refresh();
    } catch (err) {
      setShareError(err instanceof Error ? err.message : "could not add share");
    } finally {
      setBusy(null);
    }
  };

  const removeShare = async (share: Share) => {
    try {
      await del(`/api/storage/shares/${share.id}`);
      setShares((current) => current.filter((item) => item.id !== share.id));
      toast(`Removed share ${share.name}`, "info");
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not remove share", "error");
    }
  };

  const runTests = async () => {
    setTesting(true);
    setChecks(null);
    const results: CheckResult[] = [];
    try {
      const latest = await get<{ metrics: Metrics | null }>("/api/metrics/latest");
      const fresh = latest.metrics && Date.now() / 1000 - latest.metrics.ts < 15;
      results.push({
        name: "Hardware telemetry",
        status: fresh ? "ok" : "fail",
        detail: latest.metrics
          ? `CPU ${fmtPct(latest.metrics.cpu.percent, 1)}, RAM ${fmtPct(latest.metrics.ram.percent, 1)}, sample age ${Math.max(0, Math.round(Date.now() / 1000 - latest.metrics.ts))}s`
          : "No metrics sample received yet",
      });

      const dev = await get<DevicePayload>("/api/devices");
      const online = dev.peers.filter((peer) => peer.status === "online");
      results.push({
        name: "Paired peers",
        status: online.length ? "ok" : "warn",
        detail: online.length
          ? `${online.map((peer) => `${peer.name}${peer.latency_ms != null ? ` (${peer.latency_ms.toFixed(0)} ms)` : ""}`).join(", ")}`
          : "No online peer yet — distributed tasks and transfers stay unavailable until a second node is paired",
      });

      const plugins = await get<{ plugins: { name: string; enabled: boolean; available: boolean }[] }>("/api/plugins");
      const enabled = plugins.plugins.filter((plugin) => plugin.enabled);
      results.push({
        name: "Task plugins",
        status: enabled.length ? "ok" : "warn",
        detail: `${enabled.length} enabled: ${enabled.map((plugin) => plugin.name).join(", ") || "none"}`,
      });

      const target = online.find((peer) => peer.remote_accepts_tasks);
      if (target) {
        results.push({ name: `Remote benchmark (${target.name})`, status: "ok", detail: "Submitting a 1 MiB disk benchmark…" });
        setChecks([...results]);
        try {
          const task = await post<Task>("/api/tasks", {
            name: "Setup connection test",
            plugin: "disk_benchmark",
            spec: { size_mb: 1 },
            priority: 8,
            target_node: target.node_id,
            max_retries: 0,
          });
          let final: Task | null = null;
          for (let attempt = 0; attempt < 80; attempt += 1) {
            await new Promise((resolve) => window.setTimeout(resolve, 1500));
            const current = await get<Task>(`/api/tasks/${task.id}`);
            if (current.status === "completed" || current.status === "failed" || current.status === "cancelled") {
              final = current;
              break;
            }
          }
          if (final?.status === "completed") {
            const result = final.result || {};
            results[results.length - 1] = {
              name: `Remote benchmark (${target.name})`,
              status: "ok",
              detail: `Completed on ${target.name}: write ${result.write_mbps ?? "?"} MB/s, read ${result.read_mbps ?? "?"} MB/s, sha256 ${String(result.sha256 || "").slice(0, 12)}…`,
            };
          } else {
            results[results.length - 1] = {
              name: `Remote benchmark (${target.name})`,
              status: "fail",
              detail: final?.error || (final ? `Task ended as ${final.status}` : "Timed out waiting for the remote worker"),
            };
          }
        } catch (err) {
          results[results.length - 1] = {
            name: `Remote benchmark (${target.name})`,
            status: "fail",
            detail: err instanceof Error ? err.message : "remote task submission failed",
          };
        }
      } else if (online.length) {
        results.push({
          name: "Remote execution",
          status: "warn",
          detail: "Peers are online but none currently grants task execution to this node (check Devices → permissions).",
        });
      }
      setChecks(results);
      refresh();
    } catch (err) {
      results.push({ name: "Self test", status: "fail", detail: err instanceof Error ? err.message : "test failed" });
      setChecks(results);
    } finally {
      setTesting(false);
    }
  };

  const screens = useMemo(() => {
    if (!devices) return layout.screens;
    const local = devices.local;
    return layout.screens
      .map((screen) => (screen.node_id === local.node_id ? { ...screen, name: local.name } : screen))
      .sort((a, b) => (a.side === "left" ? -1 : 1) - (b.side === "left" ? -1 : 1));
  }, [devices, layout.screens]);

  const stepKey: StepKey = STEPS[step].key;
  const next = () => setStep((value) => Math.min(value + 1, STEPS.length - 1));
  const back = () => setStep((value) => Math.max(value - 1, 0));

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/75 p-3 backdrop-blur-sm sm:p-6">
      <div className="panel flex h-[min(92vh,880px)] w-full max-w-6xl flex-col overflow-hidden">
        <header className="flex items-center gap-3 border-b border-ink-700/60 px-4 py-3 sm:px-5">
          <span className="grid h-9 w-9 place-items-center rounded-lg border border-cyan-400/30 bg-cyan-400/10 text-cyan-300">
            <Zap size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-base font-semibold text-white">First-run configuration</h2>
            <p className="text-[11px] text-slate-500">
              Step {step + 1} of {STEPS.length} · real hardware, no simulated values
            </p>
          </div>
          <button className="btn-ghost btn-xs" onClick={closeWizard} title="Close wizard">
            <X size={14} /> {step === STEPS.length - 1 ? "Close" : "Skip"}
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
          <nav className="flex shrink-0 gap-1 overflow-x-auto border-b border-ink-700/60 p-2 sm:w-56 sm:flex-col sm:overflow-y-auto sm:border-b-0 sm:border-r sm:p-3">
            {STEPS.map((item, index) => {
              const Icon = item.icon;
              const active = index === step;
              const done = index < step;
              return (
                <button
                  key={item.key}
                  onClick={() => setStep(index)}
                  className={`flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-left text-xs transition ${
                    active ? "bg-cyan-400/10 text-cyan-200" : "text-slate-500 hover:bg-ink-800/70 hover:text-slate-200"
                  }`}
                >
                  <span
                    className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[10px] ${
                      done
                        ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
                        : active
                          ? "border-cyan-400/50 bg-cyan-400/15 text-cyan-200"
                          : "border-ink-600 text-slate-500"
                    }`}
                  >
                    {done ? <Check size={12} /> : <Icon size={12} />}
                  </span>
                  <span className="whitespace-nowrap font-medium">{item.label}</span>
                </button>
              );
            })}
          </nav>

          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
            {loadError && (
              <div className="mb-4 rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
                {loadError}
              </div>
            )}
            {!isAdmin && (
              <div className="mb-4 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">
                You are signed in as viewer. Changes made here will be rejected by the agent; ask an admin to run the wizard.
              </div>
            )}

            {stepKey === "node" && (
              <StepShell
                title="Inspect this node"
                hint="The agent has already generated a persistent identity, a self-signed TLS certificate and started real hardware sampling. Verify the facts below before continuing."
              >
                <div className="grid gap-3 sm:grid-cols-2">
                  {[
                    ["Node name", diagnostics?.node_name ?? "…"],
                    ["Node ID", diagnostics?.node_id ?? "…"],
                    ["Operating system", diagnostics?.os ?? "…"],
                    ["Hostname", diagnostics?.hostname ?? "…"],
                    ["Agent version", diagnostics ? `${diagnostics.version} (Python ${diagnostics.python})` : "…"],
                    ["Fingerprint", diagnostics?.fingerprint ?? "…"],
                    ["Data directory", diagnostics?.data_dir ?? "…"],
                    ["Dashboard / peer port", diagnostics ? `${diagnostics.dashboard_port} / ${diagnostics.peer_port}` : "…"],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5">
                      <div className="label">{label}</div>
                      <div className="mt-1 truncate font-mono text-xs text-slate-300">{value}</div>
                    </div>
                  ))}
                </div>
                <div className="rounded-xl border border-ink-700/60 bg-ink-900/50 p-3">
                  <div className="label">LAN addresses</div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {(devices?.local.addresses || []).map((address) => (
                      <span key={address} className="badge-info font-mono">
                        {address}
                      </span>
                    ))}
                    {!devices?.local.addresses.length && <span className="text-xs text-slate-500">No LAN address detected</span>}
                  </div>
                  <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
                    Discovery uses mDNS and a UDP beacon on port {settings?.config.discovery?.beacon_port ?? 45873}. If the router
                    blocks multicast, pair by IP on the next step.
                  </p>
                </div>
                <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.05] px-3 py-2.5 text-xs text-emerald-200/90">
                  <ShieldCheck size={13} className="mr-1 inline" /> Certificate-pinned peer TLS is active. The dashboard itself is
                  LAN-only and sessions are signed with a key stored under the data directory.
                </div>
              </StepShell>
            )}

            {stepKey === "pair" && (
              <StepShell
                title="Pair the second computer"
                hint="Run GOLEM LINK on the other machine, then pair. Codes must match on both screens; approval exchanges keys and stores the peer certificate."
              >
                <PairingFlow onChanged={() => void load()} />
                <div className="rounded-xl border border-ink-700/60 bg-ink-900/50 p-3 text-xs text-slate-400">
                  Paired:{" "}
                  {devices?.peers.length
                    ? devices.peers.map((peer) => `${peer.name} (${peer.status})`).join(", ")
                    : "none yet — you can finish the wizard and pair later from Network."}
                </div>
              </StepShell>
            )}

            {stepKey === "layout" && (
              <StepShell
                title="Screen layout for shared input"
                hint="GOLEM LINK does not intercept input itself: it configures Deskflow / Input Leap, which must be installed and run inside each logged-in desktop session."
              >
                <Toggle
                  label="Enable shared keyboard & mouse"
                  hint="Generates the KVM configuration. The tool still has to be started in your desktop session."
                  checked={layout.enabled}
                  onChange={(value) => setLayout((current) => ({ ...current, enabled: value }))}
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="KVM tool">
                    <select
                      className="select"
                      value={layout.tool}
                      onChange={(event) => setLayout((current) => ({ ...current, tool: event.target.value }))}
                    >
                      <option value="deskflow">Deskflow</option>
                      <option value="input-leap">Input Leap</option>
                      <option value="none">None / configure later</option>
                    </select>
                  </Field>
                  <Field label="Server (owns the physical keyboard & mouse)">
                    <select
                      className="select"
                      value={layout.server_node_id ?? ""}
                      onChange={(event) => setLayout((current) => ({ ...current, server_node_id: event.target.value || null }))}
                    >
                      <option value="">Not set</option>
                      {(layout.screens || []).map((screen) => (
                        <option key={screen.node_id} value={screen.node_id}>
                          {screen.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <div className="space-y-2">
                  {screens.map((screen, index) => (
                    <div key={screen.node_id} className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5">
                      <Laptop size={14} className="text-cyan-400/70" />
                      <span className="min-w-0 flex-1 truncate text-sm text-slate-200">{screen.name}</span>
                      <select
                        className="select w-28"
                        value={screen.side}
                        onChange={(event) =>
                          setLayout((current) => ({
                            ...current,
                            screens: current.screens.map((item, position) =>
                              position === index ? { ...item, side: event.target.value as "left" | "right" } : item,
                            ),
                          }))
                        }
                      >
                        <option value="left">left</option>
                        <option value="right">right</option>
                      </select>
                      <input
                        className="input w-24"
                        type="number"
                        min={640}
                        max={7680}
                        value={screen.width}
                        onChange={(event) =>
                          setLayout((current) => ({
                            ...current,
                            screens: current.screens.map((item, position) =>
                              position === index ? { ...item, width: Number(event.target.value) } : item,
                            ),
                          }))
                        }
                      />
                      <span className="text-xs text-slate-600">×</span>
                      <input
                        className="input w-24"
                        type="number"
                        min={480}
                        max={4320}
                        value={screen.height}
                        onChange={(event) =>
                          setLayout((current) => ({
                            ...current,
                            screens: current.screens.map((item, position) =>
                              position === index ? { ...item, height: Number(event.target.value) } : item,
                            ),
                          }))
                        }
                      />
                      {index > 1 && (
                        <button
                          className="btn-ghost btn-xs"
                          onClick={() =>
                            setLayout((current) => ({
                              ...current,
                              screens: current.screens.filter((_item, position) => position !== index),
                            }))
                          }
                        >
                          <Trash2 size={12} />
                        </button>
                      )}
                    </div>
                  ))}
                  {layout.screens.length < 2 && devices?.peers.length === 0 && (
                    <p className="text-xs text-amber-300/90">Pair a second node to place two screens. A single-screen layout cannot be generated.</p>
                  )}
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Toggle
                    label="Clipboard sharing"
                    checked={layout.clipboard_sharing}
                    onChange={(value) => setLayout((current) => ({ ...current, clipboard_sharing: value }))}
                  />
                  <Toggle
                    label="Reconnect after sleep"
                    checked={layout.auto_reconnect}
                    onChange={(value) => setLayout((current) => ({ ...current, auto_reconnect: value }))}
                  />
                </div>
                <div className="rounded-xl border border-ink-700/60 bg-ink-900/50 p-3">
                  <div className="flex items-center justify-between">
                    <div className="label">Detection on this node</div>
                    <span className={layoutStatus?.detected.installed ? "badge-ok" : "badge-warn"}>
                      {layoutStatus?.detected.installed ? "KVM tool installed" : "not installed"}
                    </span>
                  </div>
                  <div className="mt-2 space-y-1 font-mono text-[10px] text-slate-500">
                    {(layoutStatus?.detected.binaries ?? []).map((binary) => (
                      <div key={binary} className="truncate">{binary}</div>
                    ))}
                    {layoutStatus?.detected.running.map((proc) => (
                      <div key={proc.pid} className="text-emerald-300/80">running: {proc.name} (pid {proc.pid})</div>
                    ))}
                  </div>
                  <ul className="mt-2 space-y-1 text-[11px] leading-relaxed text-slate-500">
                    {(layoutStatus?.detected.notes ?? []).map((note) => (
                      <li key={note}>• {note}</li>
                    ))}
                  </ul>
                </div>
                <div className="flex justify-end">
                  <button className="btn-primary btn-xs" onClick={() => void saveLayout()} disabled={busy === "layout" || !isAdmin}>
                    {busy === "layout" ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Save layout
                  </button>
                </div>
              </StepShell>
            )}

            {stepKey === "shares" && (
              <StepShell
                title="Approve storage shares"
                hint="Nothing is shared by default. Pick an existing directory explicitly; system roots and whole drives require a second confirmation. Read-only shares cannot be written to or deleted from."
              >
                <div className="space-y-2">
                  {shares.length ? (
                    shares.map((share) => (
                      <div key={share.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5">
                        <HardDrive size={14} className={share.exists ? "text-cyan-400/80" : "text-rose-400/80"} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-sm text-slate-200">{share.name}</span>
                            {share.read_only && <span className="badge-idle">read-only</span>}
                            {share.allow_delete && <span className="badge-warn">delete</span>}
                            {!share.exists && <span className="badge-bad">missing</span>}
                          </div>
                          <div className="truncate font-mono text-[10px] text-slate-500">{share.path}</div>
                        </div>
                        <span className="font-mono text-[11px] text-slate-400">
                          {share.usage ? `${fmtBytes(share.usage.free)} free of ${fmtBytes(share.usage.total)}` : "—"}
                        </span>
                        <button className="btn-ghost btn-xs" onClick={() => void removeShare(share)} disabled={!isAdmin}>
                          <Trash2 size={12} />
                        </button>
                      </div>
                    ))
                  ) : (
                    <div className="empty">No shares approved yet. Remote storage and path-based tasks stay disabled.</div>
                  )}
                </div>
                <div className="rounded-xl border border-ink-700/60 bg-ink-900/50 p-3">
                  <div className="label">Approve a directory</div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                    <input
                      className="input"
                      placeholder="Label (e.g. Projects)"
                      value={shareForm.name}
                      onChange={(event) => setShareForm((current) => ({ ...current, name: event.target.value }))}
                    />
                    <input
                      className="input font-mono"
                      placeholder={devices?.local.os === "Windows" ? "D:\\Projects" : "/home/user/projects"}
                      value={shareForm.path}
                      onChange={(event) => setShareForm((current) => ({ ...current, path: event.target.value }))}
                    />
                    <button className="btn-primary btn-xs" onClick={() => void addShare()} disabled={busy === "share" || !shareForm.path || !isAdmin}>
                      <Plus size={12} /> Add
                    </button>
                  </div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    <Toggle
                      label="Read-only"
                      checked={shareForm.read_only}
                      onChange={(value) =>
                        setShareForm((current) => ({ ...current, read_only: value, allow_delete: value ? false : current.allow_delete }))
                      }
                    />
                    <Toggle
                      label="Allow delete"
                      hint="Required for remote files to be removed."
                      checked={shareForm.allow_delete}
                      disabled={shareForm.read_only}
                      onChange={(value) => setShareForm((current) => ({ ...current, allow_delete: value }))}
                    />
                  </div>
                  <label className="mt-2 flex items-start gap-2 text-[11px] text-slate-500">
                    <input
                      type="checkbox"
                      className="mt-0.5 h-3.5 w-3.5 rounded border-ink-600 bg-ink-950"
                      checked={shareForm.confirm_root}
                      onChange={(event) => setShareForm((current) => ({ ...current, confirm_root: event.target.checked }))}
                    />
                    I explicitly confirm sharing a system root or a whole drive (not recommended).
                  </label>
                  {shareError && <div className="mt-2 text-xs text-rose-300">{shareError}</div>}
                </div>
              </StepShell>
            )}

            {stepKey === "policy" && (
              <StepShell
                title="Scheduling and overload policy"
                hint="Work is placed on the node with the lowest live pressure score. CPU and RAM are aggregated for display only — nothing is merged or migrated at the OS level."
              >
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Placement policy" hint="remote_first only uses peers that explicitly accept tasks.">
                    <select
                      className="select"
                      value={policy.policy}
                      onChange={(event) => setPolicy((current) => ({ ...current, policy: event.target.value }))}
                    >
                      <option value="balanced">Balanced</option>
                      <option value="local_first">Local first</option>
                      <option value="remote_first">Remote first</option>
                    </select>
                  </Field>
                  <Field label="Max concurrent tasks on this node">
                    <input
                      className="input"
                      type="number"
                      min={1}
                      max={16}
                      value={policy.max_concurrent_tasks}
                      onChange={(event) => setPolicy((current) => ({ ...current, max_concurrent_tasks: Number(event.target.value) }))}
                    />
                  </Field>
                  <Field label="Reserved CPU %">
                    <input
                      className="input"
                      type="number"
                      min={0}
                      max={80}
                      value={policy.reserve_cpu_percent}
                      onChange={(event) => setPolicy((current) => ({ ...current, reserve_cpu_percent: Number(event.target.value) }))}
                    />
                  </Field>
                  <Field label="Reserved RAM %">
                    <input
                      className="input"
                      type="number"
                      min={0}
                      max={80}
                      value={policy.reserve_ram_percent}
                      onChange={(event) => setPolicy((current) => ({ ...current, reserve_ram_percent: Number(event.target.value) }))}
                    />
                  </Field>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Toggle
                    label="Allow remote execution"
                    hint="This node may run tasks submitted by paired peers that it authorizes."
                    checked={policy.allow_remote_execution}
                    onChange={(value) => setPolicy((current) => ({ ...current, allow_remote_execution: value }))}
                  />
                  <Toggle
                    label="Re-evaluate queued work under overload"
                    hint="Running OS processes are never migrated or killed."
                    checked={policy.rebalance_on_overload}
                    onChange={(value) => setPolicy((current) => ({ ...current, rebalance_on_overload: value }))}
                  />
                  <Toggle
                    label="Keep this machine light"
                    hint="New tasks go to the other machine above 55% CPU / 70% RAM when it is healthy; the limits are editable in Settings. Operating-system processes are never touched."
                    checked={policy.keep_host_light}
                    onChange={(value) => setPolicy((current) => ({ ...current, keep_host_light: value }))}
                  />
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  {[
                    ["CPU warning", "cpu_warn_percent"],
                    ["CPU critical", "cpu_crit_percent"],
                    ["RAM warning", "ram_warn_percent"],
                    ["RAM critical", "ram_crit_percent"],
                    ["Disk free warning", "disk_free_warn_percent"],
                    ["Disk free critical", "disk_free_crit_percent"],
                  ].map(([label, key]) => (
                    <Field key={key} label={label}>
                      <input
                        className="input"
                        type="number"
                        min={1}
                        max={99}
                        value={thresholds[key as keyof typeof thresholds] as number}
                        onChange={(event) =>
                          setThresholds((current) => ({ ...current, [key]: Number(event.target.value) }))
                        }
                      />
                    </Field>
                  ))}
                  <Field label="Temperature warning °C" hint="Only alerts on sensors the platform actually reports.">
                    <input
                      className="input"
                      type="number"
                      placeholder="unset"
                      value={thresholds.temp_warn_c}
                      onChange={(event) => setThresholds((current) => ({ ...current, temp_warn_c: event.target.value }))}
                    />
                  </Field>
                  <Field label="Temperature critical °C" hint="Sustained critical temperature pauses new task intake.">
                    <input
                      className="input"
                      type="number"
                      placeholder="unset"
                      value={thresholds.temp_crit_c}
                      onChange={(event) => setThresholds((current) => ({ ...current, temp_crit_c: event.target.value }))}
                    />
                  </Field>
                </div>
                <div className="flex justify-end">
                  <button className="btn-primary btn-xs" onClick={() => void savePolicy()} disabled={busy === "policy" || !isAdmin}>
                    {busy === "policy" ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Save policy
                  </button>
                </div>
              </StepShell>
            )}

            {stepKey === "startup" && (
              <StepShell
                title="Start automatically"
                hint="The agent runs as a restricted OS service once installed: systemd on Debian 13, a Windows service (boot start, automatic restart) on Windows. The dashboard itself is served by the agent on port 8080."
              >
                <Toggle
                  label="Start GOLEM LINK at boot"
                  hint="Stored in the node configuration; the installer applies the service definition."
                  checked={autoStart}
                  onChange={(value) => void saveAutoStart(value)}
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-xl border border-ink-700/60 bg-ink-900/50 p-3">
                    <div className="section-title">Debian 13</div>
                    <pre className="mt-2 overflow-x-auto rounded-lg bg-ink-950/80 p-3 font-mono text-[10px] leading-relaxed text-slate-400">
{`sudo systemctl enable --now golem-link
journalctl -u golem-link -f`}
                    </pre>
                    <p className="mt-2 text-[11px] text-slate-500">Runs as the unprivileged `golem-link` account with journald logging.</p>
                  </div>
                  <div className="rounded-xl border border-ink-700/60 bg-ink-900/50 p-3">
                    <div className="section-title">Windows 10</div>
                    <pre className="mt-2 overflow-x-auto rounded-lg bg-ink-950/80 p-3 font-mono text-[10px] leading-relaxed text-slate-400">
{`GOLEM-LINK-Setup.exe
Get-Service GolemLink
Restart-Service GolemLink`}
                    </pre>
                    <p className="mt-2 text-[11px] text-slate-500">Boot start, restart on failure, Private-network firewall rule.</p>
                  </div>
                </div>
                <p className="rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5 text-[11px] leading-relaxed text-slate-500">
                  The desktop dashboard is not the shared-input component. Deskflow / Input Leap must be started in the interactive
                  desktop session so it can capture the physical keyboard and mouse.
                </p>
              </StepShell>
            )}

            {stepKey === "test" && (
              <StepShell
                title="Verify real connectivity"
                hint="These checks exercise the running agent: a fresh psutil sample, paired peer status, enabled plugins and — when a peer grants task execution — a real 1 MiB disk benchmark executed on the remote node."
              >
                <div className="flex flex-wrap items-center gap-2">
                  <button className="btn-primary btn-xs" onClick={() => void runTests()} disabled={testing}>
                    {testing ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} {testing ? "Running checks…" : "Run connection test"}
                  </button>
                  <span className="text-[11px] text-slate-500">
                    The remote benchmark writes and reads a temporary file on the executor; it is deleted immediately.
                  </span>
                </div>
                <div className="space-y-2">
                  {(checks ?? []).map((check) => (
                    <div key={check.name} className="flex items-start gap-3 rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5">
                      <span
                        className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border text-[10px] ${
                          check.status === "ok"
                            ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
                            : check.status === "warn"
                              ? "border-amber-400/40 bg-amber-400/10 text-amber-300"
                              : "border-rose-400/40 bg-rose-400/10 text-rose-300"
                        }`}
                      >
                        {check.status === "ok" ? <Check size={11} /> : check.status === "warn" ? "!" : <X size={11} />}
                      </span>
                      <div className="min-w-0">
                        <div className="text-sm text-slate-200">{check.name}</div>
                        <div className="mt-0.5 break-words text-[11px] leading-relaxed text-slate-500">{check.detail}</div>
                      </div>
                    </div>
                  ))}
                  {!checks && !testing && (
                    <div className="empty">
                      <TestTube2 size={20} className="text-slate-600" />
                      No checks run yet.
                    </div>
                  )}
                </div>
              </StepShell>
            )}

            {stepKey === "summary" && (
              <StepShell
                title="Configuration summary"
                hint="Everything below reflects the persisted agent configuration. You can reopen this wizard from Settings at any time."
              >
                <div className="space-y-2">
                  {[
                    {
                      icon: Laptop,
                      label: "Node identity",
                      value: `${diagnostics?.node_name ?? "…"} · ${diagnostics?.node_id ?? "…"}`,
                    },
                    {
                      icon: Network,
                      label: "Paired devices",
                      value: devices?.peers.length
                        ? devices.peers.map((peer) => `${peer.name} (${peer.status}, tasks: ${peer.remote_accepts_tasks ? "yes" : "no"}, storage: ${peer.remote_accepts_storage ? "yes" : "no"})`).join(" · ")
                        : "No peer paired yet",
                    },
                    {
                      icon: LayoutGrid,
                      label: "Input layout",
                      value: layout.enabled
                        ? `${layout.tool} · ${screens.map((screen) => `${screen.name} (${screen.side})`).join(" | ") || "no screens"}`
                        : "Disabled",
                    },
                    {
                      icon: HardDrive,
                      label: "Approved shares",
                      value: shares.length ? shares.map((share) => share.name).join(", ") : "None",
                    },
                    {
                      icon: Gauge,
                      label: "Scheduling",
                      value: `${policy.policy} · max ${policy.max_concurrent_tasks} concurrent · CPU ${thresholds.cpu_warn_percent}/${thresholds.cpu_crit_percent}% · RAM ${thresholds.ram_warn_percent}/${thresholds.ram_crit_percent}%`,
                    },
                    { icon: Power, label: "Start at boot", value: autoStart ? "Enabled" : "Disabled" },
                    {
                      icon: ListChecks,
                      label: "Connection test",
                      value: checks
                        ? checks.every((check) => check.status !== "fail")
                          ? `${checks.filter((check) => check.status === "ok").length} of ${checks.length} checks passed`
                          : "Failures detected — review the test step"
                        : "Not run",
                    },
                  ].map(({ icon: Icon, label, value }) => (
                    <div key={label} className="flex items-start gap-3 rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5">
                      <Icon size={15} className="mt-0.5 shrink-0 text-cyan-400/80" />
                      <div className="min-w-0">
                        <div className="label">{label}</div>
                        <div className="mt-0.5 break-words text-xs leading-relaxed text-slate-300">{value}</div>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.05] px-3 py-2.5 text-[11px] leading-relaxed text-amber-100/80">
                  Honest limitations: OS processes are never migrated between machines; CPU/RAM totals are aggregates, not merged
                  resources; Wayland desktops may restrict input injection; SMB helpers are generated for you to review, never executed
                  automatically.
                </div>
              </StepShell>
            )}
          </div>
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-ink-700/60 px-4 py-3 sm:px-5">
          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            <Cpu size={12} /> {devices ? `${devices.local.cpu_cores_logical} logical cores · ${fmtBytes(devices.local.metrics?.ram.total ?? 0)} RAM` : "loading…"}
            <MonitorSmartphone size={12} className="ml-2" /> {devices ? `${devices.local.input_role} input role` : ""}
          </div>
          <div className="flex items-center gap-2">
            <button className="btn-ghost btn-xs" onClick={back} disabled={step === 0}>
              <ChevronLeft size={13} /> Back
            </button>
            {step < STEPS.length - 1 ? (
              <button className="btn-primary btn-xs" onClick={next}>
                Next <ChevronRight size={13} />
              </button>
            ) : (
              <button className="btn-primary btn-xs" onClick={closeWizard}>
                <Check size={13} /> Enter dashboard
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}
