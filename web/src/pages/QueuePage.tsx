import * as React from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowUp, CircleCheck, Pencil, X } from "lucide-react";
import { AppHeader } from "@/components/AppHeader";
import { ScheduleDialog } from "@/components/ScheduleDialog";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { usePrinters } from "@/hooks/usePrinters";
import { material } from "@/lib/files";
import { dayAndClock, duration } from "@/lib/format";
import {
  cancelQueueItem,
  confirmPlate,
  moveQueueItem,
  pauseQueue,
  type PrinterQueue,
  type QueueItem,
  type QueueStatus,
  useQueue,
  useRefreshQueue,
  windowLabel,
} from "@/lib/queue";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Record<QueueStatus, string> = {
  scheduled: "border bg-background",
  starting: "bg-brand text-brand-foreground",
  started: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200",
  missed: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-200",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-200",
  cancelled: "bg-muted text-muted-foreground",
  needs_check: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
};
const STATUS_LABEL: Record<QueueStatus, string> = {
  scheduled: "Scheduled",
  starting: "Starting",
  started: "Started",
  missed: "Missed",
  failed: "Failed",
  cancelled: "Cancelled",
  needs_check: "Needs check",
};

// The status badge already says "Starting", "Scheduled" and so on, so only show a note that adds to it.
function extraNote(i: { status: QueueStatus; note?: string | null }): string | null {
  if (!i.note) return null;
  const plain = (t: string) => t.replace(/[.…\s]+$/, "").toLowerCase();
  return plain(i.note) === plain(STATUS_LABEL[i.status]) ? null : i.note;
}

function Badge({ className, children }: { className?: string; children: React.ReactNode }) {
  return <span className={cn("inline-block rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap", className)}>{children}</span>;
}

function starts(i: QueueItem, items: QueueItem[]): React.ReactNode {
  if (i.mode === "at") return <>At {dayAndClock(i.start_at)}</>;
  if (i.mode === "idle") return <>When idle</>;
  const above = items.findIndex((x) => x.id === i.after_id);
  const what = above >= 0 ? `#${above + 1}` : i.after_id ? "the last print" : "nothing (runs when idle)";
  return (
    <>
      After {what}
      {i.gap_minutes ? <span className="block text-[13px] text-muted-foreground">+ {i.gap_minutes} min</span> : null}
    </>
  );
}

function fileLine(i: QueueItem): string {
  const parts = [material(i.meta), i.meta.estimated_seconds != null ? duration(i.meta.estimated_seconds) : null].filter(Boolean);
  return parts.join(" · ");
}

