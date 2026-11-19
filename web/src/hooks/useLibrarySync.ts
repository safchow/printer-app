import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { usePrinters } from "@/hooks/usePrinters";
import { invalidateLibrary } from "@/lib/files";
import type { Printer } from "@/lib/types";

/** States that only describe the link to the printer, not a print. */
const LINK_STATES = new Set<Printer["state"]>(["connecting", "offline"]);

/**
 * When a print starts, finishes, fails or is cancelled (seen on the existing
 * WebSocket, no extra requests to the printer), the history and print counts
 * change, so mark them stale.
 */
export function useLibrarySync() {
  const qc = useQueryClient();
  const { printers } = usePrinters();
  const seen = React.useRef(new Map<string, string>());

  React.useEffect(() => {
    let changed = false;
    for (const p of printers) {
      if (LINK_STATES.has(p.state)) continue;
      const sig = `${p.state}|${p.filename ?? ""}`;
      const before = seen.current.get(p.id);
      if (before !== undefined && before !== sig) changed = true;
      seen.current.set(p.id, sig);
    }
    if (changed) void invalidateLibrary(qc, ["/api/history", "/api/files"]);
  }, [printers, qc]);
}

export function LibrarySync() {
  useLibrarySync();
  return null;
}
