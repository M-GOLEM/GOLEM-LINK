import {
  ClipboardCopy,
  ClipboardList,
  KeyRound,
  MonitorSmartphone,
  Plug,
  Plus,
  RefreshCw,
  Terminal,
  Trash2,
  Wand2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fmtBytes,
  fmtClock,
  fmtDuration,
  get,
  patch,
  post,
  type AuditEntry,
  type DevicePayload,
  type Diagnostics,
  type InputSharingGenerate,
  type SettingsPayload,
  type Share,
} from "../api";
import { CopyText, Empty, Field, Panel, Spinner, Toggle } from "../components/ui";
import { usePolling } from "../hooks";
import { useApp } from "../store";

const TABS = [
  { key: "node", label: "Node" },
  { key: "thresholds", label: "Overload thresholds" },
  { key: "scheduling", label: "Scheduling" },
  { key: "security", label: "Security" },
  { key: "input", label: "Input sharing" },
  { key: "clipboard", label: "Text relay" },
  { key: "diagnostics", label: "Diagnostics" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

interface ThresholdForm {
  cpu_warn_percent: string;
  cpu_crit_percent: string;
  ram_warn_percent: string;
  ram_crit_percent: string;
  temp_warn_c: string;
  temp_crit_c: string;
  disk_free_warn_percent: string;
  disk_free_crit_percent: string;
  net_latency_warn_ms: string;
  net_loss_warn_percent: string;
  sample_interval_s: string;
  adaptive_sampling: boolean;
  max_sample_interval_s: string;
  thermal_sustained_samples: string;
}

interface SchedulingForm {
  enabled: boolean;
  policy: "balanced" | "local_first" | "remote_first";
  allow_remote_execution: boolean;
  max_concurrent_tasks: string;
  default_timeout_s: string;
  max_retries: string;
  retry_backoff_s: string;
  reserve_cpu_percent: string;
  reserve_ram_percent: string;
  rebalance_on_overload: boolean;
  keep_host_light: boolean;
  keep_host_cpu_percent: string;
  keep_host_ram_percent: string;
  rank_aging_per_min: string;
  rank_aging_max: string;
  preempt_on_overload: boolean;
  preempt_after_s: string;
  preempt_max_per_task: string;
  relieve_memory: boolean;
  relieve_ram_percent: string;
  relieve_cooldown_s: string;
  nice_workers: boolean;
  enabled_plugins: string[];
  allow_custom_python: boolean;
}

interface DiscoveryForm {
  mdns_enabled: boolean;
  beacon_enabled: boolean;
  beacon_port: string;
  beacon_interval_s: string;
  reconnect_interval_s: string;
  offline_after_s: string;
  manual_peers_text: string;
}

interface DashboardForm {
  session_ttl_s: string;
  login_max_attempts: string;
  login_window_s: string;
}

interface ScreenRow {
  node_id: string;
  name: string;
  side: "left" | "right";
  width: string;
  height: string;
}

interface InputForm {
  enabled: boolean;
  tool: "deskflow" | "input-leap" | "none";
  server_node_id: string;
  screens: ScreenRow[];
  clipboard_sharing: boolean;
  auto_reconnect: boolean;
}

const BLANK_THRESHOLDS: ThresholdForm = {
  cpu_warn_percent: "80",
  cpu_crit_percent: "92",
  ram_warn_percent: "80",
  ram_crit_percent: "90",
  temp_warn_c: "",
  temp_crit_c: "",
  disk_free_warn_percent: "10",
  disk_free_crit_percent: "4",
  net_latency_warn_ms: "150",
  net_loss_warn_percent: "5",
  sample_interval_s: "2",
  adaptive_sampling: true,
  max_sample_interval_s: "6",
  thermal_sustained_samples: "3",
};

const BLANK_SCHEDULING: SchedulingForm = {
  enabled: true,
  policy: "balanced",
  allow_remote_execution: true,
  max_concurrent_tasks: "2",
  default_timeout_s: "3600",
  max_retries: "2",
  retry_backoff_s: "5",
  reserve_cpu_percent: "15",
  reserve_ram_percent: "10",
  rebalance_on_overload: true,
  keep_host_light: true,
  keep_host_cpu_percent: "55",
  keep_host_ram_percent: "70",
  rank_aging_per_min: "2",
  rank_aging_max: "25",
  preempt_on_overload: true,
  preempt_after_s: "15",
  preempt_max_per_task: "2",
  relieve_memory: true,
  relieve_ram_percent: "80",
  relieve_cooldown_s: "120",
  nice_workers: true,
  enabled_plugins: [],
  allow_custom_python: false,
};

const BLANK_DISCOVERY: DiscoveryForm = {
  mdns_enabled: true,
  beacon_enabled: true,
  beacon_port: "45873",
  beacon_interval_s: "5",
  reconnect_interval_s: "5",
  offline_after_s: "20",
  manual_peers_text: "",
};

const BLANK_DASHBOARD: DashboardForm = {
  session_ttl_s: "43200",
  login_max_attempts: "5",
  login_window_s: "300",
};

const BLANK_INPUT: InputForm = {
  enabled: false,
  tool: "deskflow",
  server_node_id: "",
  screens: [],
  clipboard_sharing: true,
  auto_reconnect: true,
};

const str = (value: unknown, fallback = "") =>
  value === null || value === undefined ? fallback : String(value);
const num = (value: string) => Number(value);
const nullableNum = (value: string) => (value.trim() === "" ? null : Number(value));

function NumberInput({
  value,
  onChange,
  step = "1",
  min,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  step?: string;
  min?: number;
  disabled?: boolean;
}) {
  return (
    <input
      className="input"
      type="number"
      inputMode="decimal"
      step={step}
      min={min}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function SaveButton({
  label,
  busy,
  disabled,
  onClick,
}: {
  label: string;
  busy: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button className="btn-primary" disabled={disabled || busy} onClick={onClick}>
      {busy ? "Saving…" : label}
    </button>
  );
}

export function SettingsPage() {
  const { me, toast, refresh, openWizard } = useApp();
  const canWrite = me?.role === "admin";
  const [tab, setTab] = useState<TabKey>("node");
  const [saving, setSaving] = useState<string | null>(null);

  const settingsQ = usePolling(() => get<SettingsPayload>("/api/settings"), 30000);
  const devicesQ = usePolling(() => get<DevicePayload>("/api/devices"), 20000);
  const diagnosticsQ = usePolling(() => get<Diagnostics>("/api/diagnostics"), 15000);
  const sharesQ = usePolling(() => get<{ shares: Share[] }>("/api/storage/shares"), 30000);

  const cfg = settingsQ.data?.config;

  const [nodeName, setNodeName] = useState("");
  const [logLevel, setLogLevel] = useState("INFO");
  const [autoStart, setAutoStart] = useState(true);
  const [thresholds, setThresholds] = useState<ThresholdForm>(BLANK_THRESHOLDS);
  const [scheduling, setScheduling] = useState<SchedulingForm>(BLANK_SCHEDULING);
  const [discovery, setDiscovery] = useState<DiscoveryForm>(BLANK_DISCOVERY);
  const [dashboardCfg, setDashboardCfg] = useState<DashboardForm>(BLANK_DASHBOARD);
  const [input, setInput] = useState<InputForm>(BLANK_INPUT);

  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [viewerPassword, setViewerPassword] = useState("");
  const [clipboard, setClipboard] = useState({ enabled: true, auto_copy: true });
  const [generated, setGenerated] = useState<InputSharingGenerate | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [feedBusy, setFeedBusy] = useState<string | null>(null);

  const hydrateStamp = useRef("");
  const stamp = useMemo(
    () =>
      cfg
        ? JSON.stringify({
            node_name: cfg.node_name,
            log_level: cfg.log_level,
            auto_start: cfg.auto_start,
            thresholds: cfg.thresholds,
            scheduling: cfg.scheduling,
            discovery: cfg.discovery,
            dashboard: {
              session_ttl_s: cfg.dashboard?.session_ttl_s,
              login_max_attempts: cfg.dashboard?.login_max_attempts,
              login_window_s: cfg.dashboard?.login_window_s,
            },
            input_sharing: cfg.input_sharing,
            clipboard: cfg.clipboard,
          })
        : "",
    [cfg],
  );

  useEffect(() => {
    if (!cfg || !stamp || stamp === hydrateStamp.current) return;
    hydrateStamp.current = stamp;
    setNodeName(str(cfg.node_name));
    setLogLevel(str(cfg.log_level, "INFO"));
    setAutoStart(!!cfg.auto_start);
    const t = cfg.thresholds ?? {};
    setThresholds({
      cpu_warn_percent: str(t.cpu_warn_percent, "80"),
      cpu_crit_percent: str(t.cpu_crit_percent, "92"),
      ram_warn_percent: str(t.ram_warn_percent, "80"),
      ram_crit_percent: str(t.ram_crit_percent, "90"),
      temp_warn_c: str(t.temp_warn_c),
      temp_crit_c: str(t.temp_crit_c),
      disk_free_warn_percent: str(t.disk_free_warn_percent, "10"),
      disk_free_crit_percent: str(t.disk_free_crit_percent, "4"),
      net_latency_warn_ms: str(t.net_latency_warn_ms, "150"),
      net_loss_warn_percent: str(t.net_loss_warn_percent, "5"),
      sample_interval_s: str(t.sample_interval_s, "2"),
      adaptive_sampling: t.adaptive_sampling !== false,
      max_sample_interval_s: str(t.max_sample_interval_s, "6"),
      thermal_sustained_samples: str(t.thermal_sustained_samples, "3"),
    });
    const s = cfg.scheduling ?? {};
    setScheduling({
      enabled: s.enabled !== false,
      policy: (s.policy ?? "balanced") as SchedulingForm["policy"],
      allow_remote_execution: s.allow_remote_execution !== false,
      max_concurrent_tasks: str(s.max_concurrent_tasks, "2"),
      default_timeout_s: str(s.default_timeout_s, "3600"),
      max_retries: str(s.max_retries, "2"),
      retry_backoff_s: str(s.retry_backoff_s, "5"),
      reserve_cpu_percent: str(s.reserve_cpu_percent, "15"),
      reserve_ram_percent: str(s.reserve_ram_percent, "10"),
      rebalance_on_overload: s.rebalance_on_overload !== false,
      keep_host_light: s.keep_host_light !== false,
      keep_host_cpu_percent: str(s.keep_host_cpu_percent, "55"),
      keep_host_ram_percent: str(s.keep_host_ram_percent, "70"),
      rank_aging_per_min: str(s.rank_aging_per_min, "2"),
      rank_aging_max: str(s.rank_aging_max, "25"),
      preempt_on_overload: s.preempt_on_overload !== false,
      preempt_after_s: str(s.preempt_after_s, "15"),
      preempt_max_per_task: str(s.preempt_max_per_task, "2"),
      relieve_memory: s.relieve_memory !== false,
      relieve_ram_percent: str(s.relieve_ram_percent, "80"),
      relieve_cooldown_s: str(s.relieve_cooldown_s, "120"),
      nice_workers: s.nice_workers !== false,
      enabled_plugins: Array.isArray(s.enabled_plugins) ? [...s.enabled_plugins] : [],
      allow_custom_python: !!s.allow_custom_python,
    });
    const d = cfg.discovery ?? {};
    setDiscovery({
      mdns_enabled: d.mdns_enabled !== false,
      beacon_enabled: d.beacon_enabled !== false,
      beacon_port: str(d.beacon_port, "45873"),
      beacon_interval_s: str(d.beacon_interval_s, "5"),
      reconnect_interval_s: str(d.reconnect_interval_s, "5"),
      offline_after_s: str(d.offline_after_s, "20"),
      manual_peers_text: Array.isArray(d.manual_peers) ? d.manual_peers.join("\n") : "",
    });
    const dash = cfg.dashboard ?? {};
    setDashboardCfg({
      session_ttl_s: str(dash.session_ttl_s, "43200"),
      login_max_attempts: str(dash.login_max_attempts, "5"),
      login_window_s: str(dash.login_window_s, "300"),
    });
    const is = cfg.input_sharing ?? {};
    setInput({
      enabled: !!is.enabled,
      tool: (is.tool ?? "deskflow") as InputForm["tool"],
      server_node_id: str(is.server_node_id),
      screens: Array.isArray(is.screens)
        ? is.screens.map((screen: any) => ({
            node_id: str(screen.node_id),
            name: str(screen.name),
            side: screen.side === "right" ? "right" : "left",
            width: str(screen.width, "1920"),
            height: str(screen.height, "1080"),
          }))
        : [],
      clipboard_sharing: is.clipboard_sharing !== false,
      auto_reconnect: is.auto_reconnect !== false,
    });
    const clip = cfg.clipboard ?? {};
    setClipboard({
      enabled: clip.enabled !== false,
      auto_copy: clip.auto_copy !== false,
    });
  }, [cfg, stamp]);

  const saveSettings = useCallback(
    async (key: string, payload: Record<string, unknown>, message: string) => {
      setSaving(key);
      try {
        const result = await patch<{ changed: string[]; restart_required: boolean }>("/api/settings", payload);
        toast(
          result.restart_required
            ? `${message}. Restart the agent to apply port/discovery changes.`
            : message,
          "ok",
        );
        await settingsQ.reload();
        refresh();
      } catch (err) {
        toast(err instanceof Error ? err.message : "could not save settings", "error");
      } finally {
        setSaving(null);
      }
    },
    [settingsQ, refresh, toast],
  );

  const saveNode = () => {
    if (!nodeName.trim()) {
      toast("Node name cannot be empty", "error");
      return;
    }
    void saveSettings(
      "node",
      { node_name: nodeName.trim(), log_level: logLevel, auto_start: autoStart },
      "Node settings saved",
    );
  };

  const saveThresholds = () =>
    void saveSettings(
      "thresholds",
      {
        thresholds: {
          cpu_warn_percent: num(thresholds.cpu_warn_percent),
          cpu_crit_percent: num(thresholds.cpu_crit_percent),
          ram_warn_percent: num(thresholds.ram_warn_percent),
          ram_crit_percent: num(thresholds.ram_crit_percent),
          temp_warn_c: nullableNum(thresholds.temp_warn_c),
          temp_crit_c: nullableNum(thresholds.temp_crit_c),
          disk_free_warn_percent: num(thresholds.disk_free_warn_percent),
          disk_free_crit_percent: num(thresholds.disk_free_crit_percent),
          net_latency_warn_ms: num(thresholds.net_latency_warn_ms),
          net_loss_warn_percent: num(thresholds.net_loss_warn_percent),
          sample_interval_s: num(thresholds.sample_interval_s),
          adaptive_sampling: thresholds.adaptive_sampling,
          max_sample_interval_s: num(thresholds.max_sample_interval_s),
          thermal_sustained_samples: Math.round(num(thresholds.thermal_sustained_samples)),
        },
      },
      "Overload thresholds saved",
    );

  const saveClipboard = () =>
    void saveSettings(
      "clipboard",
      { clipboard: { enabled: clipboard.enabled, auto_copy: clipboard.auto_copy } },
      "Text relay settings saved",
    );

  const saveScheduling = () =>
    void saveSettings(
      "scheduling",
      {
        scheduling: {
          enabled: scheduling.enabled,
          policy: scheduling.policy,
          allow_remote_execution: scheduling.allow_remote_execution,
          max_concurrent_tasks: Math.round(num(scheduling.max_concurrent_tasks)),
          default_timeout_s: Math.round(num(scheduling.default_timeout_s)),
          max_retries: Math.round(num(scheduling.max_retries)),
          retry_backoff_s: num(scheduling.retry_backoff_s),
          reserve_cpu_percent: num(scheduling.reserve_cpu_percent),
          reserve_ram_percent: num(scheduling.reserve_ram_percent),
          rebalance_on_overload: scheduling.rebalance_on_overload,
          keep_host_light: scheduling.keep_host_light,
          keep_host_cpu_percent: num(scheduling.keep_host_cpu_percent),
          keep_host_ram_percent: num(scheduling.keep_host_ram_percent),
          rank_aging_per_min: num(scheduling.rank_aging_per_min),
          rank_aging_max: num(scheduling.rank_aging_max),
          preempt_on_overload: scheduling.preempt_on_overload,
          preempt_after_s: num(scheduling.preempt_after_s),
          preempt_max_per_task: Math.round(num(scheduling.preempt_max_per_task)),
          relieve_memory: scheduling.relieve_memory,
          relieve_ram_percent: num(scheduling.relieve_ram_percent),
          relieve_cooldown_s: num(scheduling.relieve_cooldown_s),
          nice_workers: scheduling.nice_workers,
          enabled_plugins: scheduling.enabled_plugins,
          allow_custom_python: scheduling.allow_custom_python,
        },
      },
      "Scheduling policy saved",
    );

  const saveDiscovery = () => {
    const peers = Array.from(
      new Set(
        discovery.manual_peers_text
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean),
      ),
    );
    void saveSettings(
      "discovery",
      {
        discovery: {
          mdns_enabled: discovery.mdns_enabled,
          beacon_enabled: discovery.beacon_enabled,
          beacon_port: Math.round(num(discovery.beacon_port)),
          beacon_interval_s: num(discovery.beacon_interval_s),
          reconnect_interval_s: num(discovery.reconnect_interval_s),
          offline_after_s: num(discovery.offline_after_s),
          manual_peers: peers,
        },
      },
      "Discovery settings saved",
    );
  };

  const saveDashboard = () =>
    void saveSettings(
      "dashboard",
      {
        dashboard: {
          session_ttl_s: Math.round(num(dashboardCfg.session_ttl_s)),
          login_max_attempts: Math.round(num(dashboardCfg.login_max_attempts)),
          login_window_s: Math.round(num(dashboardCfg.login_window_s)),
        },
      },
      "Dashboard security settings saved",
    );

  const changePassword = async () => {
    if (newPassword.length < 8) {
      toast("New password must be at least 8 characters", "error");
      return;
    }
    if (newPassword !== confirmPassword) {
      toast("New password and confirmation do not match", "error");
      return;
    }
    setSaving("password");
    try {
      await post("/api/auth/password", { old_password: oldPassword, new_password: newPassword });
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
      toast("Admin password changed", "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not change password", "error");
    } finally {
      setSaving(null);
    }
  };

  const setViewer = async (password: string | null) => {
    setSaving("viewer");
    try {
      const result = await post<{ viewer_enabled: boolean }>("/api/auth/viewer-password", {
        password,
      });
      setViewerPassword("");
      toast(result.viewer_enabled ? "Viewer account enabled" : "Viewer account disabled", "ok");
      await settingsQ.reload();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not update viewer account", "error");
    } finally {
      setSaving(null);
    }
  };

  const updateScreen = (index: number, patchValue: Partial<ScreenRow>) =>
    setInput((current) => ({
      ...current,
      screens: current.screens.map((screen, position) =>
        position === index ? { ...screen, ...patchValue } : screen,
      ),
    }));

  const addScreen = (device?: { node_id: string; name: string }) => {
    setInput((current) => {
      if (device && current.screens.some((screen) => screen.node_id === device.node_id)) return current;
      return {
        ...current,
        screens: [
          ...current.screens,
          {
            node_id: device?.node_id ?? "",
            name: device?.name ?? "",
            side: current.screens.length === 0 ? "left" : "right",
            width: "1920",
            height: "1080",
          },
        ],
      };
    });
  };

  const saveLayout = async () => {
    setSaving("input");
    try {
      await post("/api/input-sharing", {
        enabled: input.enabled,
        tool: input.tool,
        server_node_id: input.server_node_id || null,
        screens: input.screens.map((screen) => ({
          node_id: screen.node_id.trim(),
          name: screen.name.trim() || screen.node_id.trim(),
          side: screen.side,
          width: Math.round(num(screen.width)) || 1920,
          height: Math.round(num(screen.height)) || 1080,
        })),
        clipboard_sharing: input.clipboard_sharing,
        auto_reconnect: input.auto_reconnect,
      });
      toast("Input sharing layout saved", "ok");
      await settingsQ.reload();
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not save input sharing layout", "error");
    } finally {
      setSaving(null);
    }
  };

  const generateConfig = async () => {
    setSaving("generate");
    try {
      const result = await post<InputSharingGenerate>("/api/input-sharing/generate");
      setGenerated(result);
      toast("Deskflow configuration generated", "ok");
      await settingsQ.reload();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not generate configuration", "error");
    } finally {
      setSaving(null);
    }
  };

  const loadLogs = useCallback(async () => {
    setFeedBusy("logs");
    try {
      const result = await get<{ lines: string[] }>("/api/logs?lines=300");
      setLogLines(result.lines);
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not read logs", "error");
    } finally {
      setFeedBusy(null);
    }
  }, [toast]);

  const loadAudit = useCallback(async () => {
    setFeedBusy("audit");
    try {
      const result = await get<{ audit: AuditEntry[] }>("/api/audit?limit=200");
      setAudit(result.audit);
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not read the audit log", "error");
    } finally {
      setFeedBusy(null);
    }
  }, [toast]);

  useEffect(() => {
    if (tab !== "diagnostics") return;
    void loadLogs();
    void loadAudit();
  }, [tab, loadLogs, loadAudit]);

  const knownDevices = useMemo(() => {
    const list: { node_id: string; name: string }[] = [];
    if (devicesQ.data) {
      list.push({ node_id: devicesQ.data.local.node_id, name: devicesQ.data.local.name });
      for (const peer of devicesQ.data.peers) list.push({ node_id: peer.node_id, name: peer.name });
    }
    return list;
  }, [devicesQ.data]);

  const detection = generated?.detected ?? settingsQ.data?.input_sharing_status.detected;
  const plugins = settingsQ.data?.plugins ?? diagnosticsQ.data?.plugins ?? [];
  const restartKeys = settingsQ.data?.restart_required_after ?? [];

  return (
    <div className="space-y-4">
      {settingsQ.error && (
        <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          Cannot load settings: {settingsQ.error}
        </div>
      )}
      {!canWrite && (
        <div className="rounded-xl border border-amber-400/25 bg-amber-400/10 px-4 py-3 text-sm text-amber-200">
          You are signed in as a viewer. Settings are read-only.
        </div>
      )}

      <div className="flex flex-wrap gap-1.5 rounded-xl border border-ink-700/60 bg-ink-900/60 p-1.5">
        {TABS.map((item) => (
          <button
            key={item.key}
            onClick={() => setTab(item.key)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              tab === item.key
                ? "bg-cyan-400/10 text-cyan-200"
                : "text-slate-400 hover:bg-ink-800 hover:text-slate-200"
            }`}
            aria-current={tab === item.key ? "page" : undefined}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "node" && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel
            title="Node identity"
            subtitle="Shown to peers and on the dashboard. Changing the name does not change the node id."
          >
            <div className="space-y-3">
              <Field label="Node name" hint="Human-readable label for this computer.">
                <input
                  className="input"
                  value={nodeName}
                  disabled={!canWrite}
                  maxLength={64}
                  onChange={(event) => setNodeName(event.target.value)}
                />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Log level" hint="Applied to new log records immediately.">
                  <select
                    className="select"
                    value={logLevel}
                    disabled={!canWrite}
                    onChange={(event) => setLogLevel(event.target.value)}
                  >
                    {["DEBUG", "INFO", "WARNING", "ERROR"].map((level) => (
                      <option key={level} value={level}>
                        {level}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Startup">
                  <Toggle
                    label="Start with the operating system"
                    hint="Installed services use this as their default; the installer is authoritative."
                    checked={autoStart}
                    disabled={!canWrite}
                    onChange={setAutoStart}
                  />
                </Field>
              </div>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <SaveButton label="Save node settings" busy={saving === "node"} disabled={!canWrite} onClick={saveNode} />
                <button className="btn-ghost" onClick={openWizard}>
                  Run setup wizard
                </button>
              </div>
            </div>
          </Panel>

          <Panel title="Identity and paths" subtitle="Read-only values from this agent.">
            <dl className="space-y-2 text-xs">
              {[
                ["Node id", me?.node_id ?? "—"],
                ["Version", me?.version ?? "—"],
                ["Data directory", settingsQ.data?.data_dir ?? "—"],
                ["Agent restart required for", restartKeys.join(", ") || "—"],
              ].map(([label, value]) => (
                <div key={label} className="flex items-start justify-between gap-3 rounded-lg border border-ink-700/50 bg-ink-900/40 px-3 py-2">
                  <dt className="text-slate-500">{label}</dt>
                  <dd className="max-w-[60%] break-words text-right font-mono text-[11px] text-slate-300">{value}</dd>
                </div>
              ))}
              <div className="flex items-start justify-between gap-3 rounded-lg border border-ink-700/50 bg-ink-900/40 px-3 py-2">
                <dt className="text-slate-500">TLS fingerprint</dt>
                <dd>
                  <CopyText value={me?.fingerprint ?? ""} />
                </dd>
              </div>
            </dl>
            <p className="mt-3 text-[11px] leading-relaxed text-slate-600">
              Port, mDNS and beacon changes are written to config.json but only take effect after restarting the agent
              process (or the installed service).
            </p>
          </Panel>

          <Panel
            title="Approved shares"
            subtitle="Storage roots this node exposes to paired peers. Managed on the Storage page."
            className="xl:col-span-2"
          >
            {sharesQ.data?.shares.length ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {sharesQ.data.shares.map((share) => (
                  <div key={share.id} className="rounded-xl border border-ink-700/60 bg-ink-900/40 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm text-slate-200">{share.name}</span>
                      <span className={share.read_only ? "badge-idle" : "badge-ok"}>
                        {share.read_only ? "read-only" : "read/write"}
                      </span>
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-slate-500">{share.path}</div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty icon={ClipboardList} title="No approved shares" hint="Add a share on the Storage page; nothing is shared by default." />
            )}
          </Panel>
        </div>
      )}

      {tab === "thresholds" && (
        <Panel
          title="Overload thresholds"
          subtitle="Used by the local overload engine for alerts and for excluding saturated nodes from scheduling."
          actions={
            <SaveButton
              label="Save thresholds"
              busy={saving === "thresholds"}
              disabled={!canWrite}
              onClick={saveThresholds}
            />
          }
        >
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <Field label="CPU warning %" hint="Alert when sustained CPU reaches this level.">
              <NumberInput value={thresholds.cpu_warn_percent} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, cpu_warn_percent: v })} />
            </Field>
            <Field label="CPU critical %" hint="Must be higher than the warning level.">
              <NumberInput value={thresholds.cpu_crit_percent} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, cpu_crit_percent: v })} />
            </Field>
            <Field label="Sample interval (s)" hint="Telemetry cadence; also drives alert smoothing.">
              <NumberInput value={thresholds.sample_interval_s} step="0.5" min={0.5} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, sample_interval_s: v })} />
            </Field>
            <Field label="Max sample interval (s)" hint="Ceiling used when adaptive sampling slows telemetry under heavy CPU.">
              <NumberInput value={thresholds.max_sample_interval_s} step="0.5" min={0.5} disabled={!canWrite || !thresholds.adaptive_sampling} onChange={(v) => setThresholds({ ...thresholds, max_sample_interval_s: v })} />
            </Field>
            <Field label="RAM warning %">
              <NumberInput value={thresholds.ram_warn_percent} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, ram_warn_percent: v })} />
            </Field>
            <Field label="RAM critical %">
              <NumberInput value={thresholds.ram_crit_percent} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, ram_crit_percent: v })} />
            </Field>
            <Field label="thermal sustained samples" hint="Consecutive critical samples before task intake is blocked.">
              <NumberInput value={thresholds.thermal_sustained_samples} min={1} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, thermal_sustained_samples: v })} />
            </Field>
            <Field label="Temperature warning °C" hint="Blank disables temperature warnings. Only sensors the hardware reports are evaluated.">
              <NumberInput value={thresholds.temp_warn_c} min={0} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, temp_warn_c: v })} />
            </Field>
            <Field label="Temperature critical °C" hint="Blank disables thermal protection.">
              <NumberInput value={thresholds.temp_crit_c} min={0} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, temp_crit_c: v })} />
            </Field>
            <Field label="Disk free warning %" hint="Alert when free space drops to this level.">
              <NumberInput value={thresholds.disk_free_warn_percent} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, disk_free_warn_percent: v })} />
            </Field>
            <Field label="Disk free critical %">
              <NumberInput value={thresholds.disk_free_crit_percent} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, disk_free_crit_percent: v })} />
            </Field>
            <Field label="Latency warning (ms)">
              <NumberInput value={thresholds.net_latency_warn_ms} disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, net_latency_warn_ms: v })} />
            </Field>
            <Field label="Packet loss warning %">
              <NumberInput value={thresholds.net_loss_warn_percent} step="0.5" disabled={!canWrite} onChange={(v) => setThresholds({ ...thresholds, net_loss_warn_percent: v })} />
            </Field>
          </div>
          <div className="mt-3">
            <Toggle
              label="Slow telemetry under heavy CPU"
              hint="Sampling drops to the max interval while CPU stays at the warning level, then returns to the base cadence below warning minus 10 points."
              checked={thresholds.adaptive_sampling}
              disabled={!canWrite}
              onChange={(value) => setThresholds({ ...thresholds, adaptive_sampling: value })}
            />
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-slate-600">
            This agent only raises alerts from real telemetry and never touches operating-system processes or other
            applications. When this node stays overloaded it stops its own running GOLEM task, requeues it, and lets the
            scheduler place it on a healthy paired device (bounded by the Scheduling settings).
          </p>
        </Panel>
      )}

      {tab === "scheduling" && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel
            title="Scheduling policy"
            subtitle="How new tasks choose an execution node."
            actions={
              <SaveButton
                label="Save scheduling"
                busy={saving === "scheduling"}
                disabled={!canWrite}
                onClick={saveScheduling}
              />
            }
          >
            <div className="space-y-3">
              <Toggle
                label="Enable the distributed scheduler"
                hint="When disabled, new tasks stay queued."
                checked={scheduling.enabled}
                disabled={!canWrite}
                onChange={(value) => setScheduling({ ...scheduling, enabled: value })}
              />
              <Field label="Policy">
                <select
                  className="select"
                  value={scheduling.policy}
                  disabled={!canWrite}
                  onChange={(event) =>
                    setScheduling({ ...scheduling, policy: event.target.value as SchedulingForm["policy"] })
                  }
                >
                  <option value="balanced">Balanced — least loaded node wins</option>
                  <option value="local_first">Local first — keep work on this node when healthy</option>
                  <option value="remote_first">Remote first — prefer a paired peer</option>
                </select>
              </Field>
              <Toggle
                label="Allow remote execution"
                hint="Peer tasks are still gated by the permissions set for that peer on the Devices page."
                checked={scheduling.allow_remote_execution}
                disabled={!canWrite}
                onChange={(value) => setScheduling({ ...scheduling, allow_remote_execution: value })}
              />
              <Toggle
                label="Move queued work away from overloaded nodes"
                hint="Queued tasks are re-placed when their target is overloaded. Running tasks are handled by the automatic host protection below; operating-system processes are never migrated."
                checked={scheduling.rebalance_on_overload}
                disabled={!canWrite}
                onChange={(value) => setScheduling({ ...scheduling, rebalance_on_overload: value })}
              />
              <Toggle
                label="Keep this machine light"
                hint="When this node is already busy and a healthy peer exists, new tasks start on the peer. Work still runs locally (rather than starving) when no peer can take it. Operating-system processes are never touched."
                checked={scheduling.keep_host_light}
                disabled={!canWrite}
                onChange={(value) => setScheduling({ ...scheduling, keep_host_light: value })}
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Host-light CPU limit %" hint="Offload new work above this utilisation.">
                  <NumberInput value={scheduling.keep_host_cpu_percent} min={0} disabled={!canWrite || !scheduling.keep_host_light} onChange={(v) => setScheduling({ ...scheduling, keep_host_cpu_percent: v })} />
                </Field>
                <Field label="Host-light RAM limit %">
                  <NumberInput value={scheduling.keep_host_ram_percent} min={0} disabled={!canWrite || !scheduling.keep_host_light} onChange={(v) => setScheduling({ ...scheduling, keep_host_ram_percent: v })} />
                </Field>
                <Field label="Max concurrent tasks">
                  <NumberInput value={scheduling.max_concurrent_tasks} min={1} disabled={!canWrite} onChange={(v) => setScheduling({ ...scheduling, max_concurrent_tasks: v })} />
                </Field>
                <Field label="Default timeout (s)">
                  <NumberInput value={scheduling.default_timeout_s} min={1} disabled={!canWrite} onChange={(v) => setScheduling({ ...scheduling, default_timeout_s: v })} />
                </Field>
                <Field label="Max retries">
                  <NumberInput value={scheduling.max_retries} min={0} disabled={!canWrite} onChange={(v) => setScheduling({ ...scheduling, max_retries: v })} />
                </Field>
                <Field label="Retry backoff (s)">
                  <NumberInput value={scheduling.retry_backoff_s} step="0.5" min={0} disabled={!canWrite} onChange={(v) => setScheduling({ ...scheduling, retry_backoff_s: v })} />
                </Field>
                <Field label="Reserve CPU %" hint="Headroom kept for the operating system.">
                  <NumberInput value={scheduling.reserve_cpu_percent} min={0} disabled={!canWrite} onChange={(v) => setScheduling({ ...scheduling, reserve_cpu_percent: v })} />
                </Field>
                <Field label="Reserve RAM %">
                  <NumberInput value={scheduling.reserve_ram_percent} min={0} disabled={!canWrite} onChange={(v) => setScheduling({ ...scheduling, reserve_ram_percent: v })} />
                </Field>
              </div>

              <div className="mt-1 space-y-2 border-t border-ink-700/60 pt-3">
                <Toggle
                  label="Protect this machine automatically"
                  hint="When the host stays above the limits above while running this node's own tasks, the lowest-priority task is stopped, requeued and placed on a healthy peer. Pinned targets are respected and each task is moved at most the limit below."
                  checked={scheduling.preempt_on_overload}
                  disabled={!canWrite}
                  onChange={(value) => setScheduling({ ...scheduling, preempt_on_overload: value })}
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Sustained busy before moving (s)" hint="How long the host must stay over the limits before a running task is moved.">
                    <NumberInput value={scheduling.preempt_after_s} min={0} disabled={!canWrite || !scheduling.preempt_on_overload} onChange={(v) => setScheduling({ ...scheduling, preempt_after_s: v })} />
                  </Field>
                  <Field label="Move limit per task">
                    <NumberInput value={scheduling.preempt_max_per_task} min={0} disabled={!canWrite || !scheduling.preempt_on_overload} onChange={(v) => setScheduling({ ...scheduling, preempt_max_per_task: v })} />
                  </Field>
                </div>
                <Toggle
                  label="Release the agent's memory under RAM pressure"
                  hint="Collects garbage and asks the OS allocator to reclaim this agent's own pages. Operating-system processes and other applications are never touched."
                  checked={scheduling.relieve_memory}
                  disabled={!canWrite}
                  onChange={(value) => setScheduling({ ...scheduling, relieve_memory: value })}
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Relief starts at RAM %">
                    <NumberInput value={scheduling.relieve_ram_percent} min={0} disabled={!canWrite || !scheduling.relieve_memory} onChange={(v) => setScheduling({ ...scheduling, relieve_ram_percent: v })} />
                  </Field>
                  <Field label="Relief cooldown (s)">
                    <NumberInput value={scheduling.relieve_cooldown_s} min={0} disabled={!canWrite || !scheduling.relieve_memory} onChange={(v) => setScheduling({ ...scheduling, relieve_cooldown_s: v })} />
                  </Field>
                </div>
                <Toggle
                  label="Run task workers at low priority"
                  hint="Windows workers start below normal priority; Linux workers run under nice 10. Heavy tasks then cannot freeze the desktop or the dashboard."
                  checked={scheduling.nice_workers}
                  disabled={!canWrite}
                  onChange={(value) => setScheduling({ ...scheduling, nice_workers: value })}
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Priority aging per minute" hint="Points a queued task gains for every minute it waits.">
                    <NumberInput value={scheduling.rank_aging_per_min} step="0.5" min={0} disabled={!canWrite} onChange={(v) => setScheduling({ ...scheduling, rank_aging_per_min: v })} />
                  </Field>
                  <Field label="Priority aging cap" hint="Maximum points an old queued task can gain; FIFO still breaks ties.">
                    <NumberInput value={scheduling.rank_aging_max} min={0} disabled={!canWrite} onChange={(v) => setScheduling({ ...scheduling, rank_aging_max: v })} />
                  </Field>
                </div>
              </div>
            </div>
          </Panel>

          <Panel
            title={
              <div className="section-title">
                <Plug size={13} className="text-cyan-400" /> Task plugins
              </div>
            }
            subtitle="Only enabled plugins can be selected when creating tasks on this node."
          >
            <div className="space-y-2">
              {plugins.map((plugin) => {
                const enabled = scheduling.enabled_plugins.includes(plugin.name);
                return (
                  <label
                    key={plugin.name}
                    className={`flex items-start gap-3 rounded-xl border px-3 py-2.5 transition ${
                      enabled ? "border-cyan-400/30 bg-cyan-400/[0.05]" : "border-ink-700/60 bg-ink-900/40"
                    } ${!plugin.available ? "opacity-50" : ""}`}
                  >
                    <input
                      type="checkbox"
                      className="mt-1 accent-cyan-400"
                      checked={enabled}
                      disabled={!canWrite || !plugin.available}
                      onChange={() =>
                        setScheduling({
                          ...scheduling,
                          enabled_plugins: enabled
                            ? scheduling.enabled_plugins.filter((name) => name !== plugin.name)
                            : [...scheduling.enabled_plugins, plugin.name],
                        })
                      }
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-slate-200">{plugin.name}</span>
                        {plugin.runs_user_code && <span className="badge-warn">runs user code</span>}
                        {!plugin.available && <span className="badge-idle">unavailable</span>}
                      </span>
                      <span className="mt-0.5 block text-[11px] leading-relaxed text-slate-500">{plugin.description}</span>
                      {plugin.requires.length > 0 && (
                        <span className="mt-1 block font-mono text-[10px] text-slate-600">
                          requires: {plugin.requires.join(", ")}
                        </span>
                      )}
                    </span>
                  </label>
                );
              })}
              {!plugins.length && <Spinner label="Loading plugin catalog…" />}
            </div>
            <div className="mt-3">
              <Toggle
                label="Allow custom Python tasks"
                hint="Executes operator-supplied code in the isolated worker process. Off by default; enable only if you trust everyone with dashboard access."
                checked={scheduling.allow_custom_python}
                disabled={!canWrite}
                onChange={(value) => setScheduling({ ...scheduling, allow_custom_python: value })}
              />
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-slate-600">
              Plugin changes are persisted together with the scheduling settings via the Save scheduling button.
            </p>
          </Panel>

          <Panel
            title="Discovery"
            subtitle="mDNS and the UDP beacon only operate on the local network."
            className="xl:col-span-2"
            actions={
              <SaveButton
                label="Save discovery"
                busy={saving === "discovery"}
                disabled={!canWrite}
                onClick={saveDiscovery}
              />
            }
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="space-y-2">
                <Toggle
                  label="mDNS / DNS-SD"
                  hint={`Service type _golem-link._tcp.local. Changing this requires an agent restart.`}
                  checked={discovery.mdns_enabled}
                  disabled={!canWrite}
                  onChange={(value) => setDiscovery({ ...discovery, mdns_enabled: value })}
                />
                <Toggle
                  label="UDP broadcast beacon"
                  hint="Fallback for routers that filter multicast. Requires an agent restart."
                  checked={discovery.beacon_enabled}
                  disabled={!canWrite}
                  onChange={(value) => setDiscovery({ ...discovery, beacon_enabled: value })}
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Beacon port">
                    <NumberInput value={discovery.beacon_port} min={1} disabled={!canWrite} onChange={(v) => setDiscovery({ ...discovery, beacon_port: v })} />
                  </Field>
                  <Field label="Beacon interval (s)">
                    <NumberInput value={discovery.beacon_interval_s} step="0.5" min={1} disabled={!canWrite} onChange={(v) => setDiscovery({ ...discovery, beacon_interval_s: v })} />
                  </Field>
                  <Field label="Reconnect interval (s)">
                    <NumberInput value={discovery.reconnect_interval_s} step="0.5" min={1} disabled={!canWrite} onChange={(v) => setDiscovery({ ...discovery, reconnect_interval_s: v })} />
                  </Field>
                  <Field label="Offline after (s)" hint="Silence before a peer is marked offline.">
                    <NumberInput value={discovery.offline_after_s} min={5} disabled={!canWrite} onChange={(v) => setDiscovery({ ...discovery, offline_after_s: v })} />
                  </Field>
                </div>
              </div>
              <Field
                label="Manual peer addresses"
                hint="One host or host:port per line. Only LAN addresses are accepted; used when discovery cannot see the other machine."
              >
                <textarea
                  className="input scroll-thin min-h-[180px] font-mono text-xs"
                  spellCheck={false}
                  value={discovery.manual_peers_text}
                  disabled={!canWrite}
                  onChange={(event) => setDiscovery({ ...discovery, manual_peers_text: event.target.value })}
                />
              </Field>
            </div>
          </Panel>
        </div>
      )}

      {tab === "security" && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel
            title="Dashboard sessions"
            subtitle="Signed cookies, CSRF protection and login rate limiting are always enforced."
            actions={
              <SaveButton
                label="Save security"
                busy={saving === "dashboard"}
                disabled={!canWrite}
                onClick={saveDashboard}
              />
            }
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Session lifetime (s)" hint="Idle session expiry; default 12 hours.">
                <NumberInput value={dashboardCfg.session_ttl_s} min={300} disabled={!canWrite} onChange={(v) => setDashboardCfg({ ...dashboardCfg, session_ttl_s: v })} />
              </Field>
              <Field label="Login attempts before lockout">
                <NumberInput value={dashboardCfg.login_max_attempts} min={1} disabled={!canWrite} onChange={(v) => setDashboardCfg({ ...dashboardCfg, login_max_attempts: v })} />
              </Field>
              <Field label="Rate-limit window (s)">
                <NumberInput value={dashboardCfg.login_window_s} min={30} disabled={!canWrite} onChange={(v) => setDashboardCfg({ ...dashboardCfg, login_window_s: v })} />
              </Field>
              <div className="rounded-xl border border-ink-700/60 bg-ink-900/40 px-3 py-2.5 text-[11px] leading-relaxed text-slate-500">
                Peer traffic is pinned to each node's self-signed certificate. Dashboard traffic stays plain HTTP on the
                LAN by design; bind to a private interface and never expose port {cfg?.dashboard?.port ?? 8080} to the
                internet.
              </div>
            </div>
          </Panel>

          <Panel
            title={
              <div className="section-title">
                <KeyRound size={13} className="text-cyan-400" /> Admin password
              </div>
            }
            subtitle="Stored as a scrypt hash in the local database. There is no default password."
          >
            {canWrite ? (
              <div className="space-y-3">
                <Field label="Current password">
                  <input
                    className="input"
                    type="password"
                    autoComplete="current-password"
                    value={oldPassword}
                    onChange={(event) => setOldPassword(event.target.value)}
                  />
                </Field>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="New password" hint="At least 8 characters.">
                    <input
                      className="input"
                      type="password"
                      autoComplete="new-password"
                      value={newPassword}
                      onChange={(event) => setNewPassword(event.target.value)}
                    />
                  </Field>
                  <Field label="Confirm new password">
                    <input
                      className="input"
                      type="password"
                      autoComplete="new-password"
                      value={confirmPassword}
                      onChange={(event) => setConfirmPassword(event.target.value)}
                    />
                  </Field>
                </div>
                <SaveButton
                  label="Change admin password"
                  busy={saving === "password"}
                  disabled={!oldPassword || !newPassword}
                  onClick={() => void changePassword()}
                />
              </div>
            ) : (
              <p className="text-sm text-slate-500">Only the admin account can change passwords.</p>
            )}
          </Panel>

          <Panel
            title="Viewer account"
            subtitle="Read-only access for monitoring. Viewers cannot change settings, tasks, storage or pairing."
            className="xl:col-span-2"
          >
            <div className="grid gap-4 lg:grid-cols-[1fr_auto]">
              <div className="space-y-3">
                <Field
                  label={cfg?.dashboard?.viewer_enabled ? "Replace viewer password" : "Set a viewer password"}
                  hint="Leave blank to keep the current password. Minimum 8 characters."
                >
                  <input
                    className="input"
                    type="password"
                    autoComplete="new-password"
                    value={viewerPassword}
                    disabled={!canWrite}
                    onChange={(event) => setViewerPassword(event.target.value)}
                  />
                </Field>
                <div className="flex flex-wrap gap-2">
                  <button
                    className="btn-primary"
                    disabled={!canWrite || viewerPassword.length < 8 || saving === "viewer"}
                    onClick={() => void setViewer(viewerPassword)}
                  >
                    {cfg?.dashboard?.viewer_enabled ? "Update viewer password" : "Enable viewer access"}
                  </button>
                  {cfg?.dashboard?.viewer_enabled && (
                    <button
                      className="btn-danger"
                      disabled={!canWrite || saving === "viewer"}
                      onClick={() => void setViewer(null)}
                    >
                      Disable viewer account
                    </button>
                  )}
                </div>
              </div>
              <div className="rounded-xl border border-ink-700/60 bg-ink-900/40 px-4 py-3 text-xs text-slate-500">
                <div className="label">Status</div>
                <div className="mt-2">
                  <span className={cfg?.dashboard?.viewer_enabled ? "badge-ok" : "badge-idle"}>
                    {cfg?.dashboard?.viewer_enabled ? "viewer enabled" : "viewer disabled"}
                  </span>
                </div>
                <p className="mt-2 max-w-[240px] leading-relaxed">
                  Username <span className="font-mono text-slate-400">viewer</span>; admin role cannot be downgraded.
                </p>
              </div>
            </div>
          </Panel>
        </div>
      )}

      {tab === "input" && (
        <div className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-2">
            <Panel
              title={
                <div className="section-title">
                  <MonitorSmartphone size={13} className="text-cyan-400" /> Layout
                </div>
              }
              subtitle="GOLEM LINK configures a proven KVM tool; it does not re-implement input interception."
              actions={
                <SaveButton
                  label="Save layout"
                  busy={saving === "input"}
                  disabled={!canWrite}
                  onClick={() => void saveLayout()}
                />
              }
            >
              <div className="space-y-3">
                <Toggle
                  label="Enable shared keyboard and mouse"
                  hint="Deskflow or Input Leap must be installed and running in each logged-in desktop session."
                  checked={input.enabled}
                  disabled={!canWrite}
                  onChange={(value) => setInput({ ...input, enabled: value })}
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="KVM tool">
                    <select
                      className="select"
                      value={input.tool}
                      disabled={!canWrite}
                      onChange={(event) => setInput({ ...input, tool: event.target.value as InputForm["tool"] })}
                    >
                      <option value="deskflow">Deskflow</option>
                      <option value="input-leap">Input Leap</option>
                      <option value="none">None</option>
                    </select>
                  </Field>
                  <Field label="Server node" hint="The machine whose keyboard/mouse is shared.">
                    <select
                      className="select"
                      value={input.server_node_id}
                      disabled={!canWrite}
                      onChange={(event) => setInput({ ...input, server_node_id: event.target.value })}
                    >
                      <option value="">Automatic (first screen)</option>
                      {input.screens
                        .filter((screen) => screen.node_id.trim())
                        .map((screen, index) => (
                          <option key={`${screen.node_id}-${index}`} value={screen.node_id.trim()}>
                            {screen.name || screen.node_id}
                          </option>
                        ))}
                    </select>
                  </Field>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Toggle
                    label="Share clipboard"
                    hint="Text clipboard sharing is provided by the KVM tool."
                    checked={input.clipboard_sharing}
                    disabled={!canWrite}
                    onChange={(value) => setInput({ ...input, clipboard_sharing: value })}
                  />
                  <Toggle
                    label="Auto reconnect"
                    hint="Ask the KVM client to reconnect after sleep or network interruptions."
                    checked={input.auto_reconnect}
                    disabled={!canWrite}
                    onChange={(value) => setInput({ ...input, auto_reconnect: value })}
                  />
                </div>

                <div className="rounded-xl border border-ink-700/60 bg-ink-900/40 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="label">Screens</span>
                    <div className="flex flex-wrap gap-1.5">
                      {knownDevices
                        .filter((device) => !input.screens.some((screen) => screen.node_id === device.node_id))
                        .map((device) => (
                          <button
                            key={device.node_id}
                            className="btn-ghost btn-xs"
                            disabled={!canWrite}
                            onClick={() => addScreen(device)}
                          >
                            <Plus size={11} /> {device.name}
                          </button>
                        ))}
                      <button className="btn-ghost btn-xs" disabled={!canWrite} onClick={() => addScreen()}>
                        <Plus size={11} /> Blank
                      </button>
                    </div>
                  </div>
                  {input.screens.length ? (
                    <div className="mt-2 space-y-2">
                      {input.screens.map((screen, index) => (
                        <div key={index} className="rounded-lg border border-ink-700/60 bg-ink-950/50 p-2.5">
                          <div className="grid gap-2 sm:grid-cols-2">
                            <Field label="Name">
                              <input
                                className="input py-1.5 text-xs"
                                value={screen.name}
                                disabled={!canWrite}
                                placeholder="Display name"
                                onChange={(event) => updateScreen(index, { name: event.target.value })}
                              />
                            </Field>
                            <Field label="Node id">
                              <input
                                className="input py-1.5 font-mono text-[11px]"
                                value={screen.node_id}
                                disabled={!canWrite}
                                placeholder="gl-…"
                                onChange={(event) => updateScreen(index, { node_id: event.target.value })}
                              />
                            </Field>
                          </div>
                          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                            <Field label="Side">
                              <select
                                className="select py-1.5 text-xs"
                                value={screen.side}
                                disabled={!canWrite}
                                onChange={(event) =>
                                  updateScreen(index, { side: event.target.value as ScreenRow["side"] })
                                }
                              >
                                <option value="left">Left</option>
                                <option value="right">Right</option>
                              </select>
                            </Field>
                            <Field label="Width">
                              <NumberInput
                                value={screen.width}
                                min={1}
                                disabled={!canWrite}
                                onChange={(value) => updateScreen(index, { width: value })}
                              />
                            </Field>
                            <Field label="Height">
                              <NumberInput
                                value={screen.height}
                                min={1}
                                disabled={!canWrite}
                                onChange={(value) => updateScreen(index, { height: value })}
                              />
                            </Field>
                            <div className="flex items-end">
                              <button
                                className="btn-ghost btn-xs"
                                disabled={!canWrite}
                                onClick={() =>
                                  setInput((current) => ({
                                    ...current,
                                    screens: current.screens.filter((_, position) => position !== index),
                                  }))
                                }
                              >
                                <Trash2 size={12} /> Remove
                              </button>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
                      Add at least two screens with opposite left/right sides before generating a configuration.
                    </p>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    className="btn-primary"
                    disabled={!canWrite || saving === "generate"}
                    onClick={() => void generateConfig()}
                  >
                    <Wand2 size={13} /> {saving === "generate" ? "Generating…" : "Generate KVM config"}
                  </button>
                  <span className="text-[11px] text-slate-600">
                    Writes Deskflow/Input Leap configuration into {settingsQ.data?.input_sharing_status.config_dir ?? "the data directory"}.
                  </span>
                </div>
              </div>
            </Panel>

            <Panel
              title="Detection"
              subtitle="What this node can actually see. Nothing is installed automatically."
            >
              {detection ? (
                <div className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    <span className={detection.installed ? "badge-ok" : "badge-idle"}>
                      {detection.installed ? "tool detected" : "not installed"}
                    </span>
                    <span className="badge-info">tool: {detection.tool}</span>
                    <span className="badge-idle">platform: {detection.platform}</span>
                    <span className={detection.running.length ? "badge-ok" : "badge-idle"}>
                      {detection.running.length} process{detection.running.length === 1 ? "" : "es"}
                    </span>
                  </div>
                  {detection.binaries.length > 0 && (
                    <div>
                      <div className="label mb-1">Binaries</div>
                      <div className="space-y-1">
                        {detection.binaries.map((binary) => (
                          <div key={binary} className="font-mono text-[10px] text-slate-400">
                            {binary}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {detection.running.length > 0 && (
                    <div>
                      <div className="label mb-1">Running</div>
                      <div className="space-y-1">
                        {detection.running.map((proc) => (
                          <div key={proc.pid} className="font-mono text-[10px] text-slate-400">
                            pid {proc.pid} · {proc.name}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  <ul className="space-y-1.5 border-t border-ink-700/60 pt-3 text-[11px] leading-relaxed text-slate-500">
                    {detection.notes.map((note) => (
                      <li key={note} className="flex gap-2">
                        <span className="text-cyan-400/70">·</span>
                        <span>{note}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <Spinner label="Detecting KVM tooling…" />
              )}
            </Panel>
          </div>

          {generated && (
            <Panel
              title="Generated configuration"
              subtitle="Copy these paths and commands to the corresponding machine."
            >
              <div className="space-y-3">
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="rounded-xl border border-ink-700/60 bg-ink-900/40 px-3 py-2">
                    <div className="label">Server config</div>
                    <div className="mt-1 break-all font-mono text-[11px] text-slate-300">{generated.server_config_path}</div>
                  </div>
                  <div className="rounded-xl border border-ink-700/60 bg-ink-900/40 px-3 py-2">
                    <div className="label">Server / client screens</div>
                    <div className="mt-1 font-mono text-[11px] text-slate-300">
                      {generated.server_screen} → {generated.client_screen}
                    </div>
                  </div>
                </div>
                <div className="rounded-xl border border-ink-700/60 bg-ink-900/40 px-3 py-2">
                  <div className="label">Start scripts</div>
                  <div className="mt-1 space-y-0.5 break-all font-mono text-[11px] text-slate-300">
                    <div>Linux server — {generated.scripts.linux_server}</div>
                    <div>Linux client — {generated.scripts.linux_client}</div>
                    <div>Windows server — {generated.scripts.windows_server}</div>
                    <div>Windows client — {generated.scripts.windows_client}</div>
                  </div>
                </div>
                <ol className="space-y-1.5 text-xs text-slate-400">
                  {generated.instructions.map((instruction, index) => (
                    <li key={instruction} className="flex gap-2">
                      <span className="font-mono text-cyan-400/80">{index + 1}.</span>
                      <span>{instruction}</span>
                    </li>
                  ))}
                </ol>
                <p className="text-[11px] leading-relaxed text-slate-600">
                  {generated.auto_reconnect
                    ? "The start scripts restart the KVM tool after sleep, logout or a crash."
                    : "Auto-reconnect is off: the KVM tool stays stopped if it exits."}{" "}
                  On Wayland, remote input depends on compositor support — an X11 desktop session is the reliable
                  option.
                </p>
              </div>
            </Panel>
          )}
        </div>
      )}

      {tab === "clipboard" && (
        <div className="space-y-4">
          <Panel
            title={
              <div className="section-title">
                <ClipboardCopy size={13} className="text-cyan-400" /> Text relay
              </div>
            }
            subtitle="Send a copied text to another paired computer's clipboard. Only text is relayed, never files."
            actions={
              <SaveButton
                label="Save relay"
                busy={saving === "clipboard"}
                disabled={!canWrite}
                onClick={() => void saveClipboard()}
              />
            }
          >
            <div className="space-y-3">
              <Toggle
                label="Allow text relay on this node"
                hint="Paired devices that this node grants control to can send short text to its clipboard."
                checked={clipboard.enabled}
                disabled={!canWrite}
                onChange={(value) => setClipboard({ ...clipboard, enabled: value })}
              />
              <Toggle
                label="Copy received text automatically"
                hint="When the agent can reach the logged-in desktop it puts the text straight on the clipboard; otherwise the item waits in Text Relay with a Copy button."
                checked={clipboard.auto_copy}
                disabled={!canWrite || !clipboard.enabled}
                onChange={(value) => setClipboard({ ...clipboard, auto_copy: value })}
              />
              <p className="text-[11px] leading-relaxed text-slate-500">
                On Windows the service copies into the active console session. On Linux the service must run as the
                desktop user to reach the clipboard; if it cannot, open Text Relay on that machine and press Copy
                (the browser copies in your session).
              </p>
            </div>
          </Panel>
        </div>
      )}

      {tab === "diagnostics" && (
        <div className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-2">
            <Panel
              title="System"
              subtitle="Live values from this agent, refreshed every 15 seconds."
              actions={
                <button
                  className="btn-ghost btn-xs"
                  disabled={feedBusy === "logs"}
                  onClick={() => {
                    void diagnosticsQ.reload();
                    void loadLogs();
                  }}
                >
                  <RefreshCw size={12} /> Refresh
                </button>
              }
            >
              {diagnosticsQ.data ? (
                <dl className="grid gap-2 sm:grid-cols-2">
                  {[
                    ["Version", diagnosticsQ.data.version],
                    ["Node name", diagnosticsQ.data.node_name],
                    ["OS", diagnosticsQ.data.os],
                    ["Hostname", diagnosticsQ.data.hostname],
                    ["Python", diagnosticsQ.data.python],
                    ["Uptime", fmtDuration(diagnosticsQ.data.uptime_s)],
                    ["Dashboard port", String(diagnosticsQ.data.dashboard_port)],
                    ["Peer port", String(diagnosticsQ.data.peer_port)],
                    ["Peers paired", String(diagnosticsQ.data.peers_paired)],
                    ["Peers online", String(diagnosticsQ.data.peers_online)],
                    ["mDNS", diagnosticsQ.data.mdns_enabled ? "enabled" : "disabled"],
                    ["Beacon", diagnosticsQ.data.beacon_enabled ? "enabled" : "disabled"],
                    ["Database size", fmtBytes(diagnosticsQ.data.db_size)],
                    ["Data directory", diagnosticsQ.data.data_dir],
                    [
                      "Memory relief",
                      diagnosticsQ.data.host_guard?.enabled
                        ? `${diagnosticsQ.data.host_guard.runs} run(s), ${fmtBytes(diagnosticsQ.data.host_guard.total_reclaimed_bytes)} reclaimed`
                        : "disabled",
                    ],
                    [
                      "Automatic task move",
                      diagnosticsQ.data.scheduler_relief?.preempt_on_overload
                        ? `on after ${diagnosticsQ.data.scheduler_relief.preempt_after_s}s busy`
                        : "disabled",
                    ],
                    [
                      "Monitor interval",
                      diagnosticsQ.data.monitor_interval_s ? `${diagnosticsQ.data.monitor_interval_s}s` : "—",
                    ],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-lg border border-ink-700/50 bg-ink-900/40 px-3 py-2">
                      <dt className="text-[10px] uppercase tracking-[0.12em] text-slate-600">{label}</dt>
                      <dd className="mt-0.5 break-words font-mono text-[11px] text-slate-300">{value}</dd>
                    </div>
                  ))}
                  <div className="rounded-lg border border-ink-700/50 bg-ink-900/40 px-3 py-2 sm:col-span-2">
                    <dt className="text-[10px] uppercase tracking-[0.12em] text-slate-600">TLS fingerprint</dt>
                    <dd className="mt-1">
                      <CopyText value={diagnosticsQ.data.fingerprint} />
                    </dd>
                  </div>
                </dl>
              ) : (
                <Spinner label="Reading diagnostics…" />
              )}
            </Panel>

            <Panel
              title="Plugins"
              subtitle="Availability is evaluated on this node at call time."
              bodyClassName="p-0"
            >
              <div className="overflow-x-auto">
                <table className="w-full min-w-[460px] border-collapse">
                  <thead>
                    <tr className="border-b border-ink-700/60">
                      <th className="th">Plugin</th>
                      <th className="th">State</th>
                      <th className="th">Requires</th>
                      <th className="th">User code</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plugins.map((plugin) => (
                      <tr key={plugin.name} className="border-b border-ink-700/40 last:border-0">
                        <td className="td">
                          <div className="font-mono text-xs text-slate-200">{plugin.name}</div>
                          <div className="mt-0.5 max-w-[240px] text-[10px] text-slate-600">{plugin.description}</div>
                        </td>
                        <td className="td">
                          <span className={plugin.available ? "badge-ok" : "badge-idle"}>
                            {plugin.available ? "available" : "unavailable"}
                          </span>
                          {!plugin.enabled && <div className="mt-1 text-[10px] text-slate-600">disabled in settings</div>}
                        </td>
                        <td className="td font-mono text-[10px] text-slate-500">
                          {plugin.requires.length ? plugin.requires.join(", ") : "—"}
                        </td>
                        <td className="td">
                          {plugin.runs_user_code ? <span className="badge-warn">yes</span> : <span className="text-[11px] text-slate-600">no</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <Panel
              title={
                <div className="section-title">
                  <Terminal size={13} className="text-cyan-400" /> Agent log
                </div>
              }
              subtitle="Tail of the local log file. Secrets are not written to this file."
              actions={
                <button className="btn-ghost btn-xs" disabled={feedBusy === "logs"} onClick={() => void loadLogs()}>
                  <RefreshCw size={12} /> {feedBusy === "logs" ? "Loading…" : "Reload"}
                </button>
              }
            >
              {logLines.length ? (
                <pre className="scroll-thin max-h-[360px] overflow-auto whitespace-pre-wrap rounded-lg border border-ink-700/60 bg-ink-950/70 p-3 font-mono text-[10px] leading-relaxed text-slate-400">
                  {logLines.join("\n")}
                </pre>
              ) : (
                <Empty icon={Terminal} title="No log records" hint="The log file is created when the agent writes its first record." />
              )}
            </Panel>

            <Panel
              title={
                <div className="section-title">
                  <ClipboardList size={13} className="text-cyan-400" /> Audit trail
                </div>
              }
              subtitle="Authentication, pairing, task, storage and settings events."
              actions={
                <button className="btn-ghost btn-xs" disabled={feedBusy === "audit"} onClick={() => void loadAudit()}>
                  <RefreshCw size={12} /> {feedBusy === "audit" ? "Loading…" : "Reload"}
                </button>
              }
              bodyClassName="p-0"
            >
              {audit.length ? (
                <div className="scroll-thin max-h-[360px] overflow-auto">
                  <table className="w-full min-w-[480px] border-collapse">
                    <thead className="sticky top-0 bg-ink-850">
                      <tr className="border-b border-ink-700/60">
                        <th className="th">Time</th>
                        <th className="th">Actor</th>
                        <th className="th">Action</th>
                        <th className="th">Target</th>
                        <th className="th">IP</th>
                      </tr>
                    </thead>
                    <tbody>
                      {audit.map((entry) => (
                        <tr key={entry.id} className="border-b border-ink-700/40 last:border-0">
                          <td className="td whitespace-nowrap text-[10px] text-slate-500" title={fmtClock(entry.ts)}>
                            {fmtClock(entry.ts)}
                          </td>
                          <td className="td font-mono text-[10px] text-slate-400">{entry.actor}</td>
                          <td className="td text-[11px] text-slate-300">
                            {entry.action}
                            {entry.detail && <div className="max-w-[200px] truncate text-[10px] text-slate-600">{entry.detail}</div>}
                          </td>
                          <td className="td max-w-[140px] truncate font-mono text-[10px] text-slate-500">{entry.target ?? "—"}</td>
                          <td className="td font-mono text-[10px] text-slate-500">{entry.ip ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="p-4">
                  <Empty icon={ClipboardList} title="No audit records" hint="Actions will appear here as they occur." />
                </div>
              )}
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}
