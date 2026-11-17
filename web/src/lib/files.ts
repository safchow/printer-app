import * as React from "react";
import type { FileMeta, LabelCount, PrintJob, StoredFile } from "./types";

/** GET some JSON; `reload()` asks again. `data` stays while a reload runs. */
export function useJson<T>(url: string | null) {
  const [data, setData] = React.useState<T | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    if (!url) return;
    let stop = false;
    fetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`the server answered ${r.status}`))))
      .then((d: T) => {
        if (stop) return;
        setData(d);
        setError(null);
      })
      .catch((e: Error) => !stop && setError(e.message));
    return () => {
      stop = true;
    };
  }, [url, tick]);
  const reload = React.useCallback(() => setTick((t) => t + 1), []);
  return { data, error, reload };
}

export type FileFilter = { kind: "inbox" } | { kind: "all" } | { kind: "label"; name: string };

export function filesUrl(f: FileFilter): string {
  if (f.kind === "inbox") return "/api/files?inbox=true";
  if (f.kind === "label") return `/api/files?label=${encodeURIComponent(f.name)}`;
  return "/api/files";
}

export const useFiles = (f: FileFilter) => useJson<StoredFile[]>(filesUrl(f));
export const useLabels = () => useJson<LabelCount[]>("/api/labels");
export const useFile = (id: number | null) => useJson<StoredFile>(id == null ? null : `/api/files/${id}`);
export const useHistory = () => useJson<PrintJob[]>("/api/history?limit=500");

export async function setLabel(id: number, label: string | null): Promise<StoredFile> {
  const r = await fetch(`/api/files/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  if (!r.ok) throw new Error(`the server answered ${r.status}`);
  return r.json();
}

export async function deleteFile(id: number): Promise<void> {
  const r = await fetch(`/api/files/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`the server answered ${r.status}`);
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** "PETG-CF" or "PLA + PETG" */
export function material(m: FileMeta): string | null {
  return m.filament_type?.length ? m.filament_type.join(" + ") : null;
}

/** Slicer colours are "#RRGGBB"; anything else is dropped. */
export function colours(m: FileMeta): string[] {
  return (m.filament_colour ?? []).filter((c) => /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(c)).slice(0, 4);
}

export function printedLine(f: StoredFile, when: (iso: string) => string): string {
  if (!f.prints) return "Not printed yet";
  const times = f.prints === 1 ? "Printed once" : `Printed ${f.prints} times`;
  return f.last_printed ? `${times} · last ${when(f.last_printed)}` : times;
}

export const RESULT_LABEL: Record<PrintJob["result"], string> = {
  printing: "Printing",
  complete: "Finished",
  cancelled: "Cancelled",
  error: "Failed",
  unknown: "Unknown",
};
