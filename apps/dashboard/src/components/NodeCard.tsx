import { Cpu, MemoryStick, MonitorSmartphone, Thermometer, Wifi } from "lucide-react";
import { fmtBytes, fmtPct, timeAgo, type Metrics, type Pressure } from "../api";
import { StatusDot, UsageBar } from "./ui";

export interface NodeCardData {
  node_id: string;
  name: string;
  os?: string;
  status?: string;
  address?: string;
  latency_ms?: number | null;
  last_seen?: number | null;
  metrics: Metrics | null;
  pressure: Pressure | null;
  self?: boolean;
}

export interface HottestTemp {
  value: number;
  label: string;
  gpu: boolean;
}

export function hottestTemp(metrics: Metrics | null): HottestTemp | null {
  if (!metrics) return null;
  if (metrics.temps?.length) {
    const hottest = metrics.temps.reduce((a, b) => (b.current > a.current ? b : a));
    return { value: hottest.current, label: hottest.label, gpu: false };
  }
  const gpus = (metrics.gpu ?? []).filter((gpu) => gpu.temperature != null);
  if (gpus.length) {
    const hottest = gpus.reduce((a, b) => ((b.temperature ?? 0) > (a.temperature ?? 0) ? b : a));
    return { value: hottest.temperature as number, label: hottest.name, gpu: true };
  }
  return null;
}

export function maxTemp(metrics: Metrics | null): number | null {
  return hottestTemp(metrics)?.value ?? null;
}

export function NodeCard({ node }: { node: NodeCardData }) {
  const metrics = node.metrics;
  const online = node.self || node.status === "online";
  const temp = hottestTemp(metrics);
  return (
    <div className={`panel p-4 transition ${online ? "" : "opacity-70"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-ink-600/70 bg-ink-800 text-cyan-300">
            <MonitorSmartphone size={17} />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate font-display text-sm font-semibold text-white">{node.name}</span>
              {node.self && <span className="badge-info">this node</span>}
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-500">
              <StatusDot state={online ? "online" : "offline"} />
              <span>{node.self ? "local agent" : online ? "online" : node.last_seen ? `offline · ${timeAgo(node.last_seen)}` : "offline"}</span>
              {node.os && <span className="truncate">· {node.os}</span>}
            </div>
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-[10px] text-slate-500">{node.pressure ? node.pressure.score.toFixed(0) : "—"}</div>
          <div className="text-[9px] uppercase tracking-wider text-slate-600">load score</div>
        </div>
      </div>

      <div className="mt-4 space-y-2.5">
        <div>
          <div className="flex items-center justify-between text-[11px]">
            <span className="inline-flex items-center gap-1.5 text-slate-400">
              <Cpu size={12} className="text-cyan-400/70" /> CPU
            </span>
            <span className="font-mono text-slate-300">{fmtPct(metrics?.cpu.percent ?? null)}</span>
          </div>
          <UsageBar className="mt-1.5" percent={metrics?.cpu.percent ?? 0} />
        </div>
        <div>
          <div className="flex items-center justify-between text-[11px]">
            <span className="inline-flex items-center gap-1.5 text-slate-400">
              <MemoryStick size={12} className="text-blue-400/70" /> RAM
            </span>
            <span className="font-mono text-slate-300">
              {metrics ? `${fmtBytes(metrics.ram.used)} / ${fmtBytes(metrics.ram.total)}` : "—"}
            </span>
          </div>
          <UsageBar className="mt-1.5" percent={metrics?.ram.percent ?? 0} />
        </div>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 border-t border-ink-700/60 pt-3 text-[11px]">
        <div>
          <div className="inline-flex items-center gap-1 text-slate-500">
            <Thermometer size={11} /> temp
          </div>
          <div
            className="mt-0.5 font-mono text-slate-300"
            title={temp ? `${temp.label}${temp.gpu ? " (GPU sensor)" : ""}` : metrics?.temp_note ?? "no sensor available"}
          >
            {temp === null ? "—" : `${temp.value.toFixed(0)}°C${temp.gpu ? " GPU" : ""}`}
          </div>
        </div>
        <div>
          <div className="inline-flex items-center gap-1 text-slate-500">
            <Wifi size={11} /> latency
          </div>
          <div className="mt-0.5 font-mono text-slate-300">{node.latency_ms != null ? `${node.latency_ms.toFixed(0)} ms` : node.self ? "local" : "—"}</div>
        </div>
        <div>
          <div className="text-slate-500">cores</div>
          <div className="mt-0.5 font-mono text-slate-300">{metrics?.cpu.cores_logical ?? "—"}</div>
        </div>
      </div>
      {node.address && (
        <div className="mt-2 truncate font-mono text-[10px] text-slate-600">
          {node.address}
          {node.latency_ms != null ? "" : ""}
        </div>
      )}
    </div>
  );
}
