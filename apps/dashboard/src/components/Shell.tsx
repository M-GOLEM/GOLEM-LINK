import {
  Activity,
  Bell,
  ClipboardCopy,
  CloudDownload,
  Cpu,
  HardDrive,
  LayoutDashboard,
  ListTree,
  LogOut,
  Menu,
  MonitorSmartphone,
  Network,
  Settings as SettingsIcon,
  ShieldCheck,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { get, type Overview } from "../api";
import { useApp } from "../store";
import { Logo, StatusDot, Wordmark } from "./ui";

export type PageKey =
  | "overview"
  | "devices"
  | "resources"
  | "tasks"
  | "storage"
  | "network"
  | "clipboard"
  | "alerts"
  | "updates"
  | "settings";

export const PAGE_TITLES: Record<PageKey, { title: string; subtitle: string }> = {
  overview: { title: "Overview", subtitle: "Cluster health, capacity and live workload" },
  devices: { title: "Devices", subtitle: "Paired computers, hardware and permissions" },
  resources: { title: "Resource Monitor", subtitle: "Real-time CPU, memory, thermal, disk and network telemetry" },
  tasks: { title: "Task Manager", subtitle: "Distributed jobs, execution targets and results" },
  storage: { title: "Storage Manager", subtitle: "Approved shares, remote browsing and transfers" },
  network: { title: "Network Manager", subtitle: "Discovery, pairing, latency and interfaces" },
  clipboard: { title: "Text Relay", subtitle: "Send a copied text to another paired computer's clipboard" },
  alerts: { title: "Alert Center", subtitle: "Overload, thermal, storage, network and task alerts" },
  updates: { title: "Updates", subtitle: "Verified releases, automatic installs and fleet maintenance" },
  settings: { title: "Settings", subtitle: "Scheduling, thresholds, security, input sharing and diagnostics" },
};

const NAV: { key: PageKey; label: string; icon: LucideIcon }[] = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "devices", label: "Devices", icon: MonitorSmartphone },
  { key: "resources", label: "Resources", icon: Activity },
  { key: "tasks", label: "Tasks", icon: ListTree },
  { key: "storage", label: "Storage", icon: HardDrive },
  { key: "network", label: "Network", icon: Network },
  { key: "clipboard", label: "Text Relay", icon: ClipboardCopy },
  { key: "alerts", label: "Alerts", icon: Bell },
  { key: "updates", label: "Updates", icon: CloudDownload },
  { key: "settings", label: "Settings", icon: SettingsIcon },
];

