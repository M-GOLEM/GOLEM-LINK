import {
  ArrowUpCircle,
  CheckCircle2,
  CloudDownload,
  Download,
  History,
  Laptop,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Terminal,
  UploadCloud,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  fmtBytes,
  fmtClock,
  get,
  patch,
  post,
  timeAgo,
  type FleetActionResult,
  type FleetPayload,
  type FleetRow,
  type UpdateStatus,
} from "../api";
import { CopyText, Empty, Modal, Panel, Spinner, Toggle, UsageBar } from "../components/ui";
import { usePolling } from "../hooks";
import { useApp } from "../store";

const STATE_LABEL: Record<string, string> = {
  idle: "Not checked yet",
  checking: "Checking for updates",
  up_to_date: "Up to date",
  no_artifact: "No build for this platform",
  available: "Update available",
  downloading: "Downloading",
  ready: "Verified, ready to install",
  installing: "Installing",
  updated: "Installed",
  error: "Update failed",
};

function stateTone(state: string): string {
  if (state === "up_to_date" || state === "updated") return "badge-ok";
  if (state === "error") return "badge-bad";
  if (state === "available" || state === "ready" || state === "no_artifact") return "badge-warn";
  if (state === "checking" || state === "downloading" || state === "installing") return "badge-info";
  return "badge-idle";
}

function stateLabel(update: UpdateStatus | null): string {
  if (!update) return "unavailable";
  return STATE_LABEL[update.state] ?? update.state;
}

