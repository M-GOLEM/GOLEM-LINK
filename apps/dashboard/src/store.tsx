import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { get, post, setCsrf, type Me, type Metrics } from "./api";

type Status = "boot" | "setup" | "login" | "ready";
type WsState = "idle" | "connecting" | "open" | "closed";
export type ToastKind = "ok" | "error" | "info";
export interface Toast {
  id: number;
  message: string;
  kind: ToastKind;
}

const REFRESH_COALESCE_MS = 600;

interface AppContextValue {
  status: Status;
  me: Me | null;
  wsState: WsState;
  refreshToken: number;
  toasts: Toast[];
  wizardOpen: boolean;
  openWizard: () => void;
  closeWizard: () => void;
  refresh: () => void;
  onAuthenticated: () => Promise<void>;
  enterReady: (openWizard: boolean) => void;
  logout: () => Promise<void>;
  toast: (message: string, kind?: ToastKind) => void;
  dismissToast: (id: number) => void;
}

const AppContext = createContext<AppContextValue | null>(null);
// Live telemetry arrives every 2 s. Keeping it in its own context stops the
// whole shell and every page from re-rendering on each sample.
const LiveMetricsContext = createContext<Metrics | null>(null);

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error("useApp must be used inside AppProvider");
  return value;
}

export function useLiveMetrics(): Metrics | null {
  return useContext(LiveMetricsContext);
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("boot");
  const [me, setMe] = useState<Me | null>(null);
  const [liveMetrics, setLiveMetrics] = useState<Metrics | null>(null);
  const [wsState, setWsState] = useState<WsState>("idle");
  const [refreshToken, setRefreshToken] = useState(0);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [wizardOpen, setWizardOpen] = useState(false);
  const toastSeq = useRef(0);
  const refreshTimer = useRef<number | undefined>(undefined);

  const toast = useCallback((message: string, kind: ToastKind = "info") => {
    const id = ++toastSeq.current;
    setToasts((current) => [...current.slice(-4), { id, message, kind }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id));
    }, 4600);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const refresh = useCallback(() => setRefreshToken((value) => value + 1), []);

  // Task progress can emit dozens of events per second; every event used to
  // fire all polling hooks at once. Coalesce to one refresh per burst.
  const queueRefresh = useCallback(() => {
    if (refreshTimer.current !== undefined) return;
    refreshTimer.current = window.setTimeout(() => {
      refreshTimer.current = undefined;
      setRefreshToken((value) => value + 1);
    }, REFRESH_COALESCE_MS);
  }, []);

  useEffect(
    () => () => {
      if (refreshTimer.current !== undefined) window.clearTimeout(refreshTimer.current);
    },
    [],
  );

  const onAuthenticated = useCallback(async () => {
    const next = await get<Me>("/api/auth/me");
    setCsrf(next.csrf);
    setMe(next);
    setStatus("ready");
  }, []);

  const enterReady = useCallback((openWizard: boolean) => {
    setWizardOpen(openWizard);
  }, []);

  const logout = useCallback(async () => {
    try {
      await post("/api/auth/logout");
    } catch {
      /* the session may already be gone */
    }
    setCsrf(null);
    setMe(null);
    setLiveMetrics(null);
    setStatus("login");
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const setup = await get<{ needs_setup: boolean }>("/api/setup/status");
        if (cancelled) return;
        if (setup.needs_setup) {
          setStatus("setup");
          return;
        }
        try {
          const current = await get<Me>("/api/auth/me");
          if (cancelled) return;
          setCsrf(current.csrf);
          setMe(current);
          setStatus("ready");
        } catch {
          if (!cancelled) setStatus("login");
        }
      } catch (err) {
        if (!cancelled) {
          setStatus("login");
          toast(err instanceof Error ? err.message : "cannot reach the agent", "error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [toast]);

  // Live feed: snapshot on connect, then push updates.
  useEffect(() => {
    if (status !== "ready") {
      setWsState("idle");
      return;
    }
    let socket: WebSocket | null = null;
    let retry = 0;
    let timer: number | undefined;
    let disposed = false;

    function schedule() {
      if (disposed) return;
      const hidden = document.visibilityState === "hidden";
      timer = window.setTimeout(connect, hidden ? 30000 : Math.min(1000 * 2 ** retry, 15000));
    }

    function connect() {
      if (disposed) return;
      if (document.visibilityState === "hidden") {
        setWsState("idle");
        schedule();
        return;
      }
      setWsState("connecting");
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
      socket.onopen = () => {
        retry = 0;
        setWsState("open");
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data as string);
          if (message.type === "snapshot") {
            const snapshot = message.data || {};
            if (snapshot.metrics) setLiveMetrics(snapshot.metrics as Metrics);
            setRefreshToken((value) => value + 1);
            return;
          }
          if (message.topic === "metrics") {
            setLiveMetrics(message.data as Metrics);
          }
          if (
            ["task", "alert", "transfer", "shares", "pair_requests", "peers", "settings", "update", "clipboard"].includes(
              message.topic,
            )
          ) {
            queueRefresh();
          }
        } catch {
          /* ignore malformed frames; the next snapshot repairs state */
        }
      };
      socket.onclose = (event) => {
        if (disposed) return;
        setWsState("closed");
        if (event.code === 4401) {
          // The server rejected the session cookie. Retrying would loop
          // forever against a rejected handshake; return to the login form.
          setStatus("login");
          toast("Session expired — sign in again.", "error");
          return;
        }
        retry = Math.min(retry + 1, 6);
        schedule();
      };
      socket.onerror = () => socket?.close();
    }

    const wake = () => {
      if (disposed || document.visibilityState !== "visible") return;
      if (socket && socket.readyState <= 1) return;
      if (timer !== undefined) window.clearTimeout(timer);
      retry = 0;
      connect();
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", wake);
    connect();
    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("online", wake);
      if (timer) window.clearTimeout(timer);
      socket?.close();
      setWsState("idle");
    };
  }, [status, toast, queueRefresh]);

  const value = useMemo<AppContextValue>(
    () => ({
      status,
      me,
      wsState,
      refreshToken,
      toasts,
      wizardOpen,
      openWizard: () => setWizardOpen(true),
      closeWizard: () => setWizardOpen(false),
      refresh,
      onAuthenticated,
      enterReady,
      logout,
      toast,
      dismissToast,
    }),
    [
      status,
      me,
      wsState,
      refreshToken,
      toasts,
      wizardOpen,
      refresh,
      onAuthenticated,
      enterReady,
      logout,
      toast,
      dismissToast,
    ],
  );

  return (
    <LiveMetricsContext.Provider value={liveMetrics}>
      <AppContext.Provider value={value}>{children}</AppContext.Provider>
    </LiveMetricsContext.Provider>
  );
}
