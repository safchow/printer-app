import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Link, useSearchParams } from "react-router-dom";
import { Box, CalendarClock, CheckCircle2, Download, Inbox, Printer as PrinterIcon, Search, Tag, Trash2, X } from "lucide-react";
import { AppHeader } from "@/components/AppHeader";
import { GcodeFileView } from "@/components/PrintView";
import { ScheduleDialog } from "@/components/ScheduleDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { usePrinters } from "@/hooks/usePrinters";
import {
  bytes,
  colours,
  deleteFile,
  type FileFilter,
  material,
  printBlocker,
  printedLine,
  printFile,
  printTarget,
  RESULT_LABEL,
  setLabel,
  useFile,
  useInvalidateLibrary,
  useFiles,
  useLabels,
} from "@/lib/files";
import { dayAndClock, duration, shortDay } from "@/lib/format";
import type { LabelCount, Printer, StoredFile } from "@/lib/types";
import { cn } from "@/lib/utils";

function useFilter(): [FileFilter, (f: FileFilter) => void] {
  const [params, setParams] = useSearchParams();
  const label = params.get("label");
  // Files opens on All files; ?inbox shows the Inbox.
  const filter: FileFilter = params.get("inbox") != null ? { kind: "inbox" } : label ? { kind: "label", name: label } : { kind: "all" };
  const set = (f: FileFilter) => {
    const next = new URLSearchParams(params);
    next.delete("label");
    next.delete("all");
    next.delete("inbox");
    if (f.kind === "inbox") next.set("inbox", "");
    if (f.kind === "label") next.set("label", f.name);
    setParams(next, { replace: true });
  };
  return [filter, set];
}

function Swatches({ f, className }: { f: StoredFile; className?: string }) {
  const cs = colours(f.meta);
  if (!cs.length) return null;
  return (
    <span className={cn("inline-flex gap-1", className)} aria-hidden="true">
      {cs.map((c, i) => (
        <span key={i} className="size-3 rounded-full border border-black/15" style={{ background: c.slice(0, 7) }} />
      ))}
    </span>
  );
}

function Thumb({ f, className }: { f: StoredFile; className?: string }) {
  return (
    <div className={cn("flex items-center justify-center bg-muted/60", className)}>
      {f.thumbnail ? (
        <img src={`/api/files/${f.id}/thumbnail`} alt="" loading="lazy" className="h-full w-full object-contain p-3" />
      ) : (
        <Box className="size-10 text-muted-foreground/50" aria-hidden="true" />
      )}
    </div>
  );
}

