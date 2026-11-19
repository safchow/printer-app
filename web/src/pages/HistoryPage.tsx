import * as React from "react";
import { Link } from "react-router-dom";
import { Box, ChevronRight } from "lucide-react";
import { AppHeader } from "@/components/AppHeader";
import { usePrinters } from "@/hooks/usePrinters";
import { RESULT_LABEL, useHistory } from "@/lib/files";
import { clock, dayHeading, dayKey, duration } from "@/lib/format";
import type { PrintJob } from "@/lib/types";
import { cn } from "@/lib/utils";

const RESULT_STYLE: Record<PrintJob["result"], string> = {
  printing: "bg-brand text-brand-foreground",
  complete: "border bg-background",
  cancelled: "bg-muted text-muted-foreground",
  error: "bg-offline text-offline-foreground",
  unknown: "bg-muted text-muted-foreground",
};

function SmallThumb({ job }: { job: PrintJob }) {
  const [failed, setFailed] = React.useState(false);
  return (
    <div className="flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-md border bg-muted/60">
      {job.file_id != null && !failed ? (
        <img src={`/api/files/${job.file_id}/thumbnail`} alt="" loading="lazy" className="size-full object-contain p-1" onError={() => setFailed(true)} />
      ) : (
        <Box className="size-5 text-muted-foreground/50" aria-hidden="true" />
      )}
    </div>
  );
}

function elapsed(j: PrintJob): number | null {
  if (j.print_duration) return j.print_duration;
  if (j.started_at && j.ended_at) return (new Date(j.ended_at).getTime() - new Date(j.started_at).getTime()) / 1000;
  return null;
}

export function HistoryPage() {
  const { data, error } = useHistory();
  const { printers, config } = usePrinters();
  const name = (id: string) => printers.find((p) => p.id === id)?.name ?? id;

  const groups: [string, PrintJob[]][] = [];
  for (const j of data ?? []) {
    const k = dayKey(j.started_at);
    if (groups.length && groups[groups.length - 1][0] === k) groups[groups.length - 1][1].push(j);
    else groups.push([k, [j]]);
  }
  const week = (data ?? []).filter((j) => j.started_at && Date.now() - new Date(j.started_at).getTime() < 7 * 86400000);
  const hours = week.reduce((s, j) => s + (elapsed(j) ?? 0), 0) / 3600;
  const finished = week.filter((j) => j.result === "complete").length;

  return (
    <div className="min-h-dvh bg-background">
      <AppHeader />
      <main className="mx-auto flex w-full max-w-[960px] flex-col gap-6 p-4 md:p-6">
        <div>
          <h1 className="m-0 text-xl font-semibold">History</h1>
          <p className="m-0 mt-0.5 text-sm text-muted-foreground">Every print the app has seen start, newest first. Open one to see its model and time-lapse.</p>
        </div>

        {data && data.length > 0 && (
          <div className="grid grid-cols-3 gap-4">
            {[
              ["Prints this week", String(week.length)],
              ["Finished", week.length ? `${finished} of ${week.length}` : "--"],
              ["Hours printing", hours ? hours.toFixed(1) : "0"],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl border px-4 py-3">
                <div className="text-xs text-muted-foreground">{label}</div>
                <div className="mt-0.5 text-lg font-semibold tabular-nums">{value}</div>
              </div>
            ))}
          </div>
        )}

        {config && config.capture_files === false ? (
          <p className="text-sm text-muted-foreground">Print history is part of file capture, which is turned off on this server (CAPTURE_FILES).</p>
        ) : error ? (
          <p className="text-sm text-destructive">Couldn't load the history ({error}).</p>
        ) : !data ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : data.length === 0 ? (
          <p className="rounded-xl border border-dashed px-6 py-16 text-center text-sm text-muted-foreground">
            No prints yet. The next one you start shows up here.
          </p>
        ) : (
          groups.map(([k, jobs]) => (
            <section key={k} className="flex flex-col gap-2">
              <h2 className="m-0 text-sm font-semibold text-muted-foreground">{dayHeading(jobs[0].started_at)}</h2>
              <ul className="m-0 flex list-none flex-col divide-y rounded-xl border p-0">
                {jobs.map((j) => {
                  const t = elapsed(j);
                  const to = j.result === "printing" ? `/printers/${j.printer_id}` : `/history/${j.id}`;
                  return (
                    <li key={j.id}>
                      <Link to={to} className="flex items-center gap-3.5 px-4 py-3 text-foreground no-underline hover:bg-muted/50">
                        <SmallThumb job={j} />
                        <div className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium" title={j.filename}>
                            {j.filename}
                          </span>
                          <span className="block text-[13px] text-muted-foreground">
                            {name(j.printer_id)} · {clock(j.started_at)}
                            {j.ended_at ? `–${clock(j.ended_at)}` : ""}
                            {t ? ` · ${duration(t)}` : ""}
                            {j.file_id == null ? " · no copy kept" : ""}
                          </span>
                        </div>
                        <span className={cn("inline-flex shrink-0 items-center rounded-full px-2.5 py-0.5 text-xs font-medium", RESULT_STYLE[j.result])}>
                          {RESULT_LABEL[j.result]}
                        </span>
                        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))
        )}
      </main>
    </div>
  );
}
