import { Activity, Cable, Network as NetworkIcon, Radar, RadioTower, Signal, Wifi } from "lucide-react";
import { useState } from "react";
import {
  get,
  patch,
  timeAgo,
  type NetworkPayload,
  type SettingsPayload,
} from "../api";
import { PairingFlow } from "../components/Pairing";
import { CopyText, Empty, Panel, Spinner, StatusDot, Toggle } from "../components/ui";
import { usePolling } from "../hooks";
import { useApp } from "../store";

export function NetworkPage() {
  const { me, toast, refresh } = useApp();
  const networkQ = usePolling(() => get<NetworkPayload>("/api/network"), 4000);
  const settingsQ = usePolling(() => get<SettingsPayload>("/api/settings"), 15000);
  const [busy, setBusy] = useState<string | null>(null);
  const canWrite = me?.role === "admin";
  const network = networkQ.data;
  const discovery = settingsQ.data?.config.discovery;

  const updateDiscovery = async (field: "mdns_enabled" | "beacon_enabled", value: boolean) => {
    setBusy(field);
    try {
      const result = await patch<{ restart_required: boolean }>("/api/settings", {
        discovery: { [field]: value },
      });
      toast(
        result.restart_required
          ? "Saved. Restart the agent to apply discovery changes."
          : "Discovery setting saved",
        "ok",
      );
      settingsQ.reload();
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not save discovery setting", "error");
    } finally {
      setBusy(null);
    }
  };

  const removeManualPeer = async (entry: string) => {
    if (!discovery) return;
    const remaining = discovery.manual_peers.filter((item: string) => item !== entry);
    setBusy(entry);
    try {
      await patch("/api/settings", { discovery: { manual_peers: remaining } });
      toast(`Removed manual peer ${entry}`, "info");
      settingsQ.reload();
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not update manual peers", "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      {networkQ.error && (
        <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          Cannot load network state: {networkQ.error}
        </div>
      )}

      <div className="grid-cards">
        <div className="panel p-4">
          <div className="label">Nodes online</div>
          <div className="value mt-2">{network ? `${network.online} / ${network.paired + 1}` : "—"}</div>
          <p className="mt-2 text-[11px] text-slate-500">Paired peers reachable right now, plus this node.</p>
        </div>
        <div className="panel p-4">
          <div className="label">Average peer latency</div>
          <div className="value mt-2">
            {network && network.peers.some((peer) => peer.latency_ms != null)
              ? `${Math.round(
                  network.peers.reduce((sum, peer) => sum + (peer.latency_ms ?? 0), 0) /
                    network.peers.filter((peer) => peer.latency_ms != null).length,
                )} ms`
              : "—"}
          </div>
          <p className="mt-2 text-[11px] text-slate-500">Measured against the authenticated peer health endpoint.</p>
        </div>
        <div className="panel p-4">
          <div className="label">Discovery</div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <span className={discovery?.mdns_enabled ? "badge-ok" : "badge-idle"}>mDNS {discovery?.mdns_enabled ? "on" : "off"}</span>
            <span className={discovery?.beacon_enabled ? "badge-ok" : "badge-idle"}>Beacon {discovery?.beacon_enabled ? "on" : "off"}</span>
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            UDP beacon port {discovery?.beacon_port ?? 45873}; announces every {discovery?.beacon_interval_s ?? 5}s.
          </p>
        </div>
        <div className="panel p-4">
          <div className="label">Manual peers</div>
          <div className="value mt-2">{discovery?.manual_peers.length ?? "—"}</div>
          <p className="mt-2 text-[11px] text-slate-500">Fallback addresses polled when multicast discovery is unreliable.</p>
        </div>
      </div>

      <Panel
        title={
          <div className="section-title">
            <Radar size={13} className="text-cyan-400" /> Device pairing
          </div>
        }
        subtitle="Discovery, one-time codes and explicit approval. No automatic trust on first contact."
      >
        <PairingFlow onChanged={() => refresh()} />
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel
          title={
            <div className="section-title">
              <Signal size={13} className="text-cyan-400" /> Peer links
            </div>
          }
          bodyClassName="p-0"
        >
          {network?.peers.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[540px] border-collapse">
                <thead>
                  <tr className="border-b border-ink-700/60">
                    <th className="th">Peer</th>
                    <th className="th">Address</th>
                    <th className="th">Status</th>
                    <th className="th">Latency</th>
                    <th className="th">Link</th>
                    <th className="th">Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {network.peers.map((peer) => (
                    <tr key={peer.node_id} className="row-hover border-b border-ink-700/40 last:border-0">
                      <td className="td">
                        <div className="text-sm text-slate-200">{peer.name}</div>
                        <div className="font-mono text-[10px] text-slate-600">{peer.hostname || peer.os}</div>
                      </td>
                      <td className="td font-mono text-[11px] text-slate-400">{peer.address}:{peer.peer_port}</td>
                      <td className="td">
                        <span className={peer.status === "online" ? "badge-ok" : "badge-idle"}>
                          <StatusDot state={peer.status} /> {peer.status}
                        </span>
                      </td>
                      <td className="td font-mono text-[11px] text-slate-400">
                        {peer.latency_ms != null ? `${peer.latency_ms.toFixed(0)} ms` : "—"}
                      </td>
                      <td className="td text-[11px]">
                        {peer.status === "online" ? (
                          <span className="text-slate-500">
                            {peer.reconnects ? `reconnected ${peer.reconnects}×` : "stable"}
                          </span>
                        ) : (
                          <span
                            className="block max-w-[220px] truncate text-rose-300/90"
                            title={peer.last_error ?? "no error recorded"}
                          >
                            {peer.last_error || "offline"}
                          </span>
                        )}
                      </td>
                      <td className="td text-[11px] text-slate-500">{timeAgo(peer.last_seen)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-4">
              <Empty icon={Cable} title="No peer links" hint="Pair a second node to establish pinned TLS links in both directions." />
            </div>
          )}
        </Panel>

        <Panel
          title={
            <div className="section-title">
              <Wifi size={13} className="text-cyan-400" /> Interfaces
            </div>
          }
          subtitle="Live counters from psutil on this node"
          bodyClassName="p-0"
        >
          {network?.interfaces?.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] border-collapse">
                <thead>
                  <tr className="border-b border-ink-700/60">
                    <th className="th">Interface</th>
                    <th className="th">State</th>
                    <th className="th">Send</th>
                    <th className="th">Receive</th>
                  </tr>
                </thead>
                <tbody>
                  {network.interfaces.map((iface) => (
                    <tr key={iface.name} className="row-hover border-b border-ink-700/40 last:border-0">
                      <td className="td font-mono text-xs text-slate-300">{iface.name}</td>
                      <td className="td">
                        <span className={iface.is_up === false ? "badge-bad" : "badge-ok"}>{iface.is_up === false ? "down" : "up"}</span>
                      </td>
                      <td className="td font-mono text-[11px] text-slate-400">{iface.send_rate.toFixed(1)} B/s</td>
                      <td className="td font-mono text-[11px] text-slate-400">{iface.recv_rate.toFixed(1)} B/s</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-4"><Spinner label="Reading interfaces…" /></div>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Discovery transports" subtitle="Both run locally; no cloud relay or internet lookup is used.">
          <div className="space-y-2">
            <Toggle
              label="mDNS / DNS-SD"
              hint={`Service type _golem-link._tcp.local. Instances announce as golem-link-<node>.`}
              checked={!!discovery?.mdns_enabled}
              disabled={!canWrite || busy === "mdns_enabled"}
              onChange={(value) => void updateDiscovery("mdns_enabled", value)}
            />
            <Toggle
              label="UDP broadcast beacon"
              hint="Useful on routers that block multicast; carries the same identity fields."
              checked={!!discovery?.beacon_enabled}
              disabled={!canWrite || busy === "beacon_enabled"}
              onChange={(value) => void updateDiscovery("beacon_enabled", value)}
            />
            <p className="text-[11px] leading-relaxed text-slate-600">
              Turning a transport off requires an agent restart. Existing paired peers reconnect independently of discovery.
            </p>
          </div>
        </Panel>

        <Panel
          title={
            <div className="section-title">
              <RadioTower size={13} className="text-cyan-400" /> Manual peer addresses
            </div>
          }
          subtitle="Fallbacks for networks where discovery cannot be seen."
        >
          {discovery?.manual_peers.length ? (
            <div className="space-y-2">
              {discovery.manual_peers.map((entry: string) => (
                <div key={entry} className="flex items-center gap-3 rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2">
                  <NetworkIcon size={13} className="text-slate-500" />
                  <span className="flex-1 font-mono text-xs text-slate-300">{entry}</span>
                  <button
                    className="btn-ghost btn-xs"
                    disabled={!canWrite || busy === entry}
                    onClick={() => void removeManualPeer(entry)}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              None configured. Add one on the Devices pairing panel by entering an IP and using “Pair by IP”.
            </p>
          )}
          <div className="mt-3 space-y-1.5 border-t border-ink-700/60 pt-3 font-mono text-[10px] text-slate-600">
            <div className="flex items-center justify-between"><span>fingerprint</span><CopyText value={network?.local.fingerprint ?? ""} /></div>
            <div className="flex items-center justify-between"><span>node</span><span>{network?.local.node_id ?? "—"}</span></div>
          </div>
        </Panel>
      </div>

      <Panel
        title={
          <div className="section-title">
            <Activity size={13} className="text-cyan-400" /> Discovery probe
          </div>
        }
        subtitle="Unpaired announcements currently visible to this agent."
      >
        {network?.discovered.length ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {network.discovered.map((node) => (
              <div key={node.node_id} className="rounded-xl border border-ink-700/60 bg-ink-900/50 px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <StatusDot state={node.paired ? "online" : "warning"} />
                  <span className="truncate text-sm text-slate-200">{node.name}</span>
                  {node.paired && <span className="badge-ok ml-auto">paired</span>}
                </div>
                <div className="mt-1 font-mono text-[10px] text-slate-500">
                  {node.address}:{node.port} · {node.os} · {node.source}
                </div>
                <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-slate-600">
                  {node.fingerprint}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty icon={Radar} title="No announcements" hint="Start the agent on the other machine, or pair by IP if this network filters multicast." />
        )}
      </Panel>
    </div>
  );
}