function FileCard({ f, showLabel, onOpen }: { f: StoredFile; showLabel: boolean; onOpen: () => void }) {
  const mat = material(f.meta);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex cursor-pointer flex-col overflow-hidden rounded-xl border bg-card text-left transition-colors hover:border-ring focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <div className="relative">
        <Thumb f={f} className="aspect-[4/3] w-full border-b" />
        {showLabel && f.label && (
          <span className="absolute top-2.5 left-2.5 max-w-[80%] truncate rounded-full border bg-background/90 px-2 py-0.5 text-xs">{f.label}</span>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-1.5 p-3.5">
        <span className="truncate text-sm font-medium" title={f.filename}>
          {f.filename}
        </span>
        <span className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Swatches f={f} />
          <span className="truncate">
            {[mat, f.meta.estimated_seconds != null ? duration(f.meta.estimated_seconds) : null, f.meta.filament_g != null ? `${f.meta.filament_g} g` : null]
              .filter(Boolean)
              .join(" · ") || bytes(f.size)}
          </span>
        </span>
        <span className="truncate text-[13px] text-muted-foreground">{printedLine(f, shortDay)}</span>
      </div>
    </button>
  );
}

function Sidebar({ labels, filter, setFilter, total }: { labels: LabelCount[]; filter: FileFilter; setFilter: (f: FileFilter) => void; total: number }) {
  const inbox = labels.find((l) => l.name == null)?.files ?? 0;
  const named = labels.filter((l): l is { name: string; files: number } => l.name != null);
  const item = (active: boolean) =>
    cn(
      "flex w-full shrink-0 cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm whitespace-nowrap md:w-full",
      active ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
    );
  return (
    <nav aria-label="Labels" className="flex gap-1 overflow-x-auto pb-1 md:flex-col md:overflow-visible md:pb-0">
      <button type="button" className={cn(item(filter.kind === "all"), "w-auto")} onClick={() => setFilter({ kind: "all" })}>
        <Box className="size-4" aria-hidden="true" />
        <span className="flex-1">All files</span>
        <span className="text-xs">{total}</span>
      </button>
      <button type="button" className={cn(item(filter.kind === "inbox"), "w-auto")} onClick={() => setFilter({ kind: "inbox" })}>
        <Inbox className="size-4" aria-hidden="true" />
        <span className="flex-1">Inbox</span>
        {inbox > 0 && <span className="rounded-full bg-brand px-1.5 text-xs font-medium text-brand-foreground">{inbox}</span>}
      </button>
      {named.length > 0 && <div className="hidden px-3 pt-4 pb-1 text-xs font-medium text-muted-foreground md:block">Labels</div>}
      {named.map((l) => (
        <button
          key={l.name}
          type="button"
          className={cn(item(filter.kind === "label" && filter.name === l.name), "w-auto")}
          onClick={() => setFilter({ kind: "label", name: l.name })}
        >
          <Tag className="size-4" aria-hidden="true" />
          <span className="flex-1 truncate">{l.name}</span>
          <span className="text-xs">{l.files}</span>
        </button>
      ))}
    </nav>
  );
}

function LabelPicker({ f, labels, onChange }: { f: StoredFile; labels: LabelCount[]; onChange: (label: string | null) => Promise<void> }) {
  const [text, setText] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const pick = async (label: string | null) => {
    setBusy(true);
    try {
      await onChange(label);
      setText("");
    } finally {
      setBusy(false);
    }
  };
  const named = labels.map((l) => l.name).filter((n): n is string => n != null);
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap gap-1.5">
        {named.map((n) => (
          <button
            key={n}
            type="button"
            disabled={busy}
            aria-pressed={f.label === n}
            onClick={() => pick(f.label === n ? null : n)}
            className={cn(
              "cursor-pointer rounded-full border px-3 py-1 text-[13px] transition-colors disabled:opacity-50",
              f.label === n ? "border-brand bg-brand text-brand-foreground" : "hover:bg-muted",
            )}
          >
            {n}
          </button>
        ))}
        {f.label && (
          <button type="button" disabled={busy} onClick={() => pick(null)} className="cursor-pointer rounded-full px-3 py-1 text-[13px] text-muted-foreground hover:bg-muted">
            Back to Inbox
          </button>
        )}
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) void pick(text.trim());
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={60}
          placeholder="New label"
          aria-label="New label"
          className="h-9 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Button type="submit" size="sm" variant="outline" disabled={busy || !text.trim()}>
          Add
        </Button>
      </form>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="m-0 text-sm">{value}</dd>
    </div>
  );
}

