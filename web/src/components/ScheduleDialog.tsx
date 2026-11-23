import * as React from "react";
import { CalendarClock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { usePrinters } from "@/hooks/usePrinters";
import { material } from "@/lib/files";
import { clock, dayAndClock, duration } from "@/lib/format";
import { addToQueue, editQueueItem, type QueueItem, type QueueMode, useRefreshQueue, WINDOWS } from "@/lib/queue";
import type { FileMeta, Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

const MODES: { value: QueueMode; label: string }[] = [
  { value: "at", label: "At a time" },
  { value: "idle", label: "When idle" },
  { value: "after", label: "After previous" },
];
const QUICK_HOURS = [1, 3, 6, 12];

/** A Date as the value a datetime-local input wants (the browser's own time zone). */
function localInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function inHours(h: number): Date {
  const d = new Date(Date.now() + h * 3600_000);
  d.setSeconds(0, 0);
  return d;
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={cn(
        "rounded-full border px-3 py-1 text-[13px]",
        on ? "border-brand bg-brand/10 text-brand" : "border-border hover:bg-muted",
      )}
    >
      {children}
    </button>
  );
}

/**
 * Add a stored file to a printer's queue, or change a queued item (`item`).
 * The server checks everything again; this only explains what will happen.
 */
export function ScheduleDialog({
  open,
  onOpenChange,
  file,
  printer,
  item,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  file: { id: number; filename: string; meta: FileMeta };
  printer: Printer | undefined;
  item?: QueueItem;
  onDone?: (item: QueueItem) => void;
}) {
  const { config } = usePrinters();
  const refresh = useRefreshQueue();
  const [mode, setMode] = React.useState<QueueMode>("at");
  const [startAt, setStartAt] = React.useState<string>(localInput(inHours(6)));
  const [quick, setQuick] = React.useState<number | null>(6);
  const [windowMin, setWindowMin] = React.useState<number | null>(120);
  const [gap, setGap] = React.useState(0);
  const [plate, setPlate] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setError(null);
    setBusy(false);
    if (item) {
      setMode(item.mode);
      setStartAt(localInput(item.start_at ? new Date(item.start_at) : inHours(6)));
      setQuick(null);
      setWindowMin(item.window_minutes);
      setGap(item.gap_minutes);
    } else {
      setMode("at");
      setStartAt(localInput(inHours(6)));
      setQuick(6);
      setWindowMin(120);
      setGap(0);
      setPlate(false);
    }
  }, [open, item]);

  const printing = printer?.state === "printing" || printer?.state === "paused";
  const start = startAt ? new Date(startAt) : null;
  const est = file.meta.estimated_seconds;
  const mat = material(file.meta);
  const name = printer?.name ?? "the printer";
  const latest = start && windowMin != null ? new Date(start.getTime() + windowMin * 60_000) : null;

  let summary: React.ReactNode;
  if (mode === "at" && start) {
    const finish = est != null ? new Date(start.getTime() + est * 1000) : null;
    summary = (
      <>
        Starts at <b>{dayAndClock(start.toISOString())}</b> if {name} is free.{" "}
        {latest ? (
          <>
            If it's busy, it waits until <b>{clock(latest.toISOString())}</b>, then is marked Missed.
          </>
        ) : (
          <>If it's busy, it waits for as long as it takes.</>
        )}
        {finish && <> Should finish around {dayAndClock(finish.toISOString())}.</>}
      </>
    );
  } else if (mode === "idle") {
    summary = <>Starts as soon as {name} is free and the plate is confirmed clear.</>;
  } else {
    summary = (
      <>
        Starts {gap ? `${gap} min ` : ""}after the item above it finishes successfully, once you've cleared the plate.
        {windowMin != null ? ` If it can't start within ${WINDOWS.find((w) => w.value === windowMin)?.label ?? `${windowMin} min`} of that, it's marked Missed.` : ""} If that print fails or is cancelled, this one holds.
      </>
    );
  }

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = {
        mode,
        start_at: mode === "at" && start ? start.toISOString() : null,
        window_minutes: mode === "idle" ? null : windowMin,
        gap_minutes: mode === "after" ? gap : 0,
      };
      const saved = item
        ? await editQueueItem(item.id, { ...body, any_time: body.window_minutes == null })
        : await addToQueue({ ...body, printer_id: printer!.id, file_id: file.id, plate_clear: plate && !printing });
      refresh();
      onDone?.(saved);
      onOpenChange(false);
    } catch (e) {
      setError(`Not saved: ${(e as Error).message}.`);
    } finally {
      setBusy(false);
    }
  };

  const blocker = !printer ? "No printer is set up." : !config?.capture_files ? "The queue needs file capture turned on." : null;
  const badTime = mode === "at" && (!start || isNaN(start.getTime()) || start.getTime() < Date.now() - 60_000);

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <DialogContent>
        <DialogTitle className="m-0 text-base font-semibold break-words">
          {item ? "Change" : "Schedule"} {file.filename}
        </DialogTitle>
        <DialogDescription className="m-0 text-sm text-muted-foreground">
          on {name}
          {mat ? ` · ${mat}` : ""}
          {est != null ? ` · about ${duration(est)}` : ""}
        </DialogDescription>

        <div role="radiogroup" aria-label="Start" className="flex rounded-lg bg-muted p-1">
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              role="radio"
              aria-checked={mode === m.value}
              onClick={() => setMode(m.value)}
              className={cn(
                "flex-1 rounded-md px-2 py-2 text-sm font-medium",
                mode === m.value ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
              )}
            >
              {m.label}
            </button>
          ))}
        </div>

        {mode === "at" && (
          <div className="flex flex-col gap-2">
            <span className="text-[13px] text-muted-foreground">Start</span>
            <div className="flex flex-wrap gap-1.5">
              {QUICK_HOURS.map((h) => (
                <Chip
                  key={h}
                  on={quick === h}
                  onClick={() => {
                    setQuick(h);
                    setStartAt(localInput(inHours(h)));
                  }}
                >
                  In {h} h
                </Chip>
              ))}
            </div>
            <input
              type="datetime-local"
              aria-label="Start time"
              value={startAt}
              onChange={(e) => {
                setQuick(null);
                setStartAt(e.target.value);
              }}
              className="h-10 rounded-md border bg-background px-3 text-sm"
            />
          </div>
        )}

        {mode === "after" && (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-[13px] text-muted-foreground">Gap after the print above</span>
            <input
              type="number"
              min={0}
              max={1440}
              value={gap}
              onChange={(e) => setGap(Math.max(0, Math.min(1440, Number(e.target.value) || 0)))}
              className="h-9 w-20 rounded-md border bg-background px-2 text-sm"
            />
            min
          </label>
        )}

        {mode !== "idle" && (
          <div className="flex flex-col gap-2">
            <span className="text-[13px] text-muted-foreground">If the printer is busy then, wait up to</span>
            <div className="flex flex-wrap gap-1.5">
              {WINDOWS.map((w) => (
                <Chip key={w.label} on={windowMin === w.value} onClick={() => setWindowMin(w.value)}>
                  {w.label}
                </Chip>
              ))}
            </div>
          </div>
        )}

        {!item && (
          <label className={cn("flex items-start gap-3 rounded-lg border px-3.5 py-3", printing && "opacity-60")}>
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-brand"
              checked={plate && !printing}
              disabled={printing}
              onChange={(e) => setPlate(e.target.checked)}
            />
            <span className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">The plate is clear now</span>
              <span className="text-[13px] text-muted-foreground">
                {printing
                  ? `${name} is printing, so the plate won't be clear. Confirm on the Queue page after that print ends.`
                  : "If any print ends before this one starts, the app waits for you to tap Plate cleared on the Queue page."}
              </span>
            </span>
          </label>
        )}

        <p className="m-0 rounded-lg bg-muted/60 px-3.5 py-3 text-sm leading-relaxed">{summary}</p>

        {(error || blocker || badTime) && (
          <p role="alert" className="m-0 text-sm text-destructive">
            {error ?? blocker ?? "Pick a start time in the future."}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <DialogClose asChild>
            <Button variant="outline" disabled={busy}>
              Cancel
            </Button>
          </DialogClose>
          <Button variant="brand" onClick={save} disabled={busy || !!blocker || badTime}>
            <CalendarClock /> {busy ? "Saving…" : item ? "Save" : "Add to queue"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
