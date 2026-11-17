import * as React from "react";
import { setTimeZone } from "@/lib/format";
import type { AppConfig, Printer } from "@/lib/types";

interface PrintersState {
  printers: Printer[];
  loaded: boolean;
  connected: boolean;
  lastMessageAt: number | null;
  config: AppConfig | null;
}

const PrintersContext = React.createContext<PrintersState | null>(null);

/** One WebSocket to printer-api for the whole app; reconnects with backoff. */
export function PrintersProvider({ children }: { children: React.ReactNode }) {
  const [printers, setPrinters] = React.useState<Printer[]>([]);
  const [loaded, setLoaded] = React.useState(false);
  const [connected, setConnected] = React.useState(false);
  const [lastMessageAt, setLastMessageAt] = React.useState<number | null>(null);
  const [config, setConfig] = React.useState<AppConfig | null>(null);

  React.useEffect(() => {
    fetch("/api/config")
      .then((r) => (r.ok ? r.json() : null))
      .then((c: AppConfig | null) => {
        if (c) {
          setTimeZone(c.tz);
          setConfig(c);
        }
      })
      .catch(() => {});
  }, []);

  React.useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 1000;
    let timer: number | undefined;
    let stopped = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/ws`);
      ws.onopen = () => {
        setConnected(true);
        retry = 1000;
      };
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        setLastMessageAt(Date.now());
        if (msg.type === "snapshot") {
          setPrinters(msg.printers);
          setLoaded(true);
        } else if (msg.type === "printer") {
          setPrinters((list) => {
            const i = list.findIndex((p) => p.id === msg.printer.id);
            if (i === -1) return [...list, msg.printer];
            const next = list.slice();
            next[i] = msg.printer;
            return next;
          });
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (stopped) return;
        timer = window.setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15000);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, []);

  const value = React.useMemo(
    () => ({ printers, loaded, connected, lastMessageAt, config }),
    [printers, loaded, connected, lastMessageAt, config],
  );
  return <PrintersContext.Provider value={value}>{children}</PrintersContext.Provider>;
}

export function usePrinters(): PrintersState {
  const v = React.useContext(PrintersContext);
  if (!v) throw new Error("usePrinters outside PrintersProvider");
  return v;
}

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = React.useState(Date.now());
  React.useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}