export function Shell({
  page,
  onNavigate,
  children,
}: {
  page: PageKey;
  onNavigate: (page: PageKey) => void;
  children: React.ReactNode;
}) {
  const { me, wsState, logout, refreshToken } = useApp();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    const load = () => {
      if (inFlight) return;
      inFlight = true;
      get<Overview>("/api/overview")
        .then((data) => {
          if (!cancelled) setOverview(data);
        })
        .catch(() => undefined)
        .finally(() => {
          inFlight = false;
        });
    };
    load();
    const handle = window.setInterval(load, 12000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [refreshToken]);

  const navigate = (key: PageKey) => {
    onNavigate(key);
    setMobileOpen(false);
  };

  const nav = (
    <nav className="flex flex-1 flex-col gap-1 px-2.5" aria-label="Main">
      {NAV.map(({ key, label, icon: Icon }) => {
        const active = page === key;
        return (
          <button
            key={key}
            onClick={() => navigate(key)}
            className={`group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${
              active
                ? "bg-cyan-400/10 text-cyan-200"
                : "text-slate-400 hover:bg-ink-800/70 hover:text-slate-100"
            }`}
            aria-current={active ? "page" : undefined}
          >
            <span
              className={`absolute left-0 top-1/2 h-5 w-[2px] -translate-y-1/2 rounded-full transition ${
                active ? "bg-cyan-400 shadow-[0_0_12px_rgba(34,211,238,0.8)]" : "bg-transparent"
              }`}
            />
            <Icon size={17} className={active ? "text-cyan-300" : "text-slate-500 group-hover:text-slate-300"} />
            <span className="flex-1 text-left font-medium">{label}</span>
            {key === "alerts" && !!overview?.alerts_open && (
              <span className="rounded-full bg-rose-500/15 px-1.5 py-0.5 font-mono text-[10px] text-rose-300">
                {overview.alerts_open}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );

  const sidebarBody = (
    <>
      <div className="flex items-center gap-3 px-5 pb-5 pt-5">
        <Logo size={34} />
        <Wordmark />
      </div>
      <div className="mb-4 px-4">
        <div className="rounded-xl border border-ink-700/60 bg-ink-900/60 px-3 py-2.5">
          <div className="flex items-center gap-2">
            <StatusDot state={wsState === "open" ? "online" : "warning"} />
            <span className="truncate text-sm font-medium text-slate-200">{me?.node_name || "node"}</span>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="truncate font-mono text-[10px] text-slate-500">{me?.node_id}</span>
            <span className="ml-auto badge-idle uppercase">{me?.role}</span>
          </div>
        </div>
      </div>
      {nav}
      <div className="mt-auto space-y-3 px-4 pb-5 pt-4">
        <div className="flex items-center justify-between text-[11px] text-slate-500">
          <span className="inline-flex items-center gap-1.5">
            <ShieldCheck size={12} className="text-emerald-400/80" /> LAN only
          </span>
          <span className="font-mono">v{me?.version ?? "1.2.0"}</span>
        </div>
        <div className="flex items-center justify-between gap-2 rounded-lg border border-ink-700/60 bg-ink-900/50 px-2.5 py-2">
          <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-500">
            <Cpu size={12} /> {overview ? `${overview.nodes_online}/${overview.nodes_total} online` : "…"}
          </span>
          <button className="btn-ghost btn-xs" onClick={() => logout()} title="Sign out">
            <LogOut size={13} />
          </button>
        </div>
      </div>
    </>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-ink-700/60 bg-ink-950/80 backdrop-blur-xl lg:flex">
        {sidebarBody}
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button className="absolute inset-0 bg-black/70" aria-label="Close menu" onClick={() => setMobileOpen(false)} />
          <aside className="relative flex h-full w-64 flex-col border-r border-ink-700/60 bg-ink-950">
            <button className="absolute right-3 top-4 btn-ghost btn-xs" onClick={() => setMobileOpen(false)} aria-label="Close">
              <X size={14} />
            </button>
            {sidebarBody}
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col lg:pl-60">
        <header className="sticky top-0 z-20 border-b border-ink-700/50 bg-ink-950/75 backdrop-blur-xl">
          <div className="flex items-center gap-3 px-4 py-3 sm:px-6">
            <button className="btn-ghost btn-xs lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu">
              <Menu size={16} />
            </button>
            <div className="min-w-0">
              <h1 className="truncate font-display text-lg font-semibold tracking-tight text-white">{PAGE_TITLES[page].title}</h1>
              <p className="hidden truncate text-xs text-slate-500 sm:block">{PAGE_TITLES[page].subtitle}</p>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <div className="hidden items-center gap-2 rounded-lg border border-ink-700/60 bg-ink-900/60 px-3 py-1.5 sm:flex">
                <StatusDot state={wsState === "open" ? "online" : wsState === "connecting" ? "warning" : "critical"} />
                <span className="text-[11px] text-slate-400">{wsState === "open" ? "Live" : wsState === "connecting" ? "Connecting" : "Reconnecting"}</span>
              </div>
              <button
                onClick={() => navigate("alerts")}
                className="relative rounded-lg border border-ink-700/60 bg-ink-900/60 p-2 text-slate-400 transition hover:border-ink-500 hover:text-white"
                aria-label={`Alerts${overview?.alerts_open ? ` (${overview.alerts_open} open)` : ""}`}
              >
                <Bell size={16} />
                {!!overview?.alerts_open && (
                  <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-rose-500 px-1 font-mono text-[9px] text-white">
                    {overview.alerts_open > 99 ? "99+" : overview.alerts_open}
                  </span>
                )}
              </button>
            </div>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-5 sm:px-6 sm:py-6">{children}</main>
        <footer className="px-6 pb-5 pt-2 text-center text-[10px] tracking-wide text-slate-700">
          GOLEM LINK · local network only · peer traffic is certificate-pinned TLS ·{" "}
          <span className="font-mono">{me?.fingerprint}</span>
        </footer>
      </div>
    </div>
  );
}
