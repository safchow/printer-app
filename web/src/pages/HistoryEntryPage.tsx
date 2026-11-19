import * as React from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { Box, ChevronLeft, DatabaseZap, Video } from "lucide-react";
import { AppHeader } from "@/components/AppHeader";
import { GcodeFileView } from "@/components/PrintView";
import { TimelapsePlayer } from "@/components/TimelapsePlayer";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { usePrinters } from "@/hooks/usePrinters";
import { RESULT_LABEL, bytes, useFile, useHistoryEntry, useTimelapse } from "@/lib/files";
import { clock, dayAndClock, duration } from "@/lib/format";
import type { PrintJob } from "@/lib/types";
import { cn } from "@/lib/utils";

function took(j: PrintJob): number | null {
  if (j.print_duration) return j.print_duration;
  if (j.started_at && j.ended_at) return (new Date(j.ended_at).getTime() - new Date(j.started_at).getTime()) / 1000;
  return null;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 text-[13px]">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate text-right font-mono">{children}</span>
    </div>
  );
}

/** The time-lapse side of the stage: loads frames only once it is shown. */
function TimelapseView({ job }: { job: PrintJob }) {
  const { data, error, reload } = useTimelapse(job.id);
  const message = (title: string, body?: string, retry = false) => (
    <div className="flex size-full flex-col items-center justify-center gap-2 bg-black p-6 text-center text-zinc-200">
      <DatabaseZap className="size-7 text-zinc-400" aria-hidden="true" />
      <strong className="text-[15px] font-semibold">{title}</strong>
      {body && <span className="max-w-sm text-[13px] text-zinc-400">{body}</span>}
      {retry && (
        <Button variant="outline" size="sm" className="mt-1 border-zinc-700 bg-zinc-900 text-zinc-100 hover:bg-zinc-800" onClick={reload}>
          Try again
        </Button>
      )}
    </div>
  );
  if (error) return message("Couldn't load the time-lapse", `The server answered: ${error}.`, true);
  if (!data) return <div className="flex size-full items-center justify-center bg-black text-sm text-zinc-400">Loading the time-lapse…</div>;
  if (!data.available) return message("Time-lapse can't be loaded right now", `The ${job.timelapse_frames} frames are saved, but the storage isn't reachable.`, true);
  if (!data.frames.length) return message("No frames for this print");
  return <TimelapsePlayer jobId={job.id} tl={data} className="size-full" />;
}

export function HistoryEntryPage() {
  const { id } = useParams();
  const jobId = id && /^\d+$/.test(id) ? Number(id) : null;
  const { data: job, error } = useHistoryEntry(jobId);
  const { data: file } = useFile(job?.file_id ?? null);
  const { printers } = usePrinters();
  const [lapse, setLapse] = React.useState(false);

  if (job?.result === "printing") return <Navigate to={`/printers/${job.printer_id}`} replace />;

  const printer = job ? (printers.find((p) => p.id === job.printer_id)?.name ?? job.printer_id) : "";
  const hasLapse = !!job?.timelapse_frames;
  const showLapse = lapse && hasLapse;
  const m = file?.meta ?? {};
  const t = job ? took(job) : null;
  const name = job ? job.filename.split("/").pop()!.replace(/\.(gcode|3mf)$/i, "").replace(/_[A-Za-z-]+_\d+h\d+m\d+s$/, "") : "";

  return (
    <div className="min-h-dvh bg-background">
      <AppHeader />
      <main className="mx-auto flex w-full max-w-[1440px] flex-col gap-5 p-6">
        <div className="flex min-h-10 flex-wrap items-center gap-3">
          <Link to="/history" className="inline-flex items-center gap-1 text-[13px] text-muted-foreground no-underline hover:text-foreground">
            <ChevronLeft className="size-4" aria-hidden="true" />
            History
          </Link>
          {job && (
            <>
              <h1 className="m-0 text-xl font-semibold">{name}</h1>
              <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium">{RESULT_LABEL[job.result]}</span>
              <span className="truncate text-[13px] text-muted-foreground" title={job.filename}>
                {job.filename}
              </span>
            </>
          )}
        </div>

        {error ? (
          <p className="text-sm text-destructive">Couldn't load this print ({error}).</p>
        ) : !job ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div className="grid grid-cols-[repeat(4,minmax(0,1fr))] grid-rows-[minmax(420px,calc(100dvh-16rem))_auto] gap-6">
            <section aria-label={showLapse ? "Time-lapse" : "3D model"} className="relative col-span-3 overflow-hidden rounded-xl border bg-stage">
              {showLapse ? (
                <TimelapseView job={job} />
              ) : job.file_id != null ? (
                <GcodeFileView url={`/api/files/${job.file_id}/raw`} className="size-full" />
              ) : (
                <div className="flex size-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
                  No copy of this print's file was kept, so there's no model to show.
                </div>
              )}
              {hasLapse && (
                <button
                  type="button"
                  onClick={() => setLapse((v) => !v)}
                  aria-pressed={showLapse}
                  className="absolute top-3 right-3 z-20 inline-flex h-9 items-center gap-1.5 rounded-md border bg-background/90 px-3 text-[13px] font-medium text-foreground shadow-sm backdrop-blur hover:bg-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  {showLapse ? <Box className="size-4" aria-hidden="true" /> : <Video className="size-4" aria-hidden="true" />}
                  {showLapse ? "Model" : "Time-lapse"}
                </button>
              )}
            </section>

            <Card className="flex min-h-0 flex-col justify-between gap-6 p-6">
              <div className="flex flex-col gap-2">
                <span className="text-[13px] text-muted-foreground">Result</span>
                <div className={cn("text-[44px] leading-none font-bold tracking-tight", job.result === "complete" && "font-mono text-[56px]")}>
                  {job.result === "complete" ? "100%" : RESULT_LABEL[job.result]}
                </div>
                <span className="text-[13px] text-muted-foreground">on {printer}</span>
              </div>
              <div className="flex flex-col gap-3.5">
                <Row label="Took">
                  {duration(t)}
                  {m.estimated_seconds != null && <span className="text-muted-foreground"> · est. {duration(m.estimated_seconds)}</span>}
                </Row>
                <Row label="Layers">{m.layers ?? "--"}</Row>
                <Row label="Filament">{m.filament_g != null ? `${m.filament_g} g` : "--"}</Row>
              </div>
              <div className="flex items-end justify-between border-t pt-3.5 leading-tight">
                <div className="flex flex-col">
                  <span className="text-[11px] text-muted-foreground">Started</span>
                  <span className="font-mono text-sm">{dayAndClock(job.started_at)}</span>
                </div>
                <div className="flex flex-col text-right">
                  <span className="text-[11px] text-muted-foreground">Ended</span>
                  <span className="font-mono text-sm">{job.ended_at ? clock(job.ended_at) : "--:--"}</span>
                </div>
              </div>
            </Card>

            <Card className="col-span-4 px-6 py-4 text-[13px] text-muted-foreground">
              {hasLapse
                ? `Time-lapse: ${job.timelapse_frames} frames, one per layer · ${bytes(job.timelapse_bytes ?? 0)}`
                : "No time-lapse for this print."}
            </Card>
          </div>
        )}
      </main>
    </div>
  );
}
