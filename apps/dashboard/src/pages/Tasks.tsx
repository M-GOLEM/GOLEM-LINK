import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Cpu,
  ListChecks,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Server,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  fmtDuration,
  fmtPct,
  get,
  nodeLabel,
  post,
  timeAgo,
  type Cluster,
  type Peer,
  type PluginInfo,
  type Share,
  type Task,
} from "../api";
import { Empty, Field, Modal, Panel, Spinner, Stat, StatusDot, UsageBar } from "../components/ui";
import { usePolling } from "../hooks";
import { useApp } from "../store";

const STATUS_FILTERS = ["all", "queued", "dispatching", "running", "completed", "failed", "cancelled"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

const PATH_PLUGINS = new Set(["file_hash", "text_stats", "compress", "image_resize"]);

interface FormState {
  name: string;
  plugin: string;
  priority: number;
  timeout_s: string;
  max_retries: string;
  target_node: string;
  share_id: string;
  path: string;
  dest_share_id: string;
  dest_path: string;
  out_share_id: string;
  out_path: string;
  format: string;
  max_width: number;
  max_height: number;
  quality: number;
  size_mb: number;
  script: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  plugin: "file_hash",
  priority: 5,
  timeout_s: "",
  max_retries: "",
  target_node: "",
  share_id: "",
  path: "",
  dest_share_id: "",
  dest_path: "",
  out_share_id: "",
  out_path: "",
  format: "zip",
  max_width: 1920,
  max_height: 1080,
  quality: 85,
  size_mb: 64,
  script: "",
};

function statusBadge(status: Task["status"]) {
  if (status === "completed") return "badge-ok";
  if (status === "failed") return "badge-bad";
  if (status === "running" || status === "dispatching") return "badge-warn";
  return "badge-idle";
}

function TaskDetail({
  taskId,
  peers,
  onClose,
}: {
  taskId: string | null;
  peers: Peer[];
  onClose: () => void;
}) {
  const { me, refresh, toast } = useApp();
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const canWrite = me?.role === "admin";

  useEffect(() => {
    if (!taskId) {
      setTask(null);
      return;
    }
    let cancelled = false;
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const data = await get<Task>(`/api/tasks/${taskId}`);
        if (!cancelled) {
          setTask(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "could not load task");
      } finally {
        inFlight = false;
      }
    };
    void load();
    const handle = window.setInterval(load, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [taskId]);

  const act = async (action: "cancel" | "retry") => {
    if (!taskId) return;
    setBusy(action);
    try {
      await post(`/api/tasks/${taskId}/${action}`);
      toast(action === "cancel" ? "Cancellation requested" : "Task re-queued", "ok");
      const data = await get<Task>(`/api/tasks/${taskId}`);
      setTask(data);
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : `${action} failed`, "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal
      open={taskId !== null}
      title={task?.name ?? "Task"}
      subtitle={taskId ?? undefined}
      onClose={onClose}
      width="max-w-3xl"
      footer={
        task ? (
          <>
            {task.can_cancel && (
              <button className="btn-danger btn-xs" onClick={() => void act("cancel")} disabled={!canWrite || busy !== null}>
                <X size={12} /> Cancel task
              </button>
            )}
            {task.can_retry && (
              <button className="btn-ghost btn-xs" onClick={() => void act("retry")} disabled={!canWrite || busy !== null}>
                <RotateCcw size={12} /> Retry
              </button>
            )}
            <button className="btn-ghost btn-xs" onClick={onClose}>Close</button>
          </>
        ) : (
          <button className="btn-ghost btn-xs" onClick={onClose}>Close</button>
        )
      }
    >
      {error && <div className="mb-3 rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{error}</div>}
      {!task ? (
        <Spinner label="Loading task…" />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="panel-quiet px-3 py-2">
              <div className="label">Status</div>
              <div className={`mt-1 ${statusBadge(task.status)}`}>
                <StatusDot state={task.status} /> {task.status}
              </div>
            </div>
            <div className="panel-quiet px-3 py-2">
              <div className="label">Plugin</div>
              <div className="mt-1 font-mono text-xs text-slate-300">{task.plugin}</div>
            </div>
            <div className="panel-quiet px-3 py-2">
              <div className="label">Priority</div>
              <div className="mt-1 font-mono text-xs text-slate-300">{task.priority}</div>
            </div>
            <div className="panel-quiet px-3 py-2">
              <div className="label">Progress</div>
              <div className="mt-1 font-mono text-xs text-slate-300">{fmtPct((task.progress || 0) * 100, 0)}</div>
            </div>
          </div>
          <UsageBar percent={(task.progress || 0) * 100} tone={task.status === "failed" ? "auto" : "cyan"} />

          <div className="grid gap-2 text-xs sm:grid-cols-2">
            {[
              ["Source node", nodeLabel(task.source_node, me ? { node_id: me.node_id, name: me.node_name } : null, peers)],
              ["Executor", nodeLabel(task.executor_node, me ? { node_id: me.node_id, name: me.node_name } : null, peers)],
              ["Created", timeAgo(task.created_at)],
              ["Started", task.started_at ? timeAgo(task.started_at) : "—"],
              ["Finished", task.finished_at ? timeAgo(task.finished_at) : "—"],
              ["Duration", fmtDuration((task.finished_at ?? Date.now() / 1000) - (task.started_at ?? task.created_at))],
              ["Timeout", `${task.timeout_s}s`],
              ["Retries", `${task.retries} / ${task.max_retries}`],
              ["Authorized by", task.authorized_by ?? "—"],
              ["Target", task.target_node ? nodeLabel(task.target_node, me ? { node_id: me.node_id, name: me.node_name } : null, peers) : "auto"],
            ].map(([label, value]) => (
              <div key={label} className="flex items-center justify-between gap-2 border-b border-ink-700/40 py-1.5">
                <span className="text-slate-500">{label}</span>
                <span className="truncate font-mono text-[11px] text-slate-300">{value}</span>
              </div>
            ))}
          </div>

          {task.error && (
            <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs leading-relaxed text-rose-200">
              {task.error}
            </div>
          )}

          <div>
            <div className="label mb-1.5">Specification</div>
            <pre className="scroll-thin max-h-44 overflow-auto rounded-lg border border-ink-700/60 bg-ink-950/80 p-3 font-mono text-[10px] leading-relaxed text-slate-400">
              {JSON.stringify(task.spec, null, 2)}
            </pre>
          </div>

          {task.events && task.events.length > 0 && (
            <div>
              <div className="label mb-1.5">Event log</div>
              <ol className="space-y-1.5">
                {task.events.map((event) => (
                  <li key={event.id} className="flex items-start gap-2 text-[11px]">
                    <span className="w-14 shrink-0 font-mono text-slate-600">{new Date(event.ts * 1000).toLocaleTimeString()}</span>
                    <span className="badge-idle shrink-0">{event.event}</span>
                    {event.detail && <span className="min-w-0 break-words text-slate-500">{event.detail}</span>}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {task.result && (
            <div>
              <div className="label mb-1.5">Result</div>
              <pre className="scroll-thin max-h-72 overflow-auto rounded-lg border border-emerald-400/20 bg-emerald-950/20 p-3 font-mono text-[10px] leading-relaxed text-emerald-100/80">
                {JSON.stringify(task.result, null, 2)}
              </pre>
            </div>
          )}

          {task.resource_profile && (
            <div>
              <div className="label mb-1.5">Executor resource footprint</div>
              <pre className="rounded-lg border border-ink-700/60 bg-ink-950/80 p-3 font-mono text-[10px] text-slate-500">
                {JSON.stringify(task.resource_profile, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

export function TasksPage() {
  const { me, refresh, toast } = useApp();
  const [status, setStatus] = useState<StatusFilter>("all");
  const [detailId, setDetailId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const canWrite = me?.role === "admin";

  const query = status === "all" ? "/api/tasks?limit=200" : `/api/tasks?limit=200&status=${status}`;
  const tasksQ = usePolling(() => get<{ tasks: Task[] }>(query), 4000);
  const pluginsQ = usePolling(() => get<{ plugins: PluginInfo[] }>("/api/plugins"), 30000);
  const sharesQ = usePolling(() => get<{ shares: Share[] }>("/api/storage/shares"), 30000);
  const clusterQ = usePolling(() => get<Cluster>("/api/cluster"), 15000);

  const tasks = tasksQ.data?.tasks ?? [];
  const plugins = pluginsQ.data?.plugins ?? [];
  const shares = (sharesQ.data?.shares ?? []).filter((share) => share.exists);
  const selectedPlugin = plugins.find((plugin) => plugin.name === form.plugin);

  const counts = useMemo(() => {
    const all = tasks;
    return {
      active: all.filter((task) => task.status === "running" || task.status === "dispatching").length,
      queued: all.filter((task) => task.status === "queued").length,
      completed: all.filter((task) => task.status === "completed").length,
      failed: all.filter((task) => task.status === "failed").length,
    };
  }, [tasks]);

  const openCreate = () => {
    const firstShare = shares[0]?.id ?? "";
    const firstPlugin = plugins.find((plugin) => plugin.enabled && !plugin.runs_user_code)?.name ?? "file_hash";
    setForm({
      ...EMPTY_FORM,
      share_id: firstShare,
      dest_share_id: firstShare,
      out_share_id: firstShare,
      plugin: firstPlugin,
    });
    setFormError(null);
    setCreateOpen(true);
  };

  const buildSpec = (): Record<string, unknown> => {
    if (form.plugin === "disk_benchmark") return { size_mb: Number(form.size_mb) };
    if (form.plugin === "python") return { script: form.script };
    const spec: Record<string, unknown> = { share_id: form.share_id, path: form.path };
    if (form.plugin === "compress") {
      spec.dest_share_id = form.dest_share_id || form.share_id;
      spec.dest_path = form.dest_path;
      spec.format = form.format;
    }
    if (form.plugin === "image_resize") {
      spec.out_share_id = form.out_share_id || form.share_id;
      spec.out_path = form.out_path;
      spec.max_width = Number(form.max_width);
      spec.max_height = Number(form.max_height);
      spec.quality = Number(form.quality);
      spec.format = "same";
    }
    return spec;
  };

  const submit = async () => {
    if (!form.name.trim()) {
      setFormError("Give the task a name.");
      return;
    }
    if (PATH_PLUGINS.has(form.plugin) && !form.share_id) {
      setFormError("Choose an approved share for the input path.");
      return;
    }
    if (form.plugin === "compress" && !form.dest_path.trim()) {
      setFormError("Compression needs a destination file name inside a share.");
      return;
    }
    if (form.plugin === "python" && !form.script.trim()) {
      setFormError("Paste the Python script to run.");
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await post<Task>("/api/tasks", {
        name: form.name.trim(),
        plugin: form.plugin,
        spec: buildSpec(),
        priority: Number(form.priority),
        timeout_s: form.timeout_s ? Number(form.timeout_s) : null,
        max_retries: form.max_retries ? Number(form.max_retries) : null,
        target_node: form.target_node || null,
      });
      toast("Task submitted to the scheduler", "ok");
      setCreateOpen(false);
      refresh();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "submission failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid-cards">
        <Stat label="Active" value={counts.active} hint="Running or dispatching right now." icon={Cpu} tone="amber" />
        <Stat label="Queued" value={counts.queued} hint="Waiting for an eligible executor." icon={Clock} tone="cyan" />
        <Stat label="Completed" value={counts.completed} hint="Results returned and stored in history." icon={CheckCircle2} tone="green" />
        <Stat label="Failed" value={counts.failed} hint="Failures keep their error and retry count." icon={AlertTriangle} tone="red" />
      </div>

      <Panel
        title="Distributed task queue"
        subtitle="Jobs are validated, sandboxed to approved shares and executed in isolated worker subprocesses."
        actions={
          <>
            <button className="btn-ghost btn-xs" onClick={refresh}>
              <RefreshCw size={12} /> Refresh
            </button>
            <button className="btn-primary btn-xs" onClick={openCreate} disabled={!canWrite}>
              <Plus size={12} /> New task
            </button>
          </>
        }
        bodyClassName="p-0"
      >
        <div className="flex flex-wrap gap-1 border-b border-ink-700/60 px-3 py-2">
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter}
              onClick={() => setStatus(filter)}
              className={`rounded-full px-2.5 py-1 text-[11px] capitalize transition ${
                status === filter ? "bg-cyan-400/15 text-cyan-200" : "text-slate-500 hover:bg-ink-800 hover:text-slate-300"
              }`}
            >
              {filter}
            </button>
          ))}
        </div>
        {tasksQ.error && (
          <div className="m-3 rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{tasksQ.error}</div>
        )}
        {tasks.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] border-collapse">
              <thead>
                <tr className="border-b border-ink-700/60">
                  <th className="th">Task</th>
                  <th className="th">Plugin</th>
                  <th className="th">Executor</th>
                  <th className="th">Status</th>
                  <th className="th">Progress</th>
                  <th className="th">Priority</th>
                  <th className="th">Created</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id} className="row-hover cursor-pointer border-b border-ink-700/40 last:border-0" onClick={() => setDetailId(task.id)}>
                    <td className="td">
                      <div className="max-w-[240px] truncate font-medium text-slate-200">{task.name}</div>
                      <div className="font-mono text-[10px] text-slate-600">{task.id}</div>
                    </td>
                    <td className="td font-mono text-xs text-slate-400">{task.plugin}</td>
                    <td className="td">
                      <span className="font-mono text-[11px] text-slate-400">{nodeLabel(task.executor_node, me ? { node_id: me.node_id, name: me.node_name } : null, clusterQ.data?.peers)}</span>
                      {task.is_remote && <span className="badge-info ml-1.5">remote</span>}
                    </td>
                    <td className="td">
                      <span className={statusBadge(task.status)}>
                        <StatusDot state={task.status} /> {task.status}
                      </span>
                    </td>
                    <td className="td w-[120px]">
                      <UsageBar percent={(task.progress || 0) * 100} tone="cyan" />
                      <div className="mt-1 font-mono text-[10px] text-slate-600">{Math.round((task.progress || 0) * 100)}%</div>
                    </td>
                    <td className="td font-mono text-[11px] text-slate-500">{task.priority}</td>
                    <td className="td text-[11px] text-slate-500">{timeAgo(task.created_at)}</td>
                    <td className="td text-right">
                      <ArrowRight size={13} className="text-slate-600" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4">
            {tasksQ.loading ? (
              <Spinner label="Loading task history…" />
            ) : (
              <Empty
                icon={ListChecks}
                title={status === "all" ? "No tasks yet" : `No ${status} tasks`}
                hint="Create a job to hash files, compress a directory, resize images, benchmark a disk or run an approved script."
              />
            )}
          </div>
        )}
      </Panel>

      <div className="rounded-xl border border-ink-700/60 bg-ink-900/40 px-4 py-3 text-[11px] leading-relaxed text-slate-500">
        <Server size={12} className="mr-1 inline" /> Scheduling uses the least-loaded eligible node (CPU 55%, RAM 35%, temperature
        component). OS processes are never migrated; running tasks cannot be moved, only cancelled and retried. Remote execution
        requires the receiving peer to authorize this node.
      </div>

      <TaskDetail taskId={detailId} peers={clusterQ.data?.peers ?? []} onClose={() => setDetailId(null)} />

      <Modal
        open={createOpen}
        title="Submit a distributed task"
        subtitle="Only approved share roots are reachable from a job. Paths escape attempts are rejected by the worker."
        onClose={() => setCreateOpen(false)}
        width="max-w-2xl"
        footer={
          <>
            <button className="btn-ghost btn-xs" onClick={() => setCreateOpen(false)}>Cancel</button>
            <button className="btn-primary btn-xs" onClick={() => void submit()} disabled={submitting}>
              {submitting ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Submit task
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Task name">
              <input className="input" value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="Hash the projects folder" />
            </Field>
            <Field label="Plugin">
              <select
                className="select"
                value={form.plugin}
                onChange={(event) => setForm((current) => ({ ...current, plugin: event.target.value }))}
              >
                {plugins.map((plugin) => (
                  <option key={plugin.name} value={plugin.name} disabled={!plugin.enabled}>
                    {plugin.name}
                    {plugin.requires.length ? ` (requires ${plugin.requires.join(", ")})` : ""}
                    {!plugin.available ? " — unavailable" : !plugin.enabled ? " — disabled" : ""}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {selectedPlugin && (
            <p className="rounded-lg border border-ink-700/60 bg-ink-900/50 px-3 py-2 text-[11px] leading-relaxed text-slate-500">
              {selectedPlugin.description}
              {selectedPlugin.runs_user_code && (
                <span className="ml-1 text-amber-300">Runs user code — enabled explicitly on the executor only.</span>
              )}
            </p>
          )}

          {PATH_PLUGINS.has(form.plugin) && (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Input share">
                  <select
                    className="select"
                    value={form.share_id}
                    onChange={(event) => setForm((current) => ({ ...current, share_id: event.target.value }))}
                  >
                    <option value="">Select a share…</option>
                    {shares.map((share) => (
                      <option key={share.id} value={share.id}>{share.name} — {share.path}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Path inside the share" hint="Empty means the share root. Absolute paths and .. are rejected.">
                  <input className="input font-mono" value={form.path} onChange={(event) => setForm((current) => ({ ...current, path: event.target.value }))} placeholder="subfolder/file.txt" />
                </Field>
              </div>
              {!shares.length && (
                <p className="text-xs text-amber-300/90">No approved shares yet. Approve one in Storage first.</p>
              )}
            </>
          )}

          {form.plugin === "compress" && (
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Destination share">
                <select className="select" value={form.dest_share_id} onChange={(event) => setForm((current) => ({ ...current, dest_share_id: event.target.value }))}>
                  {shares.map((share) => (
                    <option key={share.id} value={share.id}>{share.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="Archive path">
                <input className="input font-mono" value={form.dest_path} onChange={(event) => setForm((current) => ({ ...current, dest_path: event.target.value }))} placeholder="backup.zip" />
              </Field>
              <Field label="Format">
                <select className="select" value={form.format} onChange={(event) => setForm((current) => ({ ...current, format: event.target.value }))}>
                  <option value="zip">zip</option>
                  <option value="tar.gz">tar.gz</option>
                  <option value="tar">tar</option>
                </select>
              </Field>
            </div>
          )}

          {form.plugin === "image_resize" && (
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Output share">
                <select className="select" value={form.out_share_id} onChange={(event) => setForm((current) => ({ ...current, out_share_id: event.target.value }))}>
                  {shares.map((share) => (
                    <option key={share.id} value={share.id}>{share.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="Output folder">
                <input className="input font-mono" value={form.out_path} onChange={(event) => setForm((current) => ({ ...current, out_path: event.target.value }))} placeholder="resized" />
              </Field>
              <Field label="Max width / height">
                <div className="flex gap-2">
                  <input className="input" type="number" min={1} value={form.max_width} onChange={(event) => setForm((current) => ({ ...current, max_width: Number(event.target.value) }))} />
                  <input className="input" type="number" min={1} value={form.max_height} onChange={(event) => setForm((current) => ({ ...current, max_height: Number(event.target.value) }))} />
                </div>
              </Field>
              <Field label="JPEG quality">
                <input className="input" type="number" min={1} max={100} value={form.quality} onChange={(event) => setForm((current) => ({ ...current, quality: Number(event.target.value) }))} />
              </Field>
            </div>
          )}

          {form.plugin === "disk_benchmark" && (
            <Field label="Test size (MiB)" hint="A temporary file is written, read and deleted on the executor.">
              <input className="input" type="number" min={1} max={1024} value={form.size_mb} onChange={(event) => setForm((current) => ({ ...current, size_mb: Number(event.target.value) }))} />
            </Field>
          )}

          {form.plugin === "python" && (
            <Field label="Python script" hint="Runs in a separate process with a minimal environment. Enabled only where the operator allows custom Python.">
              <textarea
                className="input min-h-[180px] font-mono text-xs"
                value={form.script}
                onChange={(event) => setForm((current) => ({ ...current, script: event.target.value }))}
                placeholder={'print("hello from", __import__("platform").node())'}
                spellCheck={false}
              />
            </Field>
          )}

          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Priority" hint="0 = low, 100 = urgent">
              <input className="input" type="number" min={0} max={100} value={form.priority} onChange={(event) => setForm((current) => ({ ...current, priority: Number(event.target.value) }))} />
            </Field>
            <Field label="Timeout (s)">
              <input className="input" type="number" min={1} placeholder="default" value={form.timeout_s} onChange={(event) => setForm((current) => ({ ...current, timeout_s: event.target.value }))} />
            </Field>
            <Field label="Max retries">
              <input className="input" type="number" min={0} max={10} placeholder="default" value={form.max_retries} onChange={(event) => setForm((current) => ({ ...current, max_retries: event.target.value }))} />
            </Field>
            <Field label="Target node">
              <select className="select" value={form.target_node} onChange={(event) => setForm((current) => ({ ...current, target_node: event.target.value }))}>
                <option value="">Auto (least loaded)</option>
                <option value={me?.node_id ?? "local"}>This node</option>
                {(clusterQ.data?.peers ?? []).map((peer) => (
                  <option key={peer.node_id} value={peer.node_id}>
                    {peer.name}{peer.remote_accepts_tasks ? "" : " (tasks not granted)"}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          {formError && <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{formError}</div>}
        </div>
      </Modal>
    </div>
  );
}
