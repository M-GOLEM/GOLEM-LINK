import {
  ArrowRight,
  Check,
  KeyRound,
  Laptop,
  Radar,
  RadioTower,
  ShieldQuestion,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { get, post, shortId, timeAgo, type Discovered, type PairBeginResult, type PairPollResult, type PairRequest } from "../api";
import { usePolling } from "../hooks";
import { useApp } from "../store";
import { CopyText, Empty, Modal, Spinner, StatusDot } from "./ui";

export function PairingFlow({ onChanged }: { onChanged?: () => void }) {
  const { me, refresh, toast } = useApp();
  const discoveredQ = usePolling(() => get<{ discovered: Discovered[] }>("/api/pairing/discovered"), 4000);
  const requestsQ = usePolling(() => get<{ requests: PairRequest[] }>("/api/pairing/requests"), 4000);
  const [address, setAddress] = useState("");
  const [active, setActive] = useState<PairBeginResult | null>(null);
  const [pairState, setPairState] = useState("pending");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isAdmin = me?.role === "admin";

  const begin = async (nodeId?: string, addr?: string) => {
    setBusy(nodeId || addr || "manual");
    setError(null);
    try {
      if (addr) await post("/api/pairing/manual-peer", { address: addr });
      const result = await post<PairBeginResult>(
        "/api/pairing/begin",
        nodeId ? { node_id: nodeId } : { address: addr },
      );
      setActive(result);
      setPairState("pending");
    } catch (err) {
      setError(err instanceof Error ? err.message : "pairing could not start");
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    if (!active) return;
    let stopped = false;
    const tick = async () => {
      try {
        const result = await get<PairPollResult>(`/api/pairing/poll/${active.request_id}`);
        if (stopped) return;
        setPairState(result.status);
        if (result.status === "approved") {
          toast(`${active.name} is now paired`, "ok");
          setActive(null);
          refresh();
          onChanged?.();
        } else if (result.status === "denied" || result.status === "expired") {
          setActive(null);
          toast(`Pairing ${result.status} on the other node`, "error");
        }
      } catch (err) {
        if (!stopped) {
          setError(err instanceof Error ? err.message : "pairing poll failed");
          setActive(null);
        }
      }
    };
    tick();
    const handle = window.setInterval(tick, 2500);
    return () => {
      stopped = true;
      window.clearInterval(handle);
    };
  }, [active, onChanged, refresh, toast]);

  const cancelOutgoing = async () => {
    if (!active) return;
    try {
      await post(`/api/pairing/cancel/${active.request_id}`);
    } catch {
      /* the request may already be completed */
    }
    setActive(null);
  };

  const decide = async (request: PairRequest, approve: boolean) => {
    setBusy(request.id);
    try {
      await post(`/api/pairing/requests/${request.id}`, { approve });
      toast(
        approve ? `${request.name} approved — exchanged keys and stored certificates` : `${request.name} denied`,
        approve ? "ok" : "info",
      );
      refresh();
      onChanged?.();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not answer the request", "error");
    } finally {
      setBusy(null);
    }
  };

  const discovered = (discoveredQ.data?.discovered ?? []).filter((node) => !node.paired);
  const requests = requestsQ.data?.requests ?? [];

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{error}</div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="panel">
          <header className="flex items-center justify-between border-b border-ink-700/60 px-4 py-3">
            <div>
              <h2 className="section-title">
                <Radar size={14} className="text-cyan-400" /> Discovered on this LAN
              </h2>
              <p className="mt-1 text-[11px] text-slate-500">mDNS and UDP beacon announcements, refreshed every few seconds.</p>
            </div>
            <span className="badge-idle">{discovered.length}</span>
          </header>
          <div className="space-y-2 p-3">
            {discoveredQ.loading && !discoveredQ.data ? (
              <div className="p-3"><Spinner label="Scanning the local network…" /></div>
            ) : discovered.length ? (
              discovered.map((node) => (
                <div key={node.node_id} className="flex items-center gap-3 rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5">
                  <span className="grid h-9 w-9 place-items-center rounded-lg border border-ink-600/70 bg-ink-800 text-cyan-300">
                    <Laptop size={16} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-slate-100">{node.name}</div>
                    <div className="mt-0.5 truncate font-mono text-[10px] text-slate-500">
                      {node.address}:{node.port} · {node.os || "unknown OS"} · {node.source}
                    </div>
                  </div>
                  <button
                    className="btn-primary btn-xs"
                    disabled={!isAdmin || busy === node.node_id}
                    onClick={() => begin(node.node_id)}
                    title={isAdmin ? "Send a signed pairing request" : "Admin role required"}
                  >
                    Pair <ArrowRight size={12} />
                  </button>
                </div>
              ))
            ) : (
              <Empty
                icon={RadioTower}
                title="No unpaired nodes announced"
                hint="Start GOLEM LINK on the other computer. If multicast is blocked on your router, pair by LAN IP below."
              />
            )}
            <div className="flex gap-2 pt-1">
              <input
                className="input"
                placeholder="192.168.1.42 or 192.168.1.42:8443"
                value={address}
                onChange={(event) => setAddress(event.target.value)}
              />
              <button
                className="btn-ghost shrink-0"
                disabled={!isAdmin || address.trim().length < 7 || busy !== null}
                onClick={() => begin(undefined, address.trim())}
              >
                {busy === address.trim() ? "Contacting…" : "Pair by IP"}
              </button>
            </div>
          </div>
        </section>

        <section className="panel">
          <header className="flex items-center justify-between border-b border-ink-700/60 px-4 py-3">
            <div>
              <h2 className="section-title">
                <ShieldQuestion size={14} className="text-amber-400" /> Incoming requests
              </h2>
              <p className="mt-1 text-[11px] text-slate-500">
                Compare the 6-digit code on both screens before approving. Keys are only exchanged after approval.
              </p>
            </div>
            <span className={`badge ${requests.length ? "badge-warn" : "badge-idle"}`}>{requests.length}</span>
          </header>
          <div className="space-y-2 p-3">
            {requests.length ? (
              requests.map((request) => (
                <div key={request.id} className="rounded-xl border border-amber-400/25 bg-amber-400/[0.04] px-3 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-slate-100">{request.name}</div>
                      <div className="mt-0.5 font-mono text-[10px] text-slate-500">
                        {request.os || "unknown OS"} · {request.address} · {timeAgo(request.created_at)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-lg font-semibold tracking-[0.28em] text-amber-300">{request.code}</div>
                      <div className="text-[9px] uppercase tracking-wider text-slate-600">pairing code</div>
                    </div>
                  </div>
                  <div className="mt-2 flex items-center gap-2 font-mono text-[10px] text-slate-600">
                    <KeyRound size={11} /> {request.fingerprint}
                    <CopyText value={request.fingerprint} />
                  </div>
                  <div className="mt-3 flex gap-2">
                    <button className="btn-primary btn-xs flex-1" disabled={!isAdmin || busy === request.id} onClick={() => decide(request, true)}>
                      <Check size={12} /> Codes match — approve
                    </button>
                    <button className="btn-danger btn-xs" disabled={!isAdmin || busy === request.id} onClick={() => decide(request, false)}>
                      <X size={12} /> Deny
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <Empty
                icon={ShieldQuestion}
                title="No pending requests"
                hint="Requests appear here when the other computer pairs with this node. They expire automatically."
              />
            )}
          </div>
        </section>
      </div>

      <Modal
        open={active !== null}
        title="Complete pairing"
        subtitle={active ? `Waiting for approval on ${active.name} (${shortId(active.node_id)})` : undefined}
        onClose={() => void cancelOutgoing()}
        width="max-w-lg"
        footer={
          <button className="btn-ghost btn-xs" onClick={() => void cancelOutgoing()}>
            Cancel request
          </button>
        }
      >
        {active && (
          <div className="space-y-4 text-center">
            <p className="text-xs leading-relaxed text-slate-400">
              On <span className="text-slate-200">{active.name}</span>, open <span className="text-slate-200">Network → Incoming requests</span> and
              make sure the code shown there matches this one. Approve only if they match.
            </p>
            <div className="rounded-2xl border border-cyan-400/30 bg-cyan-400/[0.06] px-6 py-6">
              <div className="font-mono text-4xl font-semibold tracking-[0.32em] text-cyan-200">{active.code}</div>
              <div className="mt-2 text-[10px] uppercase tracking-[0.22em] text-cyan-400/70">one-time pairing code</div>
            </div>
            <div className="space-y-1.5 text-left font-mono text-[11px] text-slate-500">
              <div className="flex items-center justify-between gap-2">
                <span>target</span>
                <span className="truncate text-slate-300">{active.name}</span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span>address</span>
                <span className="text-slate-300">{active.address}</span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span>fingerprint</span>
                <CopyText value={active.fingerprint} />
              </div>
            </div>
            <div className="flex items-center justify-center gap-2 text-xs text-slate-500">
              <StatusDot state={pairState === "pending" ? "connecting" : pairState === "approved" ? "online" : "failed"} />
              {pairState === "pending" ? "Awaiting approval…" : pairState}
            </div>
            <p className="text-[10px] text-slate-600">
              A fresh X25519 key is derived per request. The request expires in a few minutes if it is not answered.
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
