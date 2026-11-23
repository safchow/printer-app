import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import type { FileMeta } from "./types";

/** The print queue (api/app/queue.py has the rules). */
export type QueueMode = "at" | "idle" | "after";
export type QueueStatus = "scheduled" | "starting" | "started" | "missed" | "failed" | "cancelled" | "needs_check";

export interface QueueItem {
  id: number;
  printer_id: string;
  file_id: number;
  filename: string | null;
  meta: FileMeta;
  mode: QueueMode;
  start_at: string | null;
  window_minutes: number | null;
  gap_minutes: number;
  after_id: number | null;
  position: number;
  status: QueueStatus;
  note: string | null;
  command: string | null;
  job_id: number | null;
  due_at: string | null;
  latest_at: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface PrinterQueue {
  printer_id: string;
  name: string;
  state: string;
  paused: boolean;
  note: string | null;
  plate_ok: boolean;
  plate_confirmed_at: string | null;
  items: QueueItem[];
  done: QueueItem[];
}

export interface ItemBody {
  printer_id: string;
  file_id: number;
  mode: QueueMode;
  start_at?: string | null;
  window_minutes: number | null;
  gap_minutes: number;
  plate_clear: boolean;
}

export const QUEUE_URL = "/api/queue";

export function useQueue() {
  const q = useQuery({
    queryKey: [QUEUE_URL],
    queryFn: async () => {
      const r = await fetch(QUEUE_URL);
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : `the server answered ${r.status}`);
      }
      return (await r.json()) as PrinterQueue[];
    },
    refetchInterval: 5000,
    staleTime: 0,
  });
  return { data: q.data ?? null, error: q.error ? q.error.message : null };
}

export function useRefreshQueue() {
  const qc = useQueryClient();
  return React.useCallback(() => void qc.invalidateQueries({ queryKey: [QUEUE_URL] }), [qc]);
}

async function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    const detail = typeof data.detail === "string" ? data.detail : Array.isArray(data.detail) ? data.detail[0]?.msg : null;
    throw new Error(detail ?? `the server answered ${r.status}`);
  }
  return r.json();
}

export const addToQueue = (body: ItemBody) => send<QueueItem>(QUEUE_URL, "POST", body);
export const editQueueItem = (id: number, body: Partial<ItemBody> & { any_time?: boolean }) =>
  send<QueueItem>(`${QUEUE_URL}/${id}`, "PATCH", body);
export const cancelQueueItem = (id: number) => send<QueueItem>(`${QUEUE_URL}/${id}/cancel`, "POST");
export const moveQueueItem = (id: number, to: number) => send<QueueItem[]>(`${QUEUE_URL}/${id}/move`, "POST", { to });
export const confirmPlate = (printerId: string) =>
  send<PrinterQueue>(`${QUEUE_URL}/printers/${encodeURIComponent(printerId)}/plate`, "POST");
export const pauseQueue = (printerId: string, paused: boolean) =>
  send<PrinterQueue>(`${QUEUE_URL}/printers/${encodeURIComponent(printerId)}/pause`, "POST", { paused });

/** The wait-window choices the dialog offers, in minutes; null = any time. */
export const WINDOWS: { label: string; value: number | null }[] = [
  { label: "30 min", value: 30 },
  { label: "2 hours", value: 120 },
  { label: "6 hours", value: 360 },
  { label: "Any time", value: null },
];

export function windowLabel(m: number | null): string {
  if (m == null) return "Any time";
  if (m < 60) return `${m} min`;
  return m % 60 ? `${(m / 60).toFixed(1)} h` : `${m / 60} h`;
}
