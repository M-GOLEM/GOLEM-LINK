export interface CpuMetrics {
  percent: number;
  per_core: number[];
  load: number[];
  cores_physical: number;
  cores_logical: number;
  frequency_mhz: number | null;
}

export interface RamMetrics {
  total: number;
  used: number;
  available: number;
  percent: number;
  swap_total: number;
  swap_used: number;
  swap_percent: number;
}

export interface DiskEntry {
  device: string;
  mountpoint: string;
  fstype: string;
  total: number;
  used: number;
  free: number;
  percent: number;
}

export interface NetInterface {
  name: string;
  bytes_sent: number;
  bytes_recv: number;
  send_rate: number;
  recv_rate: number;
  is_up: boolean | null;
}

export interface TempEntry {
  chip: string;
  label: string;
  current: number;
  high: number | null;
  critical: number | null;
}

export interface GpuEntry {
  name: string;
  utilization: number | null;
  memory_used: number | null;
  memory_total: number | null;
  temperature: number | null;
  power: number | null;
}

export interface ProcessEntry {
  pid: number;
  name: string;
  cpu: number;
  memory: number;
}

export interface Metrics {
  ts: number;
  node_id: string;
  name: string;
  os: string;
  cpu: CpuMetrics;
  ram: RamMetrics;
  disk: DiskEntry[];
  disk_io: { read_bytes: number; write_bytes: number; read_rate: number; write_rate: number };
  net: { bytes_sent: number; bytes_recv: number; send_rate: number; recv_rate: number; interfaces: NetInterface[] };
  temps: TempEntry[];
  temp_source?: string | null;
  temp_note?: string | null;
  gpu: GpuEntry[];
  uptime_s: number;
  boot_time: number;
  processes: number;
  top_cpu?: ProcessEntry[];
  top_mem?: ProcessEntry[];
}

export interface Pressure {
  node_id: string;
  cpu_percent: number;
  ram_percent: number;
  max_temp_c: number | null;
  thermal_protected: boolean;
  blocked_reason: string | null;
  active_tasks: number;
  score: number;
  updated_at?: number;
}

export interface Peer {
  node_id: string;
  name: string;
  address: string;
  peer_port: number;
  status: string;
  last_seen: number | null;
  online_since?: number | null;
  latency_ms: number | null;
  failures?: number;
  reconnects?: number;
  last_error?: string | null;
  fingerprint: string;
  os?: string;
  os_version?: string;
  hostname?: string;
  cpu_model?: string;
  cores_physical?: number | null;
  cores_logical?: number | null;
  version?: string;
  allow_tasks: boolean;
  allow_storage: boolean;
  allow_updates: boolean;
  allow_control: boolean;
  remote_accepts_tasks?: boolean;
  remote_accepts_storage?: boolean;
  remote_allows_updates?: boolean;
  remote_allows_control?: boolean;
  auto_connect: boolean;
  metrics: Metrics | null;
  pressure: Pressure | null;
}

export interface Cluster {
  local: {
    node_id: string;
    name: string;
    metrics: Metrics | null;
    pressure: Pressure | null;
    fingerprint: string;
  };
  peers: Peer[];
  discovered: Discovered[];
  online: number;
  paired: number;
}

export interface NetworkPayload extends Cluster {
  interfaces: NetInterface[];
}

export interface Discovered {
  node_id: string;
  name: string;
  address: string;
  port: number;
  os: string;
  version: string;
  fingerprint: string;
  source: string;
  last_seen: number;
  paired: boolean;
}

export interface PairRequest {
  id: string;
  node_id: string;
  name: string;
  os: string;
  fingerprint: string;
  address: string;
  code: string;
  created_at: number;
  expires_at: number;
}

export interface Task {
  id: string;
  name: string;
  plugin: string;
  spec: Record<string, unknown> | null;
  priority: number;
  source_node: string;
  executor_node: string | null;
  status: "queued" | "dispatching" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  timeout_s: number;
  retries: number;
  max_retries: number;
  error: string | null;
  result: Record<string, unknown> | null;
  authorized_by: string | null;
  resource_profile: Record<string, unknown> | null;
  target_node: string | null;
  is_remote: boolean;
  can_cancel: boolean;
  can_retry: boolean;
  events?: { id: number; ts: number; event: string; detail: string | null }[];
}

