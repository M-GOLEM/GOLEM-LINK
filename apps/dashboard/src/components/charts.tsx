import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Metrics } from "../api";

export interface Series {
  key: string;
  label: string;
  color: string;
}

const axisStyle = { fontSize: 10, fill: "#64748b" } as const;

export function LiveChart({
  data,
  series,
  height = 220,
  domain = [0, 100] as [number, number],
  unit = "%",
  warningAt,
  criticalAt,
  showTime = true,
}: {
  data: Record<string, unknown>[];
  series: Series[];
  height?: number;
  domain?: [number, number];
  unit?: string;
  warningAt?: number;
  criticalAt?: number;
  showTime?: boolean;
}) {
  if (!data.length) {
    return (
      <div className="empty" style={{ height }}>
        Waiting for the first telemetry samples…
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 10, left: -18, bottom: 0 }}>
        <defs>
          {series.map((entry) => (
            <linearGradient key={entry.key} id={`grad-${entry.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={entry.color} stopOpacity={0.32} />
              <stop offset="100%" stopColor={entry.color} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid stroke="#1b232e" strokeDasharray="3 5" vertical={false} />
        <XAxis
          dataKey="label"
          tick={axisStyle}
          stroke="#27313d"
          tickLine={false}
          axisLine={false}
          minTickGap={40}
          hide={!showTime}
        />
        <YAxis tick={axisStyle} stroke="#27313d" tickLine={false} axisLine={false} domain={domain} width={46} />
        <Tooltip
          contentStyle={{
            background: "#0e1218",
            border: "1px solid #27313d",
            borderRadius: 10,
            fontSize: 12,
            color: "#e2e8f0",
          }}
          labelStyle={{ color: "#94a3b8", fontSize: 11 }}
          formatter={(value: number, name: string) => [`${Number(value).toFixed(1)}${unit}`, name]}
        />
        {warningAt !== undefined && (
          <ReferenceLine y={warningAt} stroke="#fbbf24" strokeDasharray="4 6" strokeOpacity={0.6} />
        )}
        {criticalAt !== undefined && (
          <ReferenceLine y={criticalAt} stroke="#fb7185" strokeDasharray="4 6" strokeOpacity={0.6} />
        )}
        {series.map((entry) => (
          <Area
            key={entry.key}
            type="monotone"
            dataKey={entry.key}
            name={entry.label}
            stroke={entry.color}
            strokeWidth={1.8}
            fill={`url(#grad-${entry.key})`}
            isAnimationActive={false}
            dot={false}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function Sparkline({
  data,
  color = "#22d3ee",
  height = 34,
}: {
  data: { x: number; y: number }[];
  color?: string;
  height?: number;
}) {
  if (data.length < 2) return <div style={{ height }} />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={`spark-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="y"
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#spark-${color.replace("#", "")})`}
          isAnimationActive={false}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function historyPoints(metrics: Metrics[], pick: (m: Metrics) => number) {
  return metrics.map((sample, index) => ({ x: index, y: pick(sample) }));
}
