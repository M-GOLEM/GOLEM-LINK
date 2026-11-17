import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Files,
  ListChecks,
  MemoryStick,
  MonitorSmartphone,
  Network,
  Server,
  ShieldAlert,
  Timer,
} from "lucide-react";
import { useMemo } from "react";
import { fmtBytes, fmtDuration, fmtPct, nodeLabel, shortId, timeAgo, type Alert, type Cluster, type Metrics, type Overview, type Task } from "../api";
import { LiveChart } from "../components/charts";
import { NodeCard } from "../components/NodeCard";
import { Empty, Panel, Spinner, Stat, StatusDot, UsageBar, verdictTone } from "../components/ui";
import { usePolling } from "../hooks";
import { useLiveMetrics } from "../store";
import { get } from "../api";
import type { PageKey } from "../components/Shell";

export function OverviewPage({ onNavigate }: { onNavigate: (page: PageKey) => void }) {
  const liveMetrics = useLiveMetrics();
  const overviewQ = usePolling(() => get<Overview>("/api/overview"), 5000);
  const clusterQ = usePolling(() => get<Cluster>("/api/cluster"), 5000);
  const tasksQ = usePolling(() => get<{ tasks: Task[] }>("/api/tasks?limit=6"), 5000);
  const alertsQ = usePolling(() => get<{ alerts: Alert[] }>("/api/alerts?open_only=true&limit=5"), 5000);
  const historyQ = usePolling(() => get<{ samples: Metrics[] }>("/api/metrics/history?limit=90"), 10000);

  const overview = overviewQ.data;
  const cluster = clusterQ.data;

  const chartData = useMemo(() => {
    const samples = historyQ.data?.samples ?? [];
    const list = [...samples];
    if (liveMetrics && (!list.length || liveMetrics.ts > list[list.length - 1].ts)) list.push(liveMetrics);
    return list.slice(-120).map((sample) => ({
      label: new Date(sample.ts * 1000).toLocaleTimeString([], { minute: "2-digit", second: "2-digit" }),
      cpu: sample.cpu.percent,
      ram: sample.ram.percent,
    }));
  }, [historyQ.data, liveMetrics]);

  if (!overview && overviewQ.loading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <Spinner label="Connecting to the local agent…" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {overviewQ.error && (
        <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          Cannot load overview: {overviewQ.error}
        </div>
      )}

      {cluster && cluster.peers.length === 0 && (
        <div className="panel flex flex-wrap items-center gap-4 border-cyan-400/20 bg-gradient-to-r from-cyan-400/[0.07] to-transparent p-4">
          <span className="grid h-10 w-10 place-items-center rounded-xl border border-cyan-400/30 bg-cyan-400/10 text-cyan-300">
            <Network size={19} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="font-display text-sm font-semibold text-white">No second computer paired yet</div>
            <p className="mt-0.5 text-xs text-slate-400">
              This node is running and monitoring real hardware. Pair the other computer over your LAN to unlock
              distributed tasks, shared storage and the unified dashboard.
            </p>
          </div>
          <button className="btn-primary btn-xs" onClick={() => onNavigate("network")}>
            Pair a device <ArrowRight size={13} />
          </button>
        </div>
      )}

      <div className="grid-cards">
        <Stat
          label="Nodes online"
          value={`${overview?.nodes_online ?? "—"} / ${overview?.nodes_total ?? "—"}`}
          hint="Local agent plus paired peers currently reachable on the LAN."
          icon={MonitorSmartphone}
          tone="green"
        />
        <Stat
          label="Aggregate CPU cores"
          value={overview?.cores_total ?? "—"}
          hint={overview?.cores_note ?? "Aggregate of logical cores across online nodes."}
          icon={Server}
          tone="cyan"
        />
        <Stat
          label="Aggregate physical RAM"
          value={fmtBytes(overview?.ram_total ?? 0)}
          hint={
            overview
              ? `${fmtBytes(overview.ram_used)} in use across online nodes. ${overview.ram_note}`
              : undefined
          }
          icon={MemoryStick}
          tone="blue"
        />
        <Stat
          label="Approved storage free"
          value={fmtBytes(overview?.storage_free ?? 0)}
          hint={
            overview
              ? `${fmtBytes(overview.storage_total)} total on approved shares. ${overview.storage_note}.`
              : undefined
          }
          icon={Files}
          tone="green"
        />
      </div>

      <div className="grid-cards">
        <Stat label="Active tasks" value={overview?.tasks.running ?? 0} hint="Running or dispatching now." icon={Timer} tone="amber" />
        <Stat label="Queued" value={overview?.tasks.queued ?? 0} hint="Waiting for a suitable executor." icon={ListChecks} tone="cyan" />
        <Stat
          label="Failed"
          value={overview?.tasks.failed ?? 0}
          hint={`${overview?.tasks.completed ?? 0} completed · ${overview?.tasks.cancelled ?? 0} cancelled`}
          icon={AlertTriangle}
          tone="red"
        />
        <Stat
          label="Open alerts"
          value={overview?.alerts_open ?? 0}
          hint={
            overview?.network.avg_latency_ms != null
              ? `Average peer latency ${overview.network.avg_latency_ms.toFixed(0)} ms.`
              : "No peer latency measured yet."
          }
          icon={ShieldAlert}
          tone={overview?.alerts_open ? "amber" : "green"}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          title="Local resource telemetry"
          subtitle="Real psutil samples from this node. Peer cards show each peer's latest push."
          actions={
            <div className="flex items-center gap-3 text-[11px] text-slate-500">
              <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-cyan-400" /> CPU</span>
              <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-blue-400" /> RAM</span>
              <span className="inline-flex items-center gap-1.5"><span className="h-2 w-4 border-t border-dashed border-amber-400" /> 80%</span>
            </div>
          }
        >
          <LiveChart
            data={chartData}
            series={[
              { key: "cpu", label: "CPU", color: "#22d3ee" },
              { key: "ram", label: "RAM", color: "#3b82f6" },
            ]}
            height={240}
            warningAt={80}
            criticalAt={92}
          />
          {cluster?.local.metrics && (
            <div className="mt-4 grid grid-cols-2 gap-3 border-t border-ink-700/60 pt-4 text-xs sm:grid-cols-4">
              <div>
                <div className="label">CPU now</div>
                <div className="mt-1 font-mono text-slate-200">{fmtPct(cluster.local.metrics.cpu.percent, 1)}</div>
              </div>
              <div>
                <div className="label">RAM now</div>
                <div className="mt-1 font-mono text-slate-200">{fmtPct(cluster.local.metrics.ram.percent, 1)}</div>
              </div>
              <div>
                <div className="label">Network out/in</div>
                <div className="mt-1 font-mono text-slate-200">
                  {fmtBytes(cluster.local.metrics.net.send_rate)}/s · {fmtBytes(cluster.local.metrics.net.recv_rate)}/s
                </div>
              </div>
              <div>
                <div className="label">Uptime</div>
                <div className="mt-1 font-mono text-slate-200">{fmtDuration(cluster.local.metrics.uptime_s)}</div>
              </div>
            </div>
          )}
        </Panel>

        <div className="space-y-4">
          <div className="flex items-center justify-between px-1">
            <h2 className="section-title">Nodes</h2>
            <button className="text-[11px] text-cyan-400/80 hover:text-cyan-300" onClick={() => onNavigate("devices")}>
              Details
            </button>
          </div>
          {cluster ? (
            <>
              <NodeCard
                node={{
                  node_id: cluster.local.node_id,
                  name: cluster.local.name,
                  status: "online",
                  self: true,
                  metrics: liveMetrics ?? cluster.local.metrics,
                  pressure: cluster.local.pressure,
                }}
              />
              {cluster.peers.map((peer) => (
                <NodeCard key={peer.node_id} node={peer} />
              ))}
              {cluster.peers.length === 0 && (
                <div className="empty py-6 text-xs">
                  Paired devices appear here once connected. Use Network → Device pairing.
                </div>
              )}
            </>
          ) : (
            <Spinner />
          )}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          title="Recent tasks"
          subtitle="Newest jobs from the shared queue"
          actions={
            <button className="btn-ghost btn-xs" onClick={() => onNavigate("tasks")}>
              Task manager <ArrowRight size={12} />
            </button>
          }
          bodyClassName="p-0"
        >
          {tasksQ.data?.tasks?.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse">
                <thead>
                  <tr className="border-b border-ink-700/60">
                    <th className="th">Task</th>
                    <th className="th">Plugin</th>
                    <th className="th">Executor</th>
                    <th className="th">Status</th>
                    <th className="th">Progress</th>
                    <th className="th">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {tasksQ.data.tasks.map((task) => (
                    <tr key={task.id} className="row-hover border-b border-ink-700/40 last:border-0">
                      <td className="td">
                        <div className="max-w-[220px] truncate font-medium text-slate-200">{task.name}</div>
                        <div className="font-mono text-[10px] text-slate-600">{task.id}</div>
                      </td>
                      <td className="td font-mono text-xs text-slate-400">{task.plugin}</td>
                      <td className="td">
                        <span className="font-mono text-[11px] text-slate-400">
                          {nodeLabel(task.executor_node, cluster?.local, cluster?.peers)}
                        </span>
                      </td>
                      <td className="td">
                        <span className={task.status === "completed" ? "badge-ok" : task.status === "failed" ? "badge-bad" : task.status === "running" ? "badge-warn" : "badge-idle"}>
                          <StatusDot state={task.status} /> {task.status}
                        </span>
                      </td>
                      <td className="td w-[130px]">
                        <UsageBar percent={(task.progress || 0) * 100} tone="cyan" />
                        <div className="mt-1 font-mono text-[10px] text-slate-600">{Math.round((task.progress || 0) * 100)}%</div>
                      </td>
                      <td className="td text-xs text-slate-500">{timeAgo(task.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-4">
              <Empty icon={ListChecks} title="No tasks yet" hint="Create a job in the Task Manager; eligible work is scheduled to the least loaded node." />
            </div>
          )}
        </Panel>

        <Panel
          title="Open alerts"
          subtitle="Unacknowledged warnings and criticals"
          actions={
            <button className="btn-ghost btn-xs" onClick={() => onNavigate("alerts")}>
              All alerts <ArrowRight size={12} />
            </button>
          }
        >
          {alertsQ.data?.alerts?.length ? (
            <ul className="space-y-2.5">
              {alertsQ.data.alerts.map((alert) => (
                <li key={alert.id} className="rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className={verdictTone(alert.severity)}>{alert.severity}</span>
                    <span className="text-[10px] text-slate-600">{timeAgo(alert.ts)}</span>
                  </div>
                  <p className="mt-1.5 text-xs leading-relaxed text-slate-300">{alert.message}</p>
                  <div className="mt-1 font-mono text-[10px] text-slate-600">
                    {alert.category} · {shortId(alert.node_id)}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <CheckCircle2 size={22} className="text-emerald-400/70" />
              <p className="text-sm text-slate-400">All clear</p>
              <p className="text-[11px] text-slate-600">No unacknowledged alerts.</p>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