export interface Alert {
  id: number;
  ts: number;
  node_id: string;
  severity: "warning" | "critical" | "info";
  category: string;
  message: string;
  value: number | null;
  threshold: number | null;
  acknowledged: number;
  acknowledged_at: number | null;
}

export interface StorageEntry {
  name: string;
  is_dir: boolean;
  size: number;
  mtime: number;
}

export interface Transfer {
  id: string;
  direction: "download" | "upload";
  peer_node: string;
  peer_name?: string;
  share_id: string;
  rel_path: string;
  size: number;
  transferred: number;
  status: "running" | "completed" | "failed" | "cancelled" | "cancelling";
  sha256: string | null;
  started_at: number;
  finished_at: number | null;
  error: string | null;
  speed_bps: number;
  human_size: string;
  human_transferred: string;
}

export interface Share {
  id: string;
  name: string;
  path: string;
  read_only: boolean;
  allow_delete: boolean;
  approved_at: string | null;
  exists: boolean;
  usage: { total: number; used: number; free: number; percent: number } | null;
}

export interface Volume {
  device: string;
  mountpoint: string;
  fstype: string;
  total: number;
  used: number;
  free: number;
  percent: number;
  removable: boolean;
}

export interface Overview {
  nodes_total: number;
  nodes_online: number;
  cores_total: number;
  cores_note: string;
  ram_total: number;
  ram_used: number;
  ram_note: string;
  storage_total: number;
  storage_free: number;
  storage_note: string;
  network: { online: number; avg_latency_ms: number | null; interfaces: NetInterface[] };
  tasks: { running: number; queued: number; completed: number; failed: number; cancelled: number; total: number };
  alerts_open: number;
  input_sharing_enabled: boolean;
  node_id: string;
  uptime_s: number | null;
}

export interface Me {
  role: "admin" | "viewer";
  csrf: string;
  node_id: string;
  node_name: string;
  fingerprint: string;
  version: string;
  setup_complete: boolean;
}

export interface DeviceInfo {
  node_id: string;
  name: string;
  os: string;
  os_version: string;
  os_release?: string;
  arch?: string;
  hostname: string;
  cpu_model: string;
  cpu_cores_physical: number;
  cpu_cores_logical: number;
  python?: string;
  app?: string;
  version: string;
  fingerprint: string;
  addresses: string[];
  dashboard_port: number;
  peer_port: number;
}

export interface DevicePayload {
  local: DeviceInfo & {
    status: string;
    self: boolean;
    metrics: Metrics | null;
    pressure: Pressure | null;
    shares: number;
    input_role: string;
  };
  peers: Peer[];
  discovered: Discovered[];
}

export interface PluginInfo {
  name: string;
  description: string;
  enabled: boolean;
  available: boolean;
  runs_user_code: boolean;
  requires: string[];
}

export interface ScreenConfig {
  node_id: string;
  name: string;
  side: "left" | "right";
  width: number;
  height: number;
}

export interface InputSharingDetect {
  installed: boolean;
  tool: string;
  binaries: string[];
  running: { pid: number; name: string }[];
  platform: string;
  notes: string[];
}

export interface InputSharingStatus {
  enabled: boolean;
  tool: string;
  server_node_id: string | null;
  screens: ScreenConfig[];
  detected: InputSharingDetect;
  config_dir: string;
}

export interface InputSharingGenerate {
  server_config_path: string;
  server_screen: string;
  client_screen: string;
  auto_reconnect: boolean;
  screens: ScreenConfig[];
  scripts: {
    linux_server: string;
    linux_client: string;
    windows_server: string;
    windows_client: string;
  };
  instructions: string[];
  detected: InputSharingDetect;
}

