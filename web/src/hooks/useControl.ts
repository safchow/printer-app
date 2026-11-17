import * as React from "react";

export type ControlAction = "pause" | "resume" | "cancel" | "speed" | "fan" | "temp" | "light";

/**
 * Sends one control to printer-api. The server does every check; this only
 * keeps one press in flight and shows why a press didn't go through.
 */
export function useControl(printerId: string, onDone?: () => void) {
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const timer = React.useRef<number>();

  const run = React.useCallback(
    async (action: ControlAction, body?: object, key: string = action) => {
      setBusy(key);
      setError(null);
      try {
        const r = await fetch(`/api/printers/${encodeURIComponent(printerId)}/${action}`, {
          method: "POST",
          headers: body ? { "Content-Type": "application/json" } : undefined,
          body: body ? JSON.stringify(body) : undefined,
        });
        if (!r.ok) {
          const data = await r.json().catch(() => ({}));
          throw new Error(typeof data.detail === "string" ? data.detail : `the server answered ${r.status}`);
        }
        return true;
      } catch (e) {
        setError(`Not sent: ${e instanceof Error ? e.message : e}.`);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setError(null), 8000);
        return false;
      } finally {
        setBusy(null);
        onDone?.();
      }
    },
    [printerId, onDone],
  );

  return { busy, error, run };
}
