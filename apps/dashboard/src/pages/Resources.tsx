import {
  Activity,
  Cpu,
  Flame,
  Gauge,
  HardDrive,
  MemoryStick,
  MonitorSmartphone,
  Network,
  Thermometer,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  fmtBytes,
  fmtDuration,
  fmtPct,
  fmtRate,
  get,
  timeAgo,
  type Cluster,
  type Metrics,
  type SettingsPayload,
} from "../api";
import { LiveChart } from "../components/charts";
import { hottestTemp, maxTemp } from "../components/NodeCard";
import { Empty, Panel, Spinner, UsageBar } from "../components/ui";
import { usePolling } from "../hooks";
import { useLiveMetrics } from "../store";

export function ResourcesPage() {
  const liveMetrics = useLiveMetrics();
  const clusterQ = usePolling(() => get<Cluster>("/api/cluster"), 5000);
  const historyQ = usePolling(() => get<{ samples: Metrics[] }>("/api/metrics/history?limit=150"), 10000);
  const settingsQ = usePolling(() => get<SettingsPayload>("/api/settings"), 30000);
  const [selected, setSelected] = useState<string | null>(null);

  const cluster = clusterQ.data;
  const thresholds = settingsQ.data?.config.thresholds;
  const local = liveMetrics ?? cluster?.local.metrics ?? null;
  const peers = cluster?.peers ?? [];
  const onlinePeers = peers.filter((peer) => peer.metrics);
  const peerId = selected ?? onlinePeers[0]?.node_id ?? null;
  const peer = peers.find((item) => item.node_id === peerId) ?? null;
  const shown: Metrics | null = peer?.metrics ?? local;
  const hottest = hottestTemp(shown);

  const history = useMemo(() => {
    const samples = historyQ.data?.samples ?? [];
    const list = [...samples];
    if (local && (!list.length || local.ts > list[list.length - 1].ts)) list.push(local);
    return list.slice(-150).map((sample) => ({
      label: new Date(sample.ts * 1000).toLocaleTimeString([], { minute: "2-digit", second: "2-digit" }),
      cpu: sample.cpu.percent,
      ram: sample.ram.percent,
      temp: maxTemp(sample) ?? undefined,
    }));
  }, [historyQ.data, local]);

  if (!cluster && clusterQ.loading) {
    return <div className="flex min-h-[40vh] items-center justify-center"><Spinner label="Opening telemetry streams…" /></div>;
  }

  return (
    <div className="space-y-4">
      {clusterQ.error && (
        <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          Cannot load telemetry: {clusterQ.error}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          <button
            className={`btn btn-xs ${peerId === null ? "border-cyan-400/40 bg-cyan-400/10 text-cyan-300" : "border-ink-600/80 bg-ink-800/60 text-slate-400"}`}
            onClick={() => setSelected(null)}
          >
            <MonitorSmartphone size={12} /> This node
          </button>
          {peers.map((item) => (
            <button
              key={item.node_id}
              className={`btn btn-xs ${peerId === item.node_id ? "border-cyan-400/40 bg-cyan-400/10 text-cyan-300" : "border-ink-600/80 bg-ink-800/60 text-slate-400"}`}
              onClick={() => setSelected(item.node_id)}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${item.metrics ? "bg-emerald-400" : "bg-slate-600"}`} />
              {item.name}
            </button>
          ))}
        </div>
        <div className="text-[11px] text-slate-500">
          {shown ? (
            <>
              sample {timeAgo(shown.ts)} · {fmtDuration(shown.uptime_s)} uptime · {shown.processes} processes
            </>
          ) : (
            "waiting for a telemetry sample"
          )}
        </div>
      </div>

      <div className="grid-cards">
        <div className="panel p-4">
          <div className="flex items-center justify-between">
            <div className="label">CPU utilisation</div>
            <Cpu size={15} className="text-cyan-400/80" />
          </div>
          <div className="value mt-2">{fmtPct(shown?.cpu.percent ?? null, 1)}</div>
          <UsageBar className="mt-3" percent={shown?.cpu.percent ?? 0} />
          <div className="mt-2 flex justify-between text-[10px] text-slate-600">
            <span>{shown ? `${shown.cpu.cores_logical} logical cores` : "—"}</span>
            <span>{shown?.cpu.frequency_mhz ? `${Math.round(shown.cpu.frequency_mhz)} MHz` : ""}</span>
          </div>
        </div>
        <div className="panel p-4">
          <div className="flex items-center justify-between">
            <div className="label">Memory</div>
            <MemoryStick size={15} className="text-blue-400/80" />
          </div>
          <div className="value mt-2">{fmtPct(shown?.ram.percent ?? null, 1)}</div>
          <UsageBar className="mt-3" percent={shown?.ram.percent ?? 0} />
          <div className="mt-2 flex justify-between text-[10px] text-slate-600">
            <span>{shown ? `${fmtBytes(shown.ram.used)} used` : "—"}</span>
            <span>{shown ? `${fmtBytes(shown.ram.total)} total` : ""}</span>
          </div>
        </div>
        <div className="panel p-4">
          <div className="flex items-center justify-between">
            <div className="label">Hottest sensor</div>
            <Thermometer size={15} className="text-rose-400/80" />
          </div>
          <div className="value mt-2">{hottest ? `${hottest.value.toFixed(0)} °C` : "—"}</div>
          <p className="mt-2 text-[10px] leading-relaxed text-slate-600">
            {hottest
              ? hottest.gpu
                ? `GPU sensor (${hottest.label}) — ${shown?.temp_note ?? "no CPU sensor exposed"}`
                : `${shown?.temps.length} platform sensors reported`
              : shown?.temp_note ?? "No temperature source is available on this node."}
          </p>
        </div>
        <div className="panel p-4">
          <div className="flex items-center justify-between">
            <div className="label">Disk throughput</div>
            <Zap size={15} className="text-amber-400/80" />
          </div>
          <div className="mt-2 space-y-1 font-mono text-xs text-slate-300">
            <div>read {shown ? fmtRate(shown.disk_io.read_rate) : "—"}</div>
            <div>write {shown ? fmtRate(shown.disk_io.write_rate) : "—"}</div>
          </div>
          <p className="mt-2 text-[10px] text-slate-600">Net {shown ? `${fmtRate(shown.net.send_rate)} out · ${fmtRate(shown.net.recv_rate)} in` : "—"}</p>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          title="CPU & memory history"
          subtitle={peer ? `Latest push from ${peer.name}; history is recorded on this node only.` : "Recorded on this node by the resource monitor."}
          actions={
            <div className="flex items-center gap-3 text-[11px] text-slate-500">
              <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-cyan-400" /> CPU</span>
              <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-blue-400" /> RAM</span>
              {thresholds && <span className="inline-flex items-center gap-1.5"><span className="h-2 w-4 border-t border-dashed border-amber-400" /> {thresholds.cpu_warn_percent}%</span>}
            </div>
          }
        >
          <LiveChart
            data={history}
            series={[
              { key: "cpu", label: "CPU", color: "#22d3ee" },
              { key: "ram", label: "RAM", color: "#3b82f6" },
            ]}
            height={260}
            warningAt={thresholds?.cpu_warn_percent ?? 80}
            criticalAt={thresholds?.cpu_crit_percent ?? 92}
          />
        </Panel>

        <Panel title="Load average" subtitle="1 / 5 / 15 minute run queue">
          {shown && shown.cpu.load.length >= 3 ? (
            <div className="space-y-3">
              {["1 min", "5 min", "15 min"].map((label, index) => {
                const value = shown.cpu.load[index] ?? 0;
                const cores = Math.max(shown.cpu.cores_logical, 1);
                return (
                  <div key={label}>
                    <div className="flex items-center justify-between text-[11px] text-slate-500">
                      <span>{label}</span>
                      <span className="font-mono text-slate-300">{value.toFixed(2)}</span>
                    </div>
                    <UsageBar className="mt-1.5" percent={(value / cores) * 100} tone="cyan" />
                  </div>
                );
              })}
              <p className="text-[10px] leading-relaxed text-slate-600">
                Bars are normalised by {shown.cpu.cores_logical} logical cores — the platform load average, not a synthetic score.
              </p>
            </div>
          ) : (
            <Empty icon={Activity} title="No load average" hint="Windows does not expose a Unix load average; per-core utilisation is still shown below." />
          )}
        </Panel>
      </div>

      <Panel
        title={
          <div className="section-title">
            <Flame size={13} className="text-amber-400" /> Heavy processes
          </div>
        }
        subtitle={peer ? `Largest consumers reported by ${peer.name} in the latest push` : "Largest consumers on this machine from the latest sample"}
      >
        {shown?.top_cpu?.length || shown?.top_mem?.length ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <div className="label mb-2">Top CPU</div>
                <div className="space-y-1.5">
                  {(shown.top_cpu ?? []).map((entry) => (
                    <div key={`cpu-${entry.pid}`} className="flex items-center gap-2 rounded-lg border border-ink-700/60 bg-ink-900/50 px-2.5 py-1.5">
                      <span className="min-w-0 flex-1 truncate text-xs text-slate-300">{entry.name}</span>
                      <span className="font-mono text-[9px] text-slate-600">pid {entry.pid}</span>
                      <span className="w-14 text-right font-mono text-[11px] text-amber-300">{entry.cpu.toFixed(0)}%</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div className="label mb-2">Top memory</div>
                <div className="space-y-1.5">
                  {(shown.top_mem ?? []).map((entry) => (
                    <div key={`mem-${entry.pid}`} className="flex items-center gap-2 rounded-lg border border-ink-700/60 bg-ink-900/50 px-2.5 py-1.5">
                      <span className="min-w-0 flex-1 truncate text-xs text-slate-300">{entry.name}</span>
                      <span className="font-mono text-[9px] text-slate-600">pid {entry.pid}</span>
                      <span className="w-16 text-right font-mono text-[11px] text-blue-300">{fmtBytes(entry.memory)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <p className="mt-3 text-[10px] leading-relaxed text-slate-600">
              CPU can exceed 100% — it is summed across cores. When this machine crosses its host-light limits, the scheduler places new GOLEM tasks on the other machine. GOLEM LINK never kills or migrates operating-system processes.
            </p>
          </>
        ) : (
          <Empty icon={Flame} title="No process samples yet" hint="Process names, CPU and memory are read from psutil on every two-second sample." />
        )}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Per-core utilisation" subtitle="psutil per-CPU percentages from the latest sample">
          {shown?.cpu.per_core.length ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {shown.cpu.per_core.map((percent, index) => (
                <div key={index} className="rounded-lg border border-ink-700/60 bg-ink-900/50 px-2.5 py-2">
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="text-slate-500">core {index}</span>
                    <span className="font-mono text-slate-300">{percent.toFixed(0)}%</span>
                  </div>
                  <UsageBar className="mt-1.5" percent={percent} />
                </div>
              ))}
            </div>
          ) : (
            <Empty icon={Cpu} title="No per-core samples" />
          )}
        </Panel>

        <Panel title="Temperatures" subtitle="Only hardware-reported sensors; limits come from the sensor or your thresholds">
          {shown?.temps.length ? (
            <div className="space-y-2">
              {shown.temps.map((entry) => {
                const critical = entry.critical ?? thresholds?.temp_crit_c ?? null;
                const warn = entry.high ?? thresholds?.temp_warn_c ?? null;
                const tone = critical != null && entry.current >= critical ? "text-rose-300" : warn != null && entry.current >= warn ? "text-amber-300" : "text-slate-300";
                return (
                  <div key={`${entry.chip}-${entry.label}`} className="flex items-center gap-3 rounded-lg border border-ink-700/60 bg-ink-900/50 px-3 py-2">
                    <Thermometer size={13} className={tone} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs text-slate-200">{entry.label}</div>
                      <div className="truncate font-mono text-[10px] text-slate-600">{entry.chip}</div>
                    </div>
                    <div className={`font-mono text-sm ${tone}`}>{entry.current.toFixed(1)} °C</div>
                    <div className="text-right font-mono text-[9px] text-slate-600">
                      {entry.high != null && <div>high {entry.high.toFixed(0)}</div>}
                      {entry.critical != null && <div>crit {entry.critical.toFixed(0)}</div>}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <Empty
              icon={Thermometer}
              title="No CPU sensors reported"
              hint={
                hottest?.gpu
                  ? "The GPU temperature is shown in the Hottest sensor card and the GPU panel. The agent never fabricates a CPU reading."
                  : shown?.temp_note ?? "The agent never fabricates temperatures."
              }
            />
          )}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Disks" subtitle="Mounted filesystems visible to the agent" bodyClassName="p-0">
          {shown?.disk.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] border-collapse">
                <thead>
                  <tr className="border-b border-ink-700/60">
                    <th className="th">Mount</th>
                    <th className="th">Used</th>
                    <th className="th">Free</th>
                    <th className="th">Usage</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.disk.map((disk) => (
                    <tr key={disk.mountpoint} className="border-b border-ink-700/40 last:border-0">
                      <td className="td">
                        <div className="flex items-center gap-1.5 font-mono text-xs text-slate-300">
                          <HardDrive size={12} className="text-slate-600" /> {disk.mountpoint}
                        </div>
                        <div className="font-mono text-[10px] text-slate-600">{disk.fstype} · {disk.device}</div>
                      </td>
                      <td className="td font-mono text-[11px] text-slate-400">{fmtBytes(disk.used)}</td>
                      <td className="td font-mono text-[11px] text-slate-400">{fmtBytes(disk.free)}</td>
                      <td className="td w-40">
                        <UsageBar percent={disk.percent} />
                        <div className="mt-1 font-mono text-[10px] text-slate-600">{disk.percent.toFixed(0)}%</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-4"><Empty icon={HardDrive} title="No disks reported" /></div>
          )}
        </Panel>

        <Panel title="Network & GPUs" subtitle="Interface rates and any GPU psutil can see">
          <div className="space-y-3">
            {shown?.net.interfaces.length ? (
              shown.net.interfaces.map((iface) => (
                <div key={iface.name} className="flex items-center gap-3 rounded-lg border border-ink-700/60 bg-ink-900/50 px-3 py-2">
                  <Network size={13} className="text-cyan-400/70" />
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-300">{iface.name}</span>
                  <span className="font-mono text-[10px] text-slate-500">
                    ↑{fmtRate(iface.send_rate)} ↓{fmtRate(iface.recv_rate)}
                  </span>
                  <span className={iface.is_up === false ? "badge-bad" : "badge-ok"}>{iface.is_up === false ? "down" : "up"}</span>
                </div>
              ))
            ) : (
              <p className="text-xs text-slate-500">No interfaces reported.</p>
            )}
            {shown?.gpu.length ? (
              shown.gpu.map((gpu) => (
                <div key={gpu.name} className="rounded-lg border border-ink-700/60 bg-ink-900/50 px-3 py-2">
                  <div className="flex items-center justify-between">
                    <span className="truncate text-xs text-slate-200">{gpu.name}</span>
                    {gpu.utilization != null && <span className="font-mono text-[11px] text-slate-300">{gpu.utilization.toFixed(0)}%</span>}
                  </div>
                  <div className="mt-1 flex gap-3 font-mono text-[10px] text-slate-600">
                    {gpu.memory_total != null && <span>{fmtBytes(gpu.memory_used ?? 0)} / {fmtBytes(gpu.memory_total)}</span>}
                    {gpu.temperature != null && <span>{gpu.temperature.toFixed(0)} °C</span>}
                    {gpu.power != null && <span>{gpu.power.toFixed(0)} W</span>}
                  </div>
                </div>
              ))
            ) : (
              <p className="text-xs text-slate-600">No GPU telemetry available through psutil on this platform.</p>
            )}
          </div>
        </Panel>
      </div>

      {peer && (
        <div className="rounded-xl border border-cyan-400/20 bg-cyan-400/[0.04] px-4 py-3 text-[11px] leading-relaxed text-cyan-100/80">
          <Gauge size={12} className="mr-1 inline" /> Viewing {peer.name}: these numbers are pushed over the authenticated peer
          WebSocket every couple of seconds. They describe that machine independently — cores and memory are aggregated for display
          only and are never presented as one merged computer.
        </div>
      )}
    </div>
  );
}