export interface PairBeginResult {
  request_id: string;
  node_id: string;
  name: string;
  address: string;
  code: string;
  fingerprint: string;
  expires_at: number;
}

export interface PairPollResult {
  request_id: string;
  status: "pending" | "approved" | "denied" | "expired" | string;
  node_id?: string;
}

export interface Diagnostics {
  version: string;
  node_id: string;
  node_name: string;
  os: string;
  hostname: string;
  data_dir: string;
  db_size: number;
  dashboard_port: number;
  peer_port: number;
  peers_paired: number;
  peers_online: number;
  mdns_enabled: boolean;
  beacon_enabled: boolean;
  plugins: PluginInfo[];
  python: string;
  uptime_s: number | null;
  fingerprint: string;
  host_guard?: {
    enabled: boolean;
    threshold_percent: number;
    cooldown_s: number;
    ram_percent: number | null;
    runs: number;
    last_run: number | null;
    last_reason: string | null;
    last_reclaimed_bytes: number;
    total_reclaimed_bytes: number;
    last_method: string | null;
  };
  scheduler_relief?: {
    preempt_on_overload: boolean;
    preempt_after_s: number;
    preempt_max_per_task: number;
    busy_for_s: number;
    local_running: number;
    rank_aging_per_min: number;
    rank_aging_max: number;
    nice_workers: boolean;
  };
  monitor_interval_s?: number;
}

export interface SettingsPayload {
  config: Record<string, any>;
  plugins: PluginInfo[];
  input_sharing_status: InputSharingStatus;
  data_dir: string;
  restart_required_after: string[];
}

export interface AuditEntry {
  id: number;
  ts: number;
  actor: string;
  action: string;
  target: string | null;
  detail: string | null;
  ip: string | null;
}

export interface UpdateArtifact {
  platform: string;
  file: string;
  sha256: string;
  size: number | null;
  source: string;
  source_node?: string | null;
  source_name?: string | null;
  staged: string | null;
}

export interface UpdateManifest {
  version: string;
  channel: string;
  notes: string;
  published_at: number | null;
  artifacts: { platform: string; file: string; sha256: string; size: number | null }[];
  source?: string;
  source_node?: string | null;
  source_name?: string | null;
}

export interface UpdateInstallPlan {
  mode: string;
  requires_admin: boolean;
  requires_operator: boolean;
  command: string;
  note: string;
}

export interface UpdateRevertPlan {
  available: boolean;
  kind?: string;
  path?: string;
  command?: string;
  note?: string;
  previous_installer?: string;
}

export interface UpdateHistoryEntry {
  ts: number;
  event: string;
  detail: string;
}

export interface UpdateStatus {
  state: string;
  current_version: string;
  latest_version: string | null;
  channel: string;
  platform: string;
  enabled: boolean;
  auto_check: boolean;
  auto_stage: boolean;
  auto_install: boolean;
  auto_install_supported: boolean;
  remote_install: boolean;
  remote_install_supported: boolean;
  check_interval_s: number;
  source_dir: string;
  checked_at: number | null;
  notes: string | null;
  published_at: number | null;
  error: string | null;
  progress: number;
  manifest: UpdateManifest | null;
  artifact: UpdateArtifact | null;
  install: UpdateInstallPlan | null;
  revert: UpdateRevertPlan;
  rollback_supported?: boolean;
  remote_result: Record<string, unknown> | null;
  history: UpdateHistoryEntry[];
}

export interface NodeDiagnostics {
  node_id: string;
  node_name: string;
  version: string;
  platform: string | null;
  os: string;
  hostname: string;
  python: string;
  frozen: boolean;
  uptime_s: number | null;
  cpu: { percent?: number; load?: number[]; cores_logical?: number };
  ram: { percent?: number; used?: number; total?: number };
  disk: { path?: string; total?: number; free?: number; used_percent?: number | null };
  service: {
    mode: string;
    name?: string;
    state?: string | null;
    ok?: boolean;
    helper_ready?: boolean;
    helper_can_rollback?: boolean;
  };
  units: { unit: string; active: string | null; enabled: string | null; ok: boolean }[];
  data_dir: string;
  db_bytes: number;
  logs_bytes: number;
  update: {
    state?: string;
    current_version?: string;
    latest_version?: string | null;
    auto_install?: boolean;
    remote_install?: boolean;
    rollback_supported?: boolean;
    error?: string | null;
  };
  tasks: { queued: number; running: number; completed: number; failed: number; cancelled: number };
  peers: { paired: number; online: number };
  collected_at: number;
}