function LocalStatusCard({
  update,
  canWrite,
  onReload,
}: {
  update: UpdateStatus;
  canWrite: boolean;
  onReload: () => void;
}) {
  const { toast } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const act = async (kind: "check" | "download" | "apply") => {
    setBusy(kind);
    try {
      if (kind === "check") await post("/api/update/check");
      else if (kind === "download") await post("/api/update/download");
      else await post("/api/update/apply", { confirm: true });
      toast(
        kind === "check"
          ? "Update check finished"
          : kind === "download"
            ? "Artifact verified and staged"
            : update.install?.requires_operator
              ? "Verified artifact staged — run the operator command below"
              : "Installer launched; the service will restart on its own",
        "ok",
      );
      onReload();
    } catch (err) {
      toast(err instanceof Error ? err.message : "update action failed", "error");
    } finally {
      setBusy(null);
      setConfirmOpen(false);
    }
  };

  const stage = update.state;
  const staged = !!update.artifact?.staged;
  const note = update.notes?.trim();

  return (
    <Panel
      title={
        <div className="flex flex-wrap items-center gap-2">
          <CloudDownload size={15} className="text-cyan-400" />
          <span className="section-title">This computer</span>
          <span className={stateTone(stage)}>{stateLabel(update)}</span>
        </div>
      }
      subtitle={`Channel ${update.channel} · checked ${timeAgo(update.checked_at)} · ${update.platform}`}
      actions={
        canWrite && (
          <div className="flex flex-wrap gap-2">
            <button className="btn-ghost btn-xs" disabled={busy !== null} onClick={() => void act("check")}>
              <RefreshCw size={12} className={busy === "check" ? "animate-spin" : ""} /> Check now
            </button>
            {staged && !update.install?.requires_operator && (
              <button className="btn-primary btn-xs" disabled={busy !== null} onClick={() => setConfirmOpen(true)}>
                <ArrowUpCircle size={12} /> Install {update.latest_version}
              </button>
            )}
            {stage === "available" && (
              <button className="btn-primary btn-xs" disabled={busy !== null} onClick={() => void act("download")}>
                <Download size={12} /> Download & verify
              </button>
            )}
          </div>
        )
      }
    >
      <div className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-3">
          <div className="panel-quiet px-3 py-2">
            <div className="label">Installed</div>
            <div className="mt-1 font-mono text-sm text-slate-200">v{update.current_version}</div>
          </div>
          <div className="panel-quiet px-3 py-2">
            <div className="label">Newest available</div>
            <div className="mt-1 font-mono text-sm text-slate-200">
              {update.latest_version ? `v${update.latest_version}` : "—"}
            </div>
          </div>
          <div className="panel-quiet px-3 py-2">
            <div className="label">Source</div>
            <div className="mt-1 truncate font-mono text-[11px] text-slate-300">
              {update.artifact?.source_name || update.artifact?.source || "none yet"}
            </div>
          </div>
        </div>

        {update.progress > 0 && update.progress < 1 && (
          <div>
            <div className="flex items-center justify-between text-[11px] text-slate-500">
              <span>Downloading verified artifact</span>
              <span className="font-mono">{Math.round(update.progress * 100)}%</span>
            </div>
            <UsageBar className="mt-1.5" percent={update.progress * 100} tone="cyan" />
          </div>
        )}

        {note && (
          <div className="rounded-lg border border-ink-700/60 bg-ink-900/60 px-3 py-2">
            <div className="label">Release notes</div>
            <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-slate-300">{note}</p>
          </div>
        )}

        {update.error && (
          <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200">
            {update.error}
          </div>
        )}

        {update.artifact && (
          <div className="panel-quiet px-3 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="label">Artifact</span>
              <span className="font-mono text-[11px] text-slate-300">{update.artifact.file}</span>
              {update.artifact.size != null && (
                <span className="text-[11px] text-slate-500">{fmtBytes(update.artifact.size)}</span>
              )}
              {staged && <span className="badge-ok">staged & verified</span>}
            </div>
            <div className="mt-1">
              <CopyText value={update.artifact.sha256} />
            </div>
          </div>
        )}

        {staged && update.install && (
          <div className="rounded-xl border border-cyan-400/20 bg-cyan-400/[0.04] px-3 py-3">
            <div className="flex items-center gap-2 text-xs font-medium text-cyan-200">
              <Terminal size={13} />
              {update.install.requires_operator ? "One operator command applies it" : "Install plan"}
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">{update.install.note}</p>
            <div className="mt-2 rounded-lg border border-ink-700/60 bg-ink-950/70 px-3 py-2">
              <CopyText value={update.install.command} className="max-w-full" />
            </div>
          </div>
        )}

        {staged && update.install && !update.install.requires_operator && (
          <p className="text-[11px] leading-relaxed text-emerald-200/80">
            <ShieldCheck size={11} className="mr-1 inline" />
            The installer runs detached, backs up the program folder and restores it automatically if the new version
            fails to start.
          </p>
        )}

        {update.revert.available && (
          <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.05] px-3 py-2.5">
            <div className="flex items-center gap-2 text-[11px] font-medium text-amber-200">
              <RotateCcw size={12} /> Revert path available
            </div>
            {update.revert.path && <div className="mt-1 font-mono text-[10px] text-slate-400">{update.revert.path}</div>}
            {update.revert.command && (
              <div className="mt-1.5">
                <CopyText value={update.revert.command} className="max-w-full" />
              </div>
            )}
            {update.revert.note && <p className="mt-1 text-[11px] text-slate-500">{update.revert.note}</p>}
          </div>
        )}

        {update.history.length > 0 && (
          <div>
            <div className="label mb-1 flex items-center gap-1">
              <History size={11} /> Recent update activity
            </div>
            <div className="max-h-28 space-y-1 overflow-y-auto scroll-thin">
              {[...update.history].reverse().slice(0, 8).map((entry, index) => (
                <div key={`${entry.ts}-${index}`} className="flex gap-2 text-[11px] text-slate-500">
                  <span className="shrink-0 font-mono">{fmtClock(entry.ts)}</span>
                  <span className="text-slate-400">{entry.event}</span>
                  <span className="truncate">{entry.detail}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <Modal
        open={confirmOpen}
        title={`Install GOLEM LINK ${update.latest_version ?? ""}?`}
        subtitle="The verified artifact is already staged on this computer."
        onClose={() => setConfirmOpen(false)}
        width="max-w-lg"
        footer={
          <>
            <button className="btn-ghost btn-xs" onClick={() => setConfirmOpen(false)}>Cancel</button>
            <button className="btn-primary btn-xs" disabled={busy !== null} onClick={() => void act("apply")}>
              {busy === "apply" ? "Launching…" : "Install and restart"}
            </button>
          </>
        }
      >
        <ul className="space-y-2 text-sm text-slate-400">
          <li>• The service stops, the program folder is replaced, and the service starts again.</li>
          <li>• Paired devices, shares, settings and the dashboard password are preserved.</li>
          <li>• The previous program folder is kept; a failed start restores it automatically.</li>
        </ul>
      </Modal>
    </Panel>
  );
}

function AutoUpdatePanel({ update, canWrite }: { update: UpdateStatus; canWrite: boolean }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [interval, setIntervalValue] = useState(String(Math.round(update.check_interval_s / 60)));

  useEffect(() => {
    setIntervalValue(String(Math.round(update.check_interval_s / 60)));
  }, [update.check_interval_s]);

  const save = async (payload: Record<string, unknown>, message: string) => {
    setBusy(true);
    try {
      await patch("/api/settings", { update: payload });
      toast(message, "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not save update settings", "error");
    } finally {
      setBusy(false);
    }
  };

  const saveInterval = () => {
    const minutes = Math.max(15, Math.min(24 * 60, Number(interval) || 360));
    void save({ check_interval_s: minutes * 60 }, `Checked every ${minutes} minutes`);
  };

  return (
    <Panel
      title="Autopilot updates"
      subtitle="Checks run through paired devices that serve a signed release manifest; every artifact is SHA-256 verified before it is used."
    >
      <div className="grid gap-2 lg:grid-cols-2">
        <Toggle
          label="Allow updates on this node"
          hint="Turning this off also stops serving your manifest to peers."
          checked={update.enabled}
          disabled={!canWrite || busy}
          onChange={(value) => void save({ enabled: value }, value ? "Updates enabled" : "Updates disabled")}
        />
        <Toggle
          label="Check automatically"
          hint={`Looks for a newer release every ${Math.round(update.check_interval_s / 60)} minutes.`}
          checked={update.auto_check}
          disabled={!canWrite || busy || !update.enabled}
          onChange={(value) => void save({ auto_check: value }, value ? "Automatic checks on" : "Automatic checks off")}
        />
        <Toggle
          label="Download and verify automatically"
          hint="A newer release is fetched and SHA-256 checked without a click. Nothing runs until the hash matches."
          checked={update.auto_stage}
          disabled={!canWrite || busy || !update.enabled}
          onChange={(value) => void save({ auto_stage: value }, value ? "Automatic staging on" : "Automatic staging off")}
        />
        {update.auto_install_supported ? (
          <Toggle
            label="Install automatically"
            hint={
              update.remote_install
                ? "The opt-in root helper re-checks the artifact, applies it and restarts the service unattended."
                : "Windows: a verified update installs and restarts the service unattended."
            }
            checked={update.auto_install}
            disabled={!canWrite || busy || !update.enabled}
            onChange={(value) => void save({ auto_install: value }, value ? "Automatic install on" : "Automatic install off")}
          />
        ) : (
          <div className="rounded-xl border border-ink-700/60 bg-ink-900/50 px-3.5 py-3">
            <div className="label">Install on Linux</div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">
              The service cannot elevate itself. Autopilot stages and verifies the release; applying it is one
              <span className="font-mono"> sudo </span> command on that machine, with an automatic rollback path.
              {update.remote_install_supported && (
                <> Reinstall once with <span className="font-mono">--allow-remote-updates</span> to add a root-owned
                helper and make this unattended.</>
              )}
            </p>
          </div>
        )}
        <div className="rounded-xl border border-ink-700/60 bg-ink-900/50 px-3.5 py-3">
          <div className="label">Check interval (minutes)</div>
          <div className="mt-1.5 flex gap-2">
            <input
              className="input w-24"
              type="number"
              min={15}
              max={1440}
              value={interval}
              disabled={!canWrite || busy}
              onChange={(event) => setIntervalValue(event.target.value)}
            />
            <button className="btn-ghost btn-xs" disabled={!canWrite || busy} onClick={saveInterval}>
              Save
            </button>
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">
            The agent jitters its schedule and never checks while an update is already staged.
          </p>
        </div>
      </div>
      <p className="mt-3 text-[11px] text-slate-600">
        Manifest folder: <span className="font-mono">{update.source_dir}</span>
      </p>
    </Panel>
  );
}

function FleetTable({
  rows,
  canWrite,
  busy,
  onAction,
}: {
  rows: FleetRow[];
  canWrite: boolean;
  busy: string | null;
  onAction: (row: FleetRow, action: "check" | "apply" | "restart") => void;
}) {
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="w-full min-w-[720px] text-left text-xs">
        <thead>
          <tr className="border-b border-ink-700/60 text-[10px] uppercase tracking-wide text-slate-500">
            <th className="py-2 pr-3 font-medium">Device</th>
            <th className="py-2 pr-3 font-medium">Version</th>
            <th className="py-2 pr-3 font-medium">Update state</th>
            <th className="py-2 pr-3 font-medium">Latest</th>
            <th className="py-2 pr-3 font-medium">Trust</th>
            <th className="py-2 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const online = row.status === "online";
            const state = row.update?.state ?? (online ? "unknown" : "offline");
            const disabled = !canWrite || busy !== null || !online;
            const control = row.can_control;
            return (
              <tr key={row.node_id} className="border-b border-ink-800/60 last:border-0">
                <td className="py-2.5 pr-3">
                  <div className="flex items-center gap-2">
                    <Laptop size={13} className={online ? "text-cyan-300" : "text-slate-600"} />
                    <span className="truncate font-medium text-slate-200">{row.name}</span>
                    {row.self && <span className="badge-info">this node</span>}
                    {!online && <span className="badge-idle">offline</span>}
                  </div>
                  {row.error && online && <div className="mt-0.5 text-[10px] text-amber-300/80">{row.error}</div>}
                </td>
                <td className="py-2.5 pr-3 font-mono text-slate-300">v{row.version || "?"}</td>
                <td className="py-2.5 pr-3">
                  <span className={stateTone(state)}>{stateLabel(row.update)}</span>
                  {row.update?.error && (
                    <div className="mt-0.5 max-w-[220px] truncate text-[10px] text-rose-300/80">{row.update.error}</div>
                  )}
                </td>
                <td className="py-2.5 pr-3 font-mono text-slate-400">
                  {row.update?.latest_version ? `v${row.update.latest_version}` : "—"}
                </td>
                <td className="py-2.5 pr-3">
                  {row.self ? (
                    <span className="badge-ok">local</span>
                  ) : control ? (
                    <span className="badge-ok">control granted</span>
                  ) : (
                    <span className="badge-idle">read-only</span>
                  )}
                </td>
                <td className="py-2.5 text-right">
                  <div className="inline-flex flex-wrap justify-end gap-1.5">
                    <button
                      className="btn-ghost btn-xs"
                      disabled={disabled}
                      onClick={() => onAction(row, "check")}
                      title={control || row.self ? "Ask this node to check for updates" : "Check runs locally on that node only"}
                    >
                      <RefreshCw size={11} className={busy === `${row.node_id}:check` ? "animate-spin" : ""} /> Check
                    </button>
                    <button
                      className="btn-ghost btn-xs"
                      disabled={disabled || !control || !row.update?.artifact?.staged}
                      onClick={() => onAction(row, "apply")}
                      title={control ? "Install the verified update on that node" : "That node has not granted remote control"}
                    >
                      <UploadCloud size={11} /> Apply
                    </button>
                    <button
                      className="btn-ghost btn-xs"
                      disabled={disabled || !control}
                      onClick={() => onAction(row, "restart")}
                      title={control ? "Restart the agent on that node" : "That node has not granted remote control"}
                    >
                      <RotateCcw size={11} /> Restart
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function UpdatesPage() {
  const { me, toast } = useApp();
  const canWrite = me?.role === "admin";
  const updateQ = usePolling(() => get<UpdateStatus>("/api/update"), 5000);
  const fleetQ = usePolling(() => get<FleetPayload>("/api/update/fleet"), 12000);
  const [busy, setBusy] = useState<string | null>(null);
  const [fleetBusy, setFleetBusy] = useState(false);
  const [trustBusy, setTrustBusy] = useState(false);
  const [confirmTarget, setConfirmTarget] = useState<{ row: FleetRow; action: "apply" | "restart" } | null>(null);
  const [fleetConfirm, setFleetConfirm] = useState(false);
  const [trustConfirm, setTrustConfirm] = useState(false);

  const reloadAll = useCallback(() => {
    updateQ.reload();
    fleetQ.reload();
  }, [updateQ.reload, fleetQ.reload]);

  const controlNode = async (row: FleetRow, action: "check" | "apply" | "restart") => {
    setBusy(`${row.node_id}:${action}`);
    try {
      await post(`/api/devices/${row.node_id}/control`, { action, confirm: action !== "check" });
      toast(
        action === "check"
          ? `${row.name} checked for updates`
          : action === "apply"
            ? `${row.name} is installing the staged update`
            : `Restart requested on ${row.name}`,
        "ok",
      );
      reloadAll();
    } catch (err) {
      toast(err instanceof Error ? err.message : "remote action failed", "error");
    } finally {
      setBusy(null);
      setConfirmTarget(null);
    }
  };

  const trustPeers = async () => {
    const targets = rows.filter((row) => !row.self);
    if (!targets.length) return;
    setTrustBusy(true);
    try {
      for (const row of targets) {
        await patch(`/api/peers/${row.node_id}/permissions`, { allow_control: true });
      }
      toast(`Paired devices may now maintain this node (${targets.length})`, "ok");
      reloadAll();
    } catch (err) {
      toast(err instanceof Error ? err.message : "could not update peer permissions", "error");
    } finally {
      setTrustBusy(false);
      setTrustConfirm(false);
    }
  };

  const fleetAction = async (action: "check" | "apply") => {
    setFleetBusy(true);
    try {
      const result = await post<FleetActionResult>("/api/update/fleet", { action, confirm: action === "apply" });
      const failed = result.results.filter((row) => !row.ok);
      toast(
        failed.length
          ? `${result.results.length - failed.length} succeeded, ${failed.length} failed: ${failed
              .map((row) => row.name)
              .join(", ")}`
          : `${action === "check" ? "Fleet check" : "Fleet update"} finished on ${result.results.length} node(s)`,
        failed.length ? "error" : "ok",
      );
      reloadAll();
    } catch (err) {
      toast(err instanceof Error ? err.message : "fleet action failed", "error");
    } finally {
      setFleetBusy(false);
      setFleetConfirm(false);
    }
  };

  if (!updateQ.data) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        {updateQ.error ? (
          <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            Cannot read update state: {updateQ.error}
          </div>
        ) : (
          <Spinner label="Reading update state…" />
        )}
      </div>
    );
  }

  const update = updateQ.data;
  const rows = fleetQ.data?.rows ?? [];
  const controlGranted = rows.filter((row) => !row.self && row.can_control && row.status === "online").length;

  return (
    <div className="space-y-4">
      <LocalStatusCard update={update} canWrite={canWrite} onReload={reloadAll} />

      <AutoUpdatePanel update={update} canWrite={canWrite} />

      <Panel
        title={
          <div className="flex flex-wrap items-center gap-2">
            <UploadCloud size={15} className="text-cyan-400" />
            <span className="section-title">Fleet maintenance</span>
            {updateQ.error && <span className="badge-bad">{updateQ.error}</span>}
          </div>
        }
        subtitle="Drive checks, verified installs and restarts on paired computers. A device only accepts these commands after it grants remote control to this node."
        actions={
          canWrite && (
            <div className="flex flex-wrap gap-2">
              <button className="btn-ghost btn-xs" disabled={fleetBusy} onClick={() => void fleetAction("check")}>
                <RefreshCw size={12} className={fleetBusy ? "animate-spin" : ""} /> Check all
              </button>
              <button
                className="btn-ghost btn-xs"
                disabled={fleetBusy || trustBusy || rows.every((row) => row.self)}
                onClick={() => setTrustConfirm(true)}
                title="Let every paired device run check / apply / restart on this node"
              >
                <ShieldCheck size={12} /> Let peers maintain this node
              </button>
              <button
                className="btn-primary btn-xs"
                disabled={fleetBusy || controlGranted === 0}
                onClick={() => setFleetConfirm(true)}
                title={controlGranted ? "Install staged updates on every controllable node" : "No peer has granted remote control yet"}
              >
                <UploadCloud size={12} /> Update fleet
              </button>
            </div>
          )
        }
      >
        <div className="space-y-3">
          {fleetQ.error && (
            <div className="rounded-lg border border-amber-400/25 bg-amber-400/[0.06] px-3 py-2 text-[11px] text-amber-200/90">
              Fleet status could not be refreshed: {fleetQ.error}
            </div>
          )}

          {rows.length ? (
            <FleetTable
              rows={rows}
              canWrite={canWrite}
              busy={busy}
              onAction={(row, action) =>
                action === "check" ? void controlNode(row, "check") : setConfirmTarget({ row, action })
              }
            />
          ) : (
            <Empty
              icon={UploadCloud}
              title="No devices reported yet"
              hint="Pair a second computer first; its update state appears here automatically."
            />
          )}

          <div className="rounded-lg border border-ink-700/60 bg-ink-900/50 px-3 py-2 text-[11px] leading-relaxed text-slate-500">
            <ShieldCheck size={11} className="mr-1 inline text-emerald-400/80" />
            Remote actions travel over certificate-pinned TLS, are limited to check / apply / restart, and are written to
            this node's audit log. Apply and restart always ask for confirmation. On Linux without the opt-in root
            helper, a remote apply only stages the verified artifact — the operator still runs one sudo command.
          </div>
        </div>
      </Panel>

      <Modal
        open={confirmTarget !== null}
        title={
          confirmTarget?.action === "apply"
            ? `Install update on ${confirmTarget?.row.name}?`
            : `Restart the agent on ${confirmTarget?.row.name}?`
        }
        subtitle="This command runs on that device, not on this one."
        onClose={() => setConfirmTarget(null)}
        width="max-w-lg"
        footer={
          <>
            <button className="btn-ghost btn-xs" onClick={() => setConfirmTarget(null)}>Cancel</button>
            <button
              className="btn-primary btn-xs"
              disabled={busy !== null}
              onClick={() => confirmTarget && void controlNode(confirmTarget.row, confirmTarget.action)}
            >
              {confirmTarget?.action === "apply" ? "Install remotely" : "Restart remotely"}
            </button>
          </>
        }
      >
        {confirmTarget?.action === "apply" ? (
          <p className="text-sm leading-relaxed text-slate-400">
            {confirmTarget?.row.name} already has a verified artifact staged. It will install it and restart its agent;
            open tasks may be interrupted. This node's dashboard reconnects automatically.
          </p>
        ) : (
          <p className="text-sm leading-relaxed text-slate-400">
            The agent on {confirmTarget?.row.name} restarts. Tasks running there are re-queued or fail according to their
            retry policy, and the device reconnects within a few seconds.
          </p>
        )}
      </Modal>

      <Modal
        open={fleetConfirm}
        title="Update every controllable device?"
        subtitle={`${controlGranted} of ${Math.max(rows.length - 1, 0)} paired devices grant this node remote control.`}
        onClose={() => setFleetConfirm(false)}
        width="max-w-lg"
        footer={
          <>
            <button className="btn-ghost btn-xs" onClick={() => setFleetConfirm(false)}>Cancel</button>
            <button className="btn-primary btn-xs" disabled={fleetBusy} onClick={() => void fleetAction("apply")}>
              {fleetBusy ? "Updating…" : "Update fleet"}
            </button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-slate-400">
          Peers update first and this node last, so the node serving the artifact stays reachable. Devices that did not
          grant control are skipped; offline devices are skipped too.
        </p>
        <p className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 text-[11px] text-amber-200/90">
          Only devices with an already verified, staged artifact are touched. On Linux without the opt-in root helper
          that means staging only — the operator command still applies it.
        </p>
      </Modal>

      <Modal
        open={trustConfirm}
        title="Let every paired device maintain this node?"
        subtitle="This grants, one time, the inbound permission this node needs before peers can check, install or restart it."
        onClose={() => setTrustConfirm(false)}
        width="max-w-lg"
        footer={
          <>
            <button className="btn-ghost btn-xs" onClick={() => setTrustConfirm(false)}>Cancel</button>
            <button className="btn-primary btn-xs" disabled={trustBusy} onClick={() => void trustPeers()}>
              {trustBusy ? "Granting…" : "Allow peers"}
            </button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-slate-400">
          Every currently paired device may run check, apply and restart on this node — those are the only three actions
          the protocol exposes, and each is written to this node's audit log. The permission stays per device, so you can
          revoke one below without touching the others.
        </p>
        <p className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 text-[11px] text-amber-200/90">
          This only affects what peers may ask of this node. For this dashboard to drive another device, grant it once on
          that device's Updates page too.
        </p>
      </Modal>

      <Panel title="How updates stay safe">
        <div className="grid gap-3 text-xs leading-relaxed text-slate-400 md:grid-cols-3">
          <div className="panel-quiet p-3">
            <div className="mb-1 flex items-center gap-1.5 font-medium text-slate-200">
              <CheckCircle2 size={13} className="text-emerald-400" /> Verified before use
            </div>
            Every manifest is validated (version, platform, file name) and every artifact is checked against its SHA-256
            and size before it is staged.
          </div>
          <div className="panel-quiet p-3">
            <div className="mb-1 flex items-center gap-1.5 font-medium text-slate-200">
              <RotateCcw size={13} className="text-amber-300" /> Always a way back
            </div>
            Windows keeps the previous program folder; Linux keeps <span className="font-mono">/opt/golem-link.previous</span>.
            A failed start restores the old version automatically.
          </div>
          <div className="panel-quiet p-3">
            <div className="mb-1 flex items-center gap-1.5 font-medium text-slate-200">
              <ShieldCheck size={13} className="text-cyan-300" /> Least privilege
            </div>
            Peers may pull your artifact only if you allow it. Remote check/apply/restart is a separate permission that is
            off until you grant it per device.
          </div>
        </div>
      </Panel>
    </div>
  );
}
