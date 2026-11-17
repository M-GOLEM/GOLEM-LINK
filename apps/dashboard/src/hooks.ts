import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "./store";

export function usePolling<T>(loader: () => Promise<T>, intervalMs = 5000) {
  const { refreshToken } = useApp();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const inFlight = useRef(false);
  const ticketRef = useRef(0);

  const run = useCallback(async () => {
    // A slow response plus an event burst used to stack overlapping polls
    // until the browser ran out of sockets and the dashboard appeared frozen.
    if (inFlight.current) return;
    inFlight.current = true;
    const ticket = ++ticketRef.current;
    try {
      const next = await loaderRef.current();
      if (ticket === ticketRef.current) {
        setData(next);
        setError(null);
      }
    } catch (err) {
      if (ticket === ticketRef.current) setError(err instanceof Error ? err.message : "request failed");
    } finally {
      if (ticket === ticketRef.current) setLoading(false);
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    void run();
    const handle = window.setInterval(() => void run(), intervalMs);
    const wake = () => {
      if (document.visibilityState === "visible") void run();
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    return () => {
      window.clearInterval(handle);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
      // Responses that arrive after this cycle must not overwrite newer data.
      ticketRef.current += 1;
    };
  }, [refreshToken, intervalMs, run]);

  return { data, error, loading, reload: run };
}

export function useNow(intervalMs = 15000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(handle);
  }, [intervalMs]);
  return now;
}