export interface FleetRow {
  node_id: string;
  name: string;
  self: boolean;
  status: string;
  version: string;
  update: UpdateStatus | null;
  error: string | null;
  can_control: boolean;
}

export interface FleetPayload {
  rows: FleetRow[];
}

export interface FleetActionResult {
  ok: boolean;
  action: string;
  results: { node_id: string; name: string; ok: boolean; error?: string }[];
  rows: FleetRow[];
}

export interface ClipboardItem {
  id: string;
  ts: number;
  from_node: string;
  from_name: string;
  chars: number;
  copied: boolean;
  copied_at: number | null;
  copy_method: string | null;
  copy_error: string | null;
  preview: string;
  text?: string;
}

export interface ClipboardStatus {
  enabled: boolean;
  auto_copy: boolean;
  max_chars: number;
  keep_items: number;
  last_item: ClipboardItem | null;
}

export interface ClipboardInbox {
  items: ClipboardItem[];
  status: ClipboardStatus;
}

export interface ClipboardSendResult {
  ok: boolean;
  peer: string;
  peer_id: string;
  chars: number;
  copied: boolean;
  copy_method: string | null;
  copy_error: string | null;
}

export interface ClipboardBroadcastResult {
  ok: boolean;
  chars: number;
  sent: number;
  copied: number;
  results: Array<{
    peer_id: string;
    peer: string;
    ok: boolean;
    copied: boolean;
    copy_method: string | null;
    error: string | null;
  }>;
}

export interface ClipboardCopyResult {
  id?: string;
  copied: boolean;
  method: string | null;
  error?: string | null;
}

export interface ClipboardReadResult {
  text: string;
  chars: number;
}

let csrfToken: string | null = null;
export function setCsrf(token: string | null) {
  csrfToken = token;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === "object" && item && "msg" in item ? String((item as any).msg) : JSON.stringify(item)))
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return "request failed";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  // A stalled agent must surface as an error, not as a dashboard that hangs
  // forever with a spinner: abort anything unanswered after 20 s.
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 20000);
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      method,
      headers,
      credentials: "same-origin",
      signal: init.signal ?? controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError" && !init.signal) {
      throw new ApiError(0, "the agent did not answer within 20 s");
    }
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? (payload as any).detail : payload;
    throw new ApiError(response.status, formatDetail(detail));
  }
  return payload as T;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });

// -- formatting ------------------------------------------------------------
const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

export function fmtBytes(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value <= 0) return "0 B";
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), UNITS.length - 1);
  const scaled = value / Math.pow(1024, index);
  return `${scaled.toFixed(index === 0 ? 0 : scaled >= 100 ? 0 : digits)} ${UNITS[index]}`;
}

export function fmtRate(value: number | null | undefined): string {
  return `${fmtBytes(value ?? 0)}/s`;
}

export function fmtPct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

export function timeAgo(ts: number | null | undefined): string {
  if (!ts) return "never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 5) return "just now";
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function fmtClock(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function shortId(id: string | null | undefined): string {
  if (!id) return "—";
  return id.length > 16 ? `${id.slice(0, 10)}…${id.slice(-4)}` : id;
}

export function nodeLabel(
  nodeId: string | null | undefined,
  local: { node_id: string; name: string } | null | undefined,
  peers: { node_id: string; name: string }[] | null | undefined,
): string {
  if (!nodeId) return "unassigned";
  if (local && nodeId === local.node_id) return `${local.name} (this node)`;
  const peer = peers?.find((candidate) => candidate.node_id === nodeId);
  return peer?.name ?? shortId(nodeId);
}
