import * as React from "react";

/** Only the most recent few, so the activity card keeps a fixed height. */
export const ACTIVITY_LIMIT = 5;

export interface ActionRow {
  id: number;
  action: string;
  values: Record<string, unknown>;
  command: string | null;
  result: "sent" | "refused" | "failed";
  detail: string | null;
  device: string | null;
  at: string;
}

/** The printer's recent action log; `reload` after each press. */
export function useActions(printerId: string, enabled: boolean) {
  const [rows, setRows] = React.useState<ActionRow[]>([]);
  const reload = React.useCallback(() => {
    if (!enabled) return;
    fetch(`/api/printers/${encodeURIComponent(printerId)}/actions?limit=${ACTIVITY_LIMIT}`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setRows)
      .catch(() => {});
  }, [printerId, enabled]);
  React.useEffect(reload, [reload]);
  return { rows, reload };
}
