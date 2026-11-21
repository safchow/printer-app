import * as React from "react";
import { type QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FileMeta, LabelCount, Printer, PrintJob, StoredFile, Timelapse } from "./types";

/** GET some JSON. */
export async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`the server answered ${r.status}`);
  return r.json();
}

/**
 * GET some JSON through the shared query cache (keyed by URL). Pages that
 * remount (Files <-> History) get the cached copy at once and refresh it in
 * the background when it's older than `staleTime`. `reload()` asks again.
 * `data` stays while a reload runs.
 */
export function useJson<T>(url: string | null, opts?: { staleTime?: number }) {
  const q = useQuery({
    queryKey: [url],
    queryFn: () => getJson<T>(url!),
    enabled: url != null,
    // Only override when asked: an explicit `undefined` would replace the app-wide default.
    ...(opts?.staleTime !== undefined && { staleTime: opts.staleTime }),
  });
  const { refetch } = q;
  const reload = React.useCallback(() => void refetch(), [refetch]);
  return { data: q.data ?? null, error: q.error ? q.error.message : null, reload };
}

/** URLs whose answers change when a file is added, relabelled, deleted or printed. */
const LIBRARY_PREFIXES = ["/api/files", "/api/labels", "/api/history"];

export function invalidateLibrary(qc: QueryClient, prefixes: string[] = LIBRARY_PREFIXES) {
  return qc.invalidateQueries({
    predicate: (query) => {
      const key = query.queryKey[0];
      return typeof key === "string" && prefixes.some((p) => key.startsWith(p));
    },
  });
}

/** Mark everything file-related stale; whatever is on screen refetches now, the rest on next visit. */
export function useInvalidateLibrary() {
  const qc = useQueryClient();
  return React.useCallback(() => void invalidateLibrary(qc), [qc]);
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
export const useHistoryEntry = (id: number | null) => useJson<PrintJob>(id == null ? null : `/api/history/${id}`);
export const useTimelapse = (id: number | null) => useJson<Timelapse>(id == null ? null : `/api/history/${id}/timelapse`);
export const frameUrl = (jobId: number, n: number) => `/api/history/${jobId}/timelapse/${n}.jpg`;

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

/** Ask the server to upload our copy of a stored file to a printer and start it. Throws with the server's reason. */
export async function printFile(printerId: string, fileId: number): Promise<void> {
  const r = await fetch(`/api/printers/${encodeURIComponent(printerId)}/print`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: fileId }),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `the server answered ${r.status}`);
  }
}

/** The printer Print sends a file to: the one it came from, else the only (first) printer. */
export function printTarget(f: StoredFile, printers: Printer[]): Printer | undefined {
  return printers.find((p) => p.id === f.printer_id) ?? printers[0];
}

/** Why Print can't be pressed right now, or null when it can. The server checks again. */
export function printBlocker(printer: Printer | undefined, controlsEnabled: boolean): string | null {
  if (!controlsEnabled) return "Printing from the app needs controls turned on (CONTROLS_ENABLED).";
  if (!printer) return "No printer is set up.";
  switch (printer.state) {
    case "printing":
    case "paused":
      return `${printer.name} is busy with another print.`;
    case "offline":
    case "connecting":
      return `${printer.name} is offline.`;
    case "error":
      return `${printer.name} reports an error. Check it before printing.`;
  }
  return null;
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
