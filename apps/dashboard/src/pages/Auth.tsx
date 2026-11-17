import { KeyRound, LockKeyhole, ShieldCheck, Sparkles } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { get, post } from "../api";
import { Logo, Wordmark } from "../components/ui";
import { useApp } from "../store";

function AuthLayout({ children, footer }: { children: ReactNode; footer: ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <aside className="relative hidden overflow-hidden border-r border-ink-700/60 bg-ink-950/60 lg:flex lg:flex-col lg:justify-between lg:p-10">
        <div className="pointer-events-none absolute inset-0 opacity-70 [background-image:radial-gradient(700px_420px_at_30%_20%,rgba(34,211,238,0.14),transparent_60%),radial-gradient(600px_360px_at_80%_90%,rgba(59,130,246,0.12),transparent_60%)]" />
        <div className="relative flex items-center gap-3">
          <Logo size={42} />
          <div>
            <Wordmark />
            <div className="mt-1 text-[10px] uppercase tracking-[0.32em] text-slate-600">Unified workstation</div>
          </div>
        </div>
        <div className="relative max-w-lg">
          <h1 className="font-display text-4xl font-semibold leading-tight text-white">
            Two machines.
            <br />
            One control plane.
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-slate-400">
            GOLEM LINK links your Debian and Windows computers over the local network: live hardware telemetry,
            distributed jobs, approved storage and shared input. Nothing leaves the LAN.
          </p>
          <ul className="mt-8 space-y-3 text-sm text-slate-400">
            {[
              "Peer-to-peer, no hardcoded master node",
              "Certificate-pinned TLS with Ed25519 identities",
              "Real psutil telemetry — no simulated readings",
              "Scoped permissions per device, revocable at any time",
            ].map((line) => (
              <li key={line} className="flex items-start gap-2.5">
                <ShieldCheck size={15} className="mt-0.5 shrink-0 text-cyan-400/80" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="relative font-mono text-[10px] tracking-[0.18em] text-slate-700">
          LAN ONLY · PORT 8080 · NO CLOUD RELAY
        </div>
      </aside>

      <main className="flex min-h-screen flex-col justify-center px-5 py-10 sm:px-10">
        <div className="mx-auto w-full max-w-md animate-rise">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <Logo size={36} />
            <Wordmark />
          </div>
          {children}
          <div className="mt-8">{footer}</div>
        </div>
      </main>
    </div>
  );
}

export function BootScreen() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-5">
      <div className="relative animate-pulseSoft">
        <Logo size={52} />
      </div>
      <div className="text-center">
        <div className="font-mono text-[11px] uppercase tracking-[0.4em] text-cyan-400/70">GOLEM LINK</div>
        <div className="mt-2 text-xs text-slate-500">Contacting the local agent…</div>
      </div>
      <div className="h-0.5 w-40 overflow-hidden rounded-full bg-ink-700">
        <div className="h-full w-1/2 animate-[scan_1.6s_ease-in-out_infinite] bg-cyan-400/80" />
      </div>
    </div>
  );
}

export function LoginScreen() {
  const { onAuthenticated, enterReady, toast } = useApp();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await post<{ role: string }>("/api/auth/login", { password });
      await onAuthenticated();
      enterReady(false);
      toast(`Signed in as ${result.role}`, "ok");
    } catch (err) {
      setError(err instanceof Error ? err.message : "sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout
      footer={
        <p className="text-center text-[11px] leading-relaxed text-slate-600">
          Access is restricted to devices on this local network. Dashboard writes require the admin password.
        </p>
      }
    >
      <div className="flex items-center gap-2 text-cyan-400/80">
        <KeyRound size={16} />
        <span className="font-mono text-[10px] uppercase tracking-[0.28em]">Secure session</span>
      </div>
      <h2 className="mt-3 font-display text-2xl font-semibold text-white">Sign in</h2>
      <p className="mt-1 text-sm text-slate-500">Enter the dashboard password configured on this node.</p>

      <form onSubmit={submit} className="mt-7 space-y-4">
        <label className="block">
          <span className="label">Password</span>
          <input
            className="input mt-1.5"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="••••••••"
          />
        </label>
        {error && (
          <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{error}</div>
        )}
        <button className="btn-primary w-full" type="submit" disabled={busy || !password}>
          {busy ? "Verifying…" : "Unlock dashboard"}
        </button>
      </form>
    </AuthLayout>
  );
}

export function SetupScreen() {
  const { onAuthenticated, enterReady, toast } = useApp();
  const [nodeName, setNodeName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<{ node_name: string }>("/api/setup/status")
      .then((data) => setNodeName((current) => current || data.node_name || ""))
      .catch(() => undefined);
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (password.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await post("/api/setup", { node_name: nodeName, password });
      await onAuthenticated();
      enterReady(true);
      toast("Node configured — welcome to GOLEM LINK", "ok");
    } catch (err) {
      setError(err instanceof Error ? err.message : "setup failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout
      footer={
        <p className="text-center text-[11px] leading-relaxed text-slate-600">
          First run only. The admin password is stored as a salted scrypt hash; there is no default password.
        </p>
      }
    >
      <div className="flex items-center gap-2 text-cyan-400/80">
        <Sparkles size={16} />
        <span className="font-mono text-[10px] uppercase tracking-[0.28em]">First run</span>
      </div>
      <h2 className="mt-3 font-display text-2xl font-semibold text-white">Set up this node</h2>
      <p className="mt-1 text-sm text-slate-500">
        Name this computer and choose the admin password for its dashboard. You can change both later.
      </p>

      <form onSubmit={submit} className="mt-7 space-y-4">
        <label className="block">
          <span className="label">Node name</span>
          <input
            className="input mt-1.5"
            value={nodeName}
            maxLength={64}
            onChange={(event) => setNodeName(event.target.value)}
            placeholder="workshop-desktop"
          />
          <p className="mt-1.5 text-[11px] text-slate-500">Shown on the other computer when pairing.</p>
        </label>
        <label className="block">
          <span className="label">Admin password</span>
          <input
            className="input mt-1.5"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="At least 8 characters"
          />
        </label>
        <label className="block">
          <span className="label">Confirm password</span>
          <input
            className="input mt-1.5"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            placeholder="Repeat the password"
          />
        </label>
        {error && (
          <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{error}</div>
        )}
        <button className="btn-primary w-full" type="submit" disabled={busy || !password || !confirm}>
          <LockKeyhole size={15} />
          {busy ? "Configuring…" : "Create node & continue"}
        </button>
      </form>
    </AuthLayout>
  );
}
