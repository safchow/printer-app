import type { Printer, PrinterState } from "./types";

let timeZone: string | undefined;
export function setTimeZone(tz: string | undefined) {
  try {
    if (tz) new Intl.DateTimeFormat("en", { timeZone: tz });
    timeZone = tz;
  } catch {
    timeZone = undefined;
  }
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "--:--";
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone }).format(new Date(iso));
}

export function dayAndClock(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = new Date(iso);
  const sameDay = new Intl.DateTimeFormat("en-CA", { timeZone }).format(d) === new Intl.DateTimeFormat("en-CA", { timeZone }).format(new Date());
  if (sameDay) return clock(iso);
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone }).format(d);
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds)) return "--";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}

export function temp(v: number | null | undefined): string {
  return v == null ? "--" : `${Math.round(v)}°`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  return v == null ? "--" : `${(v * 100).toFixed(digits)}%`;
}

export const STATE_LABEL: Record<PrinterState, string> = {
  connecting: "Connecting",
  offline: "Offline",
  idle: "Idle",
  printing: "Printing",
  paused: "Paused",
  complete: "Finished",
  cancelled: "Cancelled",
  error: "Error",
};

export const isActive = (p: Printer) => p.state === "printing" || p.state === "paused";

export const SPEED_PRESETS: [string, number][] = [
  ["Silent", 50],
  ["Standard", 100],
  ["Sport", 130],
  ["Ludicrous", 160],
];

export function speedName(v: number | null | undefined): string {
  if (v == null) return "";
  return SPEED_PRESETS.find(([, n]) => n === v)?.[0] ?? "Custom";
}

export function summary(printers: Printer[]): string {
  const count = (states: PrinterState[]) => printers.filter((p) => states.includes(p.state)).length;
  const parts = [
    [count(["printing"]), "printing"],
    [count(["paused"]), "paused"],
    [count(["idle", "complete", "cancelled"]), "idle"],
    [count(["error"]), "with errors"],
    [count(["offline", "connecting"]), "offline"],
  ] as const;
  return parts
    .filter(([n]) => n > 0)
    .map(([n, label]) => `${n} ${label}`)
    .join(" · ");
}

/** "Today", "Yesterday" or "Mon 16 Nov", in the server's time zone. */
export function dayHeading(iso: string | null | undefined): string {
  if (!iso) return "Unknown day";
  const key = (d: Date) => new Intl.DateTimeFormat("en-CA", { timeZone }).format(d);
  const d = new Date(iso);
  if (key(d) === key(new Date())) return "Today";
  if (key(d) === key(new Date(Date.now() - 86400000))) return "Yesterday";
  return new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone }).format(d);
}

/** "16 Nov", or the time if it's today. */
export function shortDay(iso: string | null | undefined): string {
  if (!iso) return "never";
  const key = (d: Date) => new Intl.DateTimeFormat("en-CA", { timeZone }).format(d);
  const d = new Date(iso);
  if (key(d) === key(new Date())) return `today ${clock(iso)}`;
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone }).format(d);
}

export function dayKey(iso: string | null | undefined): string {
  return iso ? new Intl.DateTimeFormat("en-CA", { timeZone }).format(new Date(iso)) : "";
}
