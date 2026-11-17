import { Link, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { CameraStage } from "@/components/CameraStage";
import { LiveCamera } from "@/components/LiveCamera";
import { CONTROLS_OFF } from "@/components/ControlStub";
import { ActionButtons, ActivityCard, type Ctl, ErrorLine, FansCard, LightCard, SpeedCard, TempCard } from "@/components/PrinterControls";
import { StateBadge } from "@/components/StateBadge";
import { ThemePicker } from "@/components/ThemePicker";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { useActions, type ActionRow } from "@/hooks/useActions";
import { useControl } from "@/hooks/useControl";
import { usePrinters } from "@/hooks/usePrinters";
import { clock, dayAndClock, duration, isActive, temp } from "@/lib/format";
import type { Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

function bars(p: Printer) {
  const file = p.progress != null ? p.progress * 100 : null;
  const layer = p.layer != null && p.total_layers ? (p.layer / p.total_layers) * 100 : null;
  return [
    { k: "File", v: file },
    { k: p.total_layers ? `Layer ${p.layer ?? 0} / ${p.total_layers}` : "Layer", v: layer },
  ];
}

function remaining(p: Printer): number | null {
  if (!p.eta_at) return null;
  return (new Date(p.eta_at).getTime() - Date.now()) / 1000;
}

function StatusNote({ p }: { p: Printer }) {
  if (p.state === "offline" || p.state === "connecting") {
    return (
      <div className="rounded-lg border border-dashed px-4 py-3 text-sm text-muted-foreground">
        {p.last_seen ? `Last seen ${dayAndClock(p.last_seen)}` : "Not reached yet"}. Checking again every {Math.round(p.reconnect_seconds)} seconds.
      </div>
    );
  }
  if (p.state === "error") {
    return <div className="rounded-lg bg-offline px-4 py-3 text-sm text-offline-foreground">{p.message ?? "The printer reported an error."}</div>;
  }
  return null;
}

/** Started and done-at times, under the progress bars. */
function Times({ p }: { p: Printer }) {
  const paused = p.state === "paused";
  const done = p.state === "complete";
  return (
    <div className="flex items-end justify-between leading-tight">
      <div className="flex flex-col">
        <span className="text-[11px] text-muted-foreground">Started</span>
        <span className="font-mono text-[15px] font-semibold">{clock(p.started_at)}</span>
      </div>
      <div className="flex flex-col text-right">
        <span className="text-[11px] text-muted-foreground">{paused ? "Paused" : done ? "Finished" : "Done at"}</span>
        <span className="font-mono text-[15px] font-semibold">{paused ? "On hold" : clock(p.eta_at)}</span>
      </div>
    </div>
  );
}

function ProgressBody({ p, big = "text-4xl" }: { p: Printer; big?: string }) {
  const active = isActive(p) || p.state === "complete";
  if (!active) {
    return <p className="m-0 text-sm text-muted-foreground">{p.state === "idle" ? "Not printing." : p.filename ? `Last: ${p.filename}` : "No job."}</p>;
  }
  return (
    <>
      <div className="flex items-end justify-between">
        <CardTitle>Progress</CardTitle>
        <div className={cn("font-mono leading-none font-bold", big)}>{Math.round((p.progress ?? 0) * 100)}%</div>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {bars(p).map((b) => (
          <div key={b.k} className="flex flex-col gap-2">
            <div className="flex justify-between text-[13px]">
              <span className="text-muted-foreground">{b.k}</span>
              <span className="font-mono">{b.v == null ? "--" : `${b.v.toFixed(1)}%`}</span>
            </div>
            <Progress value={b.v} className="h-1.5" />
          </div>
        ))}
        <div className="flex flex-col gap-2">
          <div className="flex justify-between text-[13px]">
            <span className="text-muted-foreground">Time</span>
            <span className="font-mono">
              {duration(p.print_duration)}
              {p.state === "printing" && remaining(p) != null && <span className="text-muted-foreground"> · {duration(remaining(p))} left</span>}
            </span>
          </div>
          <Progress value={(p.progress ?? 0) * 100} className="h-1.5" />
        </div>
      </div>
      <Times p={p} />
    </>
  );
}

function Desktop({ p, ctl, rows }: { p: Printer; ctl: Ctl; rows: ActionRow[] }) {
  return (
    <div className="min-h-dvh">
      <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b px-8 py-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <Button asChild variant="outline" size="icon" aria-label="Back to dashboard">
            <Link to="/">
              <ChevronLeft />
            </Link>
          </Button>
          <h1 className="m-0 text-xl font-semibold">{p.name}</h1>
          <StateBadge state={p.state} />
          {p.filename && <span className="truncate text-[13px] text-muted-foreground">{p.filename}</span>}
        </div>
        <div className="flex items-center gap-2">
          <ActionButtons p={p} ctl={ctl} />
          <ThemePicker />
        </div>
      </header>
      <main className="mx-auto flex max-w-[1360px] flex-wrap items-start gap-5 px-8 pt-6 pb-12">
        <section className="flex min-w-0 flex-[999_1_620px] flex-col gap-5">
          <StatusNote p={p} />
          <Card className="flex flex-col gap-4.5 p-6">
            <ProgressBody p={p} />
          </Card>
          <CameraStage printer={p} className="aspect-video rounded-xl border">
            <LiveCamera printer={p} />
          </CameraStage>
        </section>
        <aside className="flex min-w-0 flex-[1_1_380px] flex-col gap-4">
          <ErrorLine ctl={ctl} />
          <SpeedCard p={p} ctl={ctl} />
          <FansCard p={p} ctl={ctl} />
          <TempCard p={p} ctl={ctl} />
          <LightCard p={p} ctl={ctl} />
          {!ctl.enabled && <p className="m-0 text-xs text-muted-foreground">{CONTROLS_OFF}</p>}
          <ActivityCard rows={rows} />
        </aside>
      </main>
    </div>
  );
}

function Mobile({ p, ctl, rows }: { p: Printer; ctl: Ctl; rows: ActionRow[] }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center gap-2 px-3 pt-3 pb-2">
        <Button asChild variant="ghost" size="icon" className="size-11" aria-label="Back to dashboard">
          <Link to="/">
            <ChevronLeft className="size-[18px]" />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="m-0 text-lg font-semibold">{p.name}</h1>
          <div className="truncate text-xs text-muted-foreground">{p.filename ?? " "}</div>
        </div>
        <StateBadge state={p.state} />
        <ThemePicker />
      </header>
      <CameraStage printer={p} className="h-[220px]">
        <LiveCamera printer={p} />
      </CameraStage>
      <div className="px-4 pt-3.5">
        <StatusNote p={p} />
      </div>
      <Tabs defaultValue="status" className="flex flex-1 flex-col">
        <div className="px-4 pt-3.5">
          <TabsList aria-label="Printer sections">
            <TabsTrigger value="status">Status</TabsTrigger>
            <TabsTrigger value="controls">Controls</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="status" className="flex flex-col gap-4 p-4 outline-none">
          {isActive(p) || p.state === "complete" ? (
            <>
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-[44px] font-bold tracking-tighter">{Math.round((p.progress ?? 0) * 100)}%</span>
                {p.total_layers ? (
                  <span className="text-[13px] text-muted-foreground">
                    Layer {p.layer ?? 0} / {p.total_layers}
                  </span>
                ) : null}
              </div>
              <Card className="flex flex-col gap-3 p-3.5">
                {bars(p).map((b) => (
                  <div key={b.k} className="flex flex-col gap-1.5">
                    <div className="flex justify-between text-[13px]">
                      <span className="text-muted-foreground">{b.k.startsWith("Layer") ? "Layers" : b.k}</span>
                      <span className="font-mono">{b.v == null ? "--" : `${b.v.toFixed(1)}%`}</span>
                    </div>
                    <Progress value={b.v} className="h-[5px]" />
                  </div>
                ))}
                <div className="flex justify-between text-[13px]">
                  <span className="text-muted-foreground">Time</span>
                  <span className="font-mono">
                    {duration(p.print_duration)}
                    {p.state === "printing" && remaining(p) != null && ` · ${duration(remaining(p))} left`}
                  </span>
                </div>
                <Times p={p} />
              </Card>
            </>
          ) : (
            <p className="m-0 text-sm text-muted-foreground">{p.state === "idle" ? "Not printing." : "No job."}</p>
          )}
          <div className="grid grid-cols-3 gap-2">
            {[
              ["Nozzle", temp(p.nozzle.current)],
              ["Bed", temp(p.bed.current)],
              ["Speed", p.speed_percent == null ? "--" : `${p.speed_percent}%`],
            ].map(([k, v]) => (
              <div key={k} className="rounded-[10px] border p-2.5">
                <div className="text-[11px] text-muted-foreground">{k}</div>
                <div className="mt-0.5 font-mono text-[15px]">{v}</div>
              </div>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="controls" className="flex flex-col gap-4 p-4 outline-none">
          <ErrorLine ctl={ctl} />
          <SpeedCard p={p} ctl={ctl} bare />
          <div className="border-t pt-3.5">
            <FansCard p={p} ctl={ctl} bare />
          </div>
          <div className="border-t pt-3.5">
            <TempCard p={p} ctl={ctl} bare />
          </div>
          <div className="border-t pt-3.5">
            <LightCard p={p} ctl={ctl} bare />
          </div>
          {!ctl.enabled && <p className="m-0 text-xs text-muted-foreground">{CONTROLS_OFF}</p>}
          {rows.length > 0 && (
            <div className="border-t pt-3.5">
              <ActivityCard rows={rows} bare />
            </div>
          )}
        </TabsContent>
      </Tabs>
      {isActive(p) && (
        <div className="sticky bottom-0 grid grid-cols-2 gap-2.5 border-t bg-background px-4 pt-3 pb-[max(1.75rem,env(safe-area-inset-bottom))]">
          <ActionButtons p={p} ctl={ctl} mobile />
        </div>
      )}
    </div>
  );
}

export function PrinterPage() {
  const { id = "" } = useParams();
  const { printers, loaded, config } = usePrinters();
  const mobile = useIsMobile();
  const p = printers.find((x) => x.id === id);
  const enabled = !!config?.controls_enabled;
  const { rows, reload } = useActions(id, enabled);
  const control = useControl(id, reload);
  const ctl: Ctl = { ...control, enabled };

  if (!p) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4 p-8 text-center">
        <p className="m-0 text-muted-foreground">{loaded ? "No printer with that name." : "Connecting to the server…"}</p>
        <Button asChild variant="outline">
          <Link to="/">Back to dashboard</Link>
        </Button>
      </div>
    );
  }
  return mobile ? <Mobile p={p} ctl={ctl} rows={rows} /> : <Desktop p={p} ctl={ctl} rows={rows} />;
}