function PrinterSection({ q }: { q: PrinterQueue }) {
  const { printers } = usePrinters();
  const refresh = useRefreshQueue();
  const printer = printers.find((p) => p.id === q.printer_id);
  const [editing, setEditing] = React.useState<QueueItem | null>(null);
  const [again, setAgain] = React.useState<QueueItem | null>(null);
  const [problem, setProblem] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setProblem(null);
    try {
      await fn();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const state = printer?.state ?? q.state;
  const printing = state === "printing" || state === "paused";
  const head = q.items[0];
  const needsPlate = head?.note === "Clear the plate";
  const sub = [
    printer?.name ?? q.name,
    printing ? `printing ${printer?.filename ?? ""}${printer?.eta_at ? `, done around ${dayAndClock(printer.eta_at)}` : ""}` : state,
    `${q.items.length} waiting`,
  ].join(" · ");

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="m-0 text-sm text-muted-foreground">{sub}</p>
        <label className="flex items-center gap-2 text-sm">
          {q.paused ? "Queue paused" : "Queue running"}
          <Switch checked={!q.paused} disabled={busy} onCheckedChange={(on) => act(() => pauseQueue(q.printer_id, !on))} aria-label="Queue running" />
        </label>
      </div>

      {q.paused && q.note && (
        <p role="status" className="m-0 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm dark:border-red-900 dark:bg-red-950/40">
          {q.note} Look at the item below, then switch the queue back on.
        </p>
      )}

      {head && !q.plate_ok && (needsPlate || printing) && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm dark:border-amber-900 dark:bg-amber-950/40">
          <Badge className="bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200">{printing ? "Heads-up" : "Clear the plate"}</Badge>
          <span className="min-w-0 flex-1">
            <b>{head.filename}</b>{" "}
            {printing
              ? "is next, but the print running now will leave a part on the bed. Clear it after that print ends and tap Plate cleared."
              : "is ready to go once the bed is empty. Remove the last part, then tap Plate cleared."}
          </span>
          <Button size="sm" variant={printing ? "outline" : "brand"} disabled={printing || busy} onClick={() => act(() => confirmPlate(q.printer_id))}>
            <CircleCheck /> Plate cleared
          </Button>
        </div>
      )}

      {problem && (
        <p role="alert" className="m-0 text-sm text-destructive">
          {problem}
        </p>
      )}

      {q.items.length === 0 ? (
        <p className="m-0 rounded-xl border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
          Nothing queued. Open a file in <Link to="/files" className="text-foreground underline underline-offset-2">Files</Link> and press Schedule.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b text-left text-[13px] text-muted-foreground">
                <th className="w-8 py-2.5 pr-2 font-medium">#</th>
                <th className="py-2.5 pr-4 font-medium">File</th>
                <th className="py-2.5 pr-4 font-medium">Starts</th>
                <th className="py-2.5 pr-4 font-medium">Waits up to</th>
                <th className="py-2.5 pr-4 font-medium">Status</th>
                <th className="py-2.5 font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {q.items.map((i, n) => (
                <tr key={i.id} className="border-b border-border/60 align-middle">
                  <td className="py-3.5 pr-2 text-muted-foreground">{n + 1}</td>
                  <td className="py-3.5 pr-4">
                    <span className="block font-medium break-all">{i.filename ?? "(deleted file)"}</span>
                    <span className="text-[13px] text-muted-foreground">{fileLine(i)}</span>
                  </td>
                  <td className="py-3.5 pr-4">{starts(i, q.items)}</td>
                  <td className="py-3.5 pr-4">
                    {i.mode === "idle" ? "Any time" : i.latest_at ? `until ${dayAndClock(i.latest_at)}` : windowLabel(i.window_minutes)}
                  </td>
                  <td className="py-3.5 pr-4">
                    <Badge className={STATUS_STYLE[i.status]}>{STATUS_LABEL[i.status]}</Badge>
                    {extraNote(i) && <span className="mt-1 block text-[13px] text-muted-foreground">{extraNote(i)}</span>}
                  </td>
                  <td className="py-3.5">
                    <div className="flex justify-end gap-1">
                      <Button size="icon" variant="ghost" className="size-8" aria-label="Move up" disabled={busy || n === 0 || i.status !== "scheduled"} onClick={() => act(() => moveQueueItem(i.id, n - 1))}>
                        <ArrowUp />
                      </Button>
                      <Button size="icon" variant="ghost" className="size-8" aria-label="Move down" disabled={busy || n === q.items.length - 1 || i.status !== "scheduled"} onClick={() => act(() => moveQueueItem(i.id, n + 1))}>
                        <ArrowDown />
                      </Button>
                      <Button size="sm" variant="outline" disabled={busy || i.status !== "scheduled"} onClick={() => setEditing(i)}>
                        <Pencil /> Edit
                      </Button>
                      <Button size="sm" variant="outline" disabled={busy || i.status !== "scheduled"} onClick={() => act(() => cancelQueueItem(i.id))}>
                        <X /> Cancel
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {q.done.length > 0 && (
        <div className="flex flex-col gap-1">
          <h2 className="m-0 mt-4 text-[15px] font-semibold">Done</h2>
          <ul className="m-0 list-none p-0">
            {q.done.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border/60 py-3 text-sm">
                <span className="min-w-[200px] flex-1 font-medium break-all">{i.filename ?? "(deleted file)"}</span>
                <span className="text-muted-foreground">
                  {i.mode === "at" ? `At ${dayAndClock(i.start_at)}` : i.mode === "idle" ? "When idle" : "After previous"}
                </span>
                <Badge className={STATUS_STYLE[i.status]}>
                  {STATUS_LABEL[i.status]} {dayAndClock(i.status === "started" ? i.started_at : i.finished_at)}
                </Badge>
                {i.status !== "started" && extraNote(i) && <span className="text-[13px] text-muted-foreground">{extraNote(i)}</span>}
                <span className="ml-auto flex gap-2">
                  {i.job_id != null && (
                    <Link to={`/history/${i.job_id}`} className="text-muted-foreground underline underline-offset-2">
                      History
                    </Link>
                  )}
                  {i.status !== "started" && i.filename && (
                    <Button size="sm" variant="outline" onClick={() => setAgain(i)}>
                      Run again…
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {editing && (
        <ScheduleDialog
          open
          onOpenChange={(o) => !o && setEditing(null)}
          file={{ id: editing.file_id, filename: editing.filename ?? "file", meta: editing.meta }}
          printer={printer}
          item={editing}
        />
      )}
      {again && (
        <ScheduleDialog
          open
          onOpenChange={(o) => !o && setAgain(null)}
          file={{ id: again.file_id, filename: again.filename ?? "file", meta: again.meta }}
          printer={printer}
        />
      )}
    </section>
  );
}

export function QueuePage() {
  const { data, error } = useQueue();
  const several = (data?.length ?? 0) > 1;
  return (
    <div className="min-h-dvh bg-background">
      <AppHeader />
      <main className="mx-auto flex w-full max-w-[1120px] flex-col gap-6 p-4 md:p-6">
        <div>
          <h1 className="m-0 text-xl font-semibold">Queue</h1>
          <p className="m-0 mt-0.5 text-sm text-muted-foreground">
            Prints that start later, one at a time, only when the printer is free and the plate is confirmed clear.
          </p>
        </div>
        {error && (
          <p role="alert" className="m-0 text-sm text-destructive">
            Couldn't load the queue: {error}.
          </p>
        )}
        {data?.map((q) => (
          <div key={q.printer_id} className="flex flex-col gap-2">
            {several && <h2 className="m-0 text-base font-semibold">{q.name}</h2>}
            <PrinterSection q={q} />
          </div>
        ))}
      </main>
    </div>
  );
}