/** "Print" on a file: confirm, then one request to the server, which checks everything again. */
function PrintConfirm({
  f,
  printer,
  open,
  onOpenChange,
  onSent,
}: {
  f: StoredFile;
  printer: Printer | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSent: () => void;
}) {
  const { config } = usePrinters();
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const blocker = printBlocker(printer, !!config?.controls_enabled);
  const m = f.meta;
  const mat = material(m);

  React.useEffect(() => {
    if (open) setError(null);
  }, [open]);

  const start = async () => {
    if (!printer || blocker) return;
    setBusy(true);
    setError(null);
    try {
      await printFile(printer.id, f.id);
      onSent();
      onOpenChange(false);
    } catch (e) {
      setError(`Not started: ${(e as Error).message}.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <DialogContent>
        <DialogTitle className="m-0 text-base font-semibold break-words">Print {f.filename}?</DialogTitle>
        <DialogDescription className="m-0 text-sm text-muted-foreground">
          The app sends its copy ({bytes(f.size)}) to {printer?.name ?? "the printer"}, which starts it straight away. Check the plate is clear
          {mat ? ` and ${mat} is loaded` : " and the right filament is loaded"}.
        </DialogDescription>
        <dl className="m-0 grid grid-cols-3 gap-x-4 gap-y-3 rounded-lg border px-4 py-3">
          <Fact label="Print time" value={m.estimated_seconds != null ? `about ${duration(m.estimated_seconds)}` : "Not known"} />
          <Fact label="Filament" value={m.filament_g != null ? `${m.filament_g} g` : "--"} />
          <Fact
            label="Material"
            value={
              <span className="inline-flex items-center gap-1.5">
                <Swatches f={f} />
                {mat ?? "--"}
              </span>
            }
          />
        </dl>
        {(error || blocker) && (
          <p role="alert" className="m-0 text-sm text-destructive">
            {error ?? blocker}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <DialogClose asChild>
            <Button variant="outline" disabled={busy}>
              Cancel
            </Button>
          </DialogClose>
          <Button variant="brand" onClick={start} disabled={busy || !!blocker}>
            <PrinterIcon /> {busy ? "Sending…" : "Start print"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function FilePanel({ id, labels, onClose, onChanged }: { id: number; labels: LabelCount[]; onClose: () => void; onChanged: () => void }) {
  const { data: f, error } = useFile(id);
  const { printers, config } = usePrinters();
  const [confirm, setConfirm] = React.useState(false);
  const [printOpen, setPrintOpen] = React.useState(false);
  const [sent, setSent] = React.useState(false);
  const [scheduleOpen, setScheduleOpen] = React.useState(false);
  const [scheduled, setScheduled] = React.useState(false);
  const [problem, setProblem] = React.useState<string | null>(null);
  const printerName = (pid: string | null) => printers.find((p) => p.id === pid)?.name ?? pid ?? "";
  const printer = f ? printTarget(f, printers) : undefined;
  const blocker = printBlocker(printer, !!config?.controls_enabled);

  const changeLabel = async (label: string | null) => {
    try {
      await setLabel(id, label);
      setProblem(null);
      onChanged(); // refetches this file's details too
    } catch (e) {
      setProblem(`Couldn't change the label: ${(e as Error).message}`);
    }
  };
  const remove = async () => {
    try {
      await deleteFile(id);
      setConfirm(false);
      onChanged();
      onClose();
    } catch (e) {
      setProblem(`Couldn't delete it: ${(e as Error).message}`);
      setConfirm(false);
    }
  };

  const m = f?.meta ?? {};
  return (
    <DialogPrimitive.Root open onOpenChange={(o) => !o && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-40 flex w-[min(100vw,560px)] flex-col overflow-y-auto border-l bg-background shadow-xl outline-none"
        >
          <div className="flex items-start justify-between gap-3 border-b px-6 py-4">
            <div className="min-w-0">
              <DialogPrimitive.Title className="m-0 truncate text-base font-semibold">{f?.filename ?? "File"}</DialogPrimitive.Title>
              {f && (
                <p className="m-0 mt-0.5 text-[13px] text-muted-foreground">
                  {f.source === "print" ? "Kept when it was printed" : "Sent to the printer"} {dayAndClock(f.first_seen)}
                  {f.printer_id ? ` · ${printerName(f.printer_id)}` : ""}
                </p>
              )}
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close">
                <X />
              </Button>
            </DialogPrimitive.Close>
          </div>

          {error && <p className="m-6 text-sm text-destructive">Couldn't load this file ({error}).</p>}
          {f && (
            <div className="flex flex-col gap-6 px-6 py-5">
              <GcodeFileView url={`/api/files/${f.id}/raw`} className="h-[300px] rounded-lg border bg-muted/30" />

              <dl className="m-0 grid grid-cols-3 gap-x-4 gap-y-3.5">
                <Fact label="Print time" value={m.estimated_seconds != null ? duration(m.estimated_seconds) : "--"} />
                <Fact label="Filament" value={m.filament_g != null ? `${m.filament_g} g` : m.filament_mm != null ? `${(m.filament_mm / 1000).toFixed(1)} m` : "--"} />
                <Fact
                  label="Material"
                  value={
                    <span className="inline-flex items-center gap-1.5">
                      <Swatches f={f} />
                      {material(m) ?? "--"}
                    </span>
                  }
                />
                <Fact label="Layers" value={m.layers ?? "--"} />
                <Fact label="Layer height" value={m.layer_height != null ? `${m.layer_height} mm` : "--"} />
                <Fact label="Size" value={bytes(f.size)} />
                {m.slicer && (
                  <div className="col-span-3">
                    <Fact label="Sliced with" value={<span className="break-words">{m.slicer}</span>} />
                  </div>
                )}
              </dl>

              <section className="flex flex-col gap-2.5">
                <h3 className="m-0 text-sm font-semibold">Label</h3>
                <LabelPicker f={f} labels={labels} onChange={changeLabel} />
              </section>

              <section className="flex flex-col gap-2">
                <h3 className="m-0 text-sm font-semibold">{printedLine(f, shortDay)}</h3>
                {f.jobs && f.jobs.length > 0 && (
                  <ul className="m-0 flex list-none flex-col divide-y rounded-lg border p-0">
                    {f.jobs.slice(0, 8).map((j) => (
                      <li key={j.id} className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                        <span>{dayAndClock(j.started_at)}</span>
                        <span className="text-muted-foreground">
                          {printerName(j.printer_id)} · {RESULT_LABEL[j.result]}
                          {j.print_duration ? ` · ${duration(j.print_duration)}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {problem && <p className="m-0 text-sm text-destructive">{problem}</p>}
              {sent && (
                <p role="status" className="m-0 flex items-center gap-2 rounded-lg border px-3.5 py-2.5 text-sm">
                  <CheckCircle2 className="size-4 shrink-0 text-brand" aria-hidden="true" />
                  <span>
                    Sent to {printer?.name ?? "the printer"}. It's starting now.{" "}
                    {printer && (
                      <Link to={`/printers/${encodeURIComponent(printer.id)}`} className="underline underline-offset-2">
                        Watch it
                      </Link>
                    )}
                  </span>
                </p>
              )}

              {scheduled && (
                <p role="status" className="m-0 flex items-center gap-2 rounded-lg border px-3.5 py-2.5 text-sm">
                  <CalendarClock className="size-4 shrink-0 text-brand" aria-hidden="true" />
                  <span>
                    Added to the queue.{" "}
                    <Link to="/queue" className="underline underline-offset-2">
                      Open the queue
                    </Link>
                  </span>
                </p>
              )}

              <div className="flex flex-col gap-2 border-t pt-5">
                <div className="flex flex-wrap justify-between gap-2">
                  <div className="flex flex-wrap gap-2">
                    <Button variant="brand" disabled={!!blocker} onClick={() => setPrintOpen(true)} title={blocker ?? undefined}>
                      <PrinterIcon /> Print
                    </Button>
                    <Button
                      variant="outline"
                      disabled={!config?.capture_files || !printer}
                      onClick={() => setScheduleOpen(true)}
                      title="Print it later: at a time, when the printer is free, or after another queued print"
                    >
                      <CalendarClock /> Schedule…
                    </Button>
                    <Button asChild variant="outline">
                      <a href={`/api/files/${f.id}/raw`} download={f.filename}>
                        <Download /> Download
                      </a>
                    </Button>
                  </div>
                  <Button variant="ghost" className="text-destructive hover:bg-destructive/10" onClick={() => setConfirm(true)}>
                    <Trash2 /> Delete
                  </Button>
                </div>
                {blocker && !sent && <p className="m-0 text-[13px] text-muted-foreground">{blocker}</p>}
              </div>
              <ScheduleDialog
                open={scheduleOpen}
                onOpenChange={setScheduleOpen}
                file={f}
                printer={printer}
                onDone={() => setScheduled(true)}
              />
              <PrintConfirm
                f={f}
                printer={printer}
                open={printOpen}
                onOpenChange={setPrintOpen}
                onSent={() => {
                  setSent(true);
                  onChanged();
                }}
              />
            </div>
          )}

          <Dialog open={confirm} onOpenChange={setConfirm}>
            <DialogContent>
              <DialogTitle className="m-0 text-base font-semibold">Delete {f?.filename}?</DialogTitle>
              <DialogDescription className="m-0 text-sm text-muted-foreground">
                The app's copy goes for good. The file on the printer isn't touched, and the print history keeps the name.
              </DialogDescription>
              <div className="flex justify-end gap-2">
                <DialogClose asChild>
                  <Button variant="outline">Keep it</Button>
                </DialogClose>
                <Button variant="destructive" onClick={remove}>
                  Delete
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function FilesPage() {
  const { config } = usePrinters();
  const [filter, setFilter] = useFilter();
  const [params, setParams] = useSearchParams();
  const openId = params.get("file") ? Number(params.get("file")) : null;
  const [query, setQuery] = React.useState("");
  const files = useFiles(filter);
  const labels = useLabels();
  const all = useFiles({ kind: "all" });

  const open = (id: number | null) => {
    const next = new URLSearchParams(params);
    if (id == null) next.delete("file");
    else next.set("file", String(id));
    setParams(next);
  };
  // Also marks History stale, so a deleted or relabelled file shows up right there too.
  const changed = useInvalidateLibrary();

  const q = query.trim().toLowerCase();
  const shown = (files.data ?? []).filter((f) => !q || f.filename.toLowerCase().includes(q) || (f.label ?? "").toLowerCase().includes(q));
  const title = filter.kind === "inbox" ? "Inbox" : filter.kind === "all" ? "All files" : filter.name;
  const off = config && config.capture_files === false;

  return (
    <div className="min-h-dvh bg-background">
      <AppHeader />
      <main className="mx-auto grid w-full max-w-[1440px] grid-cols-[minmax(0,1fr)] gap-6 p-4 md:grid-cols-[220px_minmax(0,1fr)] md:p-6">
        <aside className="min-w-0 md:sticky md:top-6 md:self-start">
          <Sidebar labels={labels.data ?? []} filter={filter} setFilter={setFilter} total={all.data?.length ?? 0} />
        </aside>
        <section className="flex min-w-0 flex-col gap-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="m-0 text-xl font-semibold">{title}</h1>
              <p className="m-0 mt-0.5 text-sm text-muted-foreground">
                {filter.kind === "inbox"
                  ? "New files sent to the printer land here. Give them a label to file them away."
                  : `${shown.length} ${shown.length === 1 ? "file" : "files"}`}
              </p>
            </div>
            <label className="relative w-full sm:w-64">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search files"
                aria-label="Search files"
                className="h-9 w-full rounded-md border bg-background pr-3 pl-9 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
          </div>

          {off ? (
            <p className="text-sm text-muted-foreground">File capture is turned off on this server (CAPTURE_FILES).</p>
          ) : files.error ? (
            <p className="text-sm text-destructive">Couldn't load the files ({files.error}).</p>
          ) : !files.data ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : shown.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-16 text-center">
              <Inbox className="size-8 text-muted-foreground/60" aria-hidden="true" />
              <p className="m-0 text-sm font-medium">{q ? "Nothing matches." : filter.kind === "inbox" ? "The Inbox is empty." : "No files here yet."}</p>
              {!q && (
                <p className="m-0 max-w-sm text-sm text-muted-foreground">
                  Send a file to the printer from your slicer and it shows up here a few seconds after it finishes arriving.
                </p>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(210px,1fr))] sm:gap-4">
              {shown.map((f) => (
                <FileCard key={f.id} f={f} showLabel={filter.kind === "all"} onOpen={() => open(f.id)} />
              ))}
            </div>
          )}
          <p className="m-0 text-[13px] text-muted-foreground">
            Every file is kept until you delete it here. <Link to="/history" className="text-foreground underline underline-offset-2">Print history</Link>
          </p>
        </section>
      </main>
      {openId != null && !Number.isNaN(openId) && (
        <FilePanel key={openId} id={openId} labels={labels.data ?? []} onClose={() => open(null)} onChanged={changed} />
      )}
    </div>
  );
}
