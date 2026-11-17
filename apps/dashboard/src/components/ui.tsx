import { X, Copy, Check, type LucideIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useApp } from "../store";

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <linearGradient id="golem-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#22d3ee" />
          <stop offset="100%" stopColor="#3b82f6" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill="#0b0e13" stroke="#1b232e" />
      <path d="M32 11 51 22v20L32 53 13 42V22z" fill="none" stroke="url(#golem-grad)" strokeWidth="3" />
      <path d="M32 21 43 27.5v11L32 44.5 21 38.5v-11z" fill="none" stroke="#22d3ee" strokeOpacity="0.45" strokeWidth="1.5" />
      <circle cx="32" cy="32" r="4.5" fill="#22d3ee" />
      <circle cx="32" cy="32" r="8.5" fill="none" stroke="#22d3ee" strokeOpacity="0.3" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <div className="leading-none">
      <div className="font-display text-[15px] font-semibold tracking-[0.24em] text-white">GOLEM</div>
      <div className="mt-1 font-mono text-[10px] tracking-[0.42em] text-cyan-400/80">LINK</div>
    </div>
  );
}

export function StatusDot({ state }: { state: string }) {
  const tone =
    state === "online" || state === "open" || state === "completed" || state === "healthy"
      ? "bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.7)]"
      : state === "warning" || state === "running" || state === "connecting" || state === "dispatching"
        ? "bg-amber-400 shadow-[0_0_10px_rgba(251,191,36,0.6)]"
        : state === "critical" || state === "failed" || state === "closed"
          ? "bg-rose-400 shadow-[0_0_10px_rgba(251,113,133,0.6)]"
          : "bg-slate-500";
  return <span className={`inline-block h-2 w-2 rounded-full ${tone} ${state === "running" || state === "connecting" ? "animate-pulseSoft" : ""}`} />;
}

export function Stat({
  label,
  value,
  hint,
  icon: Icon,
  tone = "cyan",
  footer,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  icon?: LucideIcon;
  tone?: "cyan" | "green" | "amber" | "red" | "blue";
  footer?: ReactNode;
}) {
  const tones: Record<string, string> = {
    cyan: "border-cyan-400/20 bg-cyan-400/[0.06] text-cyan-300",
    green: "border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-300",
    amber: "border-amber-400/20 bg-amber-400/[0.06] text-amber-300",
    red: "border-rose-400/20 bg-rose-400/[0.06] text-rose-300",
    blue: "border-blue-400/20 bg-blue-400/[0.06] text-blue-300",
  };
  return (
    <div className="panel group animate-rise p-4 transition hover:border-ink-600">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="label">{label}</div>
          <div className="value mt-2 truncate">{value}</div>
        </div>
        {Icon && (
          <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg border ${tones[tone]}`}>
            <Icon size={17} />
          </span>
        )}
      </div>
      {hint && <p className="mt-2 text-[11px] leading-relaxed text-slate-500">{hint}</p>}
      {footer}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = "",
  bodyClassName = "p-4",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`panel animate-rise overflow-hidden ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700/60 px-4 py-3">
          <div>
            {typeof title === "string" ? <h2 className="section-title">{title}</h2> : title}
            {subtitle && <p className="mt-1 text-xs text-slate-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

export function UsageBar({
  percent,
  tone,
  className = "",
}: {
  percent: number;
  tone?: "auto" | "cyan" | "green";
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(percent, 100));
  const color =
    tone === "cyan"
      ? "bg-cyan-400"
      : tone === "green"
        ? "bg-emerald-400"
        : clamped >= 92
          ? "bg-rose-400"
          : clamped >= 80
            ? "bg-amber-400"
            : "bg-emerald-400";
  return (
    <div className={`h-1.5 w-full overflow-hidden rounded-full bg-ink-700/80 ${className}`}>
      <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${clamped}%` }} />
    </div>
  );
}

export function Modal({
  open,
  title,
  subtitle,
  onClose,
  children,
  width = "max-w-2xl",
  footer,
}: {
  open: boolean;
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  width?: string;
  footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm sm:items-center">
      <div className={`panel my-auto w-full ${width} animate-rise`} role="dialog" aria-modal="true">
        <header className="flex items-start justify-between gap-4 border-b border-ink-700/60 px-5 py-4">
          <div>
            <h2 className="font-display text-lg font-semibold text-white">{title}</h2>
            {subtitle && <p className="mt-1 text-xs text-slate-500">{subtitle}</p>}
          </div>
          <button className="btn-ghost btn-xs" onClick={onClose} aria-label="Close">
            <X size={14} />
          </button>
        </header>
        <div className="scroll-thin max-h-[72vh] overflow-y-auto px-5 py-5">{children}</div>
        {footer && <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-ink-700/60 px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <div className="mt-1.5">{children}</div>
      {hint && <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">{hint}</p>}
    </label>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <label className={`flex cursor-pointer items-start justify-between gap-4 rounded-xl border border-ink-700/60 bg-ink-900/50 px-3.5 py-3 transition hover:border-ink-600 ${disabled ? "opacity-50" : ""}`}>
      <span>
        <span className="block text-sm text-slate-200">{label}</span>
        {hint && <span className="mt-0.5 block text-[11px] leading-relaxed text-slate-500">{hint}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-6 w-11 shrink-0 rounded-full border transition ${
          checked ? "border-cyan-400/60 bg-cyan-400/25" : "border-ink-600 bg-ink-700"
        }`}
      >
        <span
          className={`absolute top-0.5 h-[18px] w-[18px] rounded-full transition-all ${
            checked ? "left-[22px] bg-cyan-300" : "left-0.5 bg-slate-400"
          }`}
        />
      </button>
    </label>
  );
}

export function CopyText({ value, className = "" }: { value: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={`group inline-flex items-center gap-1.5 rounded border border-transparent px-1 py-0.5 font-mono text-[11px] text-slate-400 transition hover:border-ink-600 hover:text-cyan-300 ${className}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1400);
        } catch {
          /* clipboard permission may be denied */
        }
      }}
      title="Copy"
    >
      <span className="max-w-[260px] truncate">{value}</span>
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} className="opacity-50 group-hover:opacity-100" />}
    </button>
  );
}

export function Empty({ icon: Icon, title, hint }: { icon?: LucideIcon; title: string; hint?: string }) {
  return (
    <div className="empty">
      {Icon && <Icon size={22} className="text-slate-600" />}
      <p className="font-medium text-slate-400">{title}</p>
      {hint && <p className="max-w-md text-xs text-slate-600">{hint}</p>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-slate-500">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-cyan-400/30 border-t-cyan-400" />
      {label || "Loading"}
    </div>
  );
}

export function Toasts() {
  const { toasts, dismissToast } = useApp();
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(360px,calc(100vw-2rem))] flex-col gap-2">
      {toasts.map((item) => (
        <button
          key={item.id}
          onClick={() => dismissToast(item.id)}
          className={`pointer-events-auto animate-rise rounded-xl border px-3.5 py-2.5 text-left text-sm shadow-panel backdrop-blur ${
            item.kind === "ok"
              ? "border-emerald-400/30 bg-emerald-950/80 text-emerald-200"
              : item.kind === "error"
                ? "border-rose-400/30 bg-rose-950/80 text-rose-200"
                : "border-cyan-400/30 bg-ink-850/95 text-slate-200"
          }`}
        >
          {item.message}
        </button>
      ))}
    </div>
  );
}

export function verdictTone(severity: string) {
  if (severity === "critical") return "badge-bad";
  if (severity === "warning") return "badge-warn";
  return "badge-info";
}
