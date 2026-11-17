import * as React from "react";
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

// three.js is big: load the 3D preview only when the printer page shows it.
const LazyPreview = React.lazy(() => import("@/components/GcodePreview").then((m) => ({ default: m.GcodePreview })));
function GcodePreview(props: { p: Printer; className?: string }) {
  return (
    <React.Suspense fallback={<div className={props.className} />}>
      <LazyPreview {...props} />
    </React.Suspense>
  );
}

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

function StatusNote({ p, className }: { p: Printer; className?: string }) {
  if (p.state === "offline" || p.state === "connecting") {
    return (
      <div className={cn("rounded-lg border border-dashed px-4 py-3 text-sm text-muted-foreground", className)}>
        {p.last_seen ? `Last seen ${dayAndClock(p.last_seen)}` : "Not reached yet"}. Checking again every {Math.round(p.reconnect_seconds)} seconds.
      </div>
    );
  }
  if (p.state === "error") {
    return <div className={cn("rounded-lg bg-offline px-4 py-3 text-sm text-offline-foreground", className)}>{p.message ?? "The printer reported an error."}</div>;
  }
  return null;
}

/** Started and done-at times, under the progress bars. */
function Times({ p, size = "text-[15px]" }: { p: Printer; size?: string }) {
  const paused = p.state === "paused";
  const done = p.state === "complete";
  return (
    <div className="flex items-end justify-between leading-tight">
      <div className="flex flex-col">
        <span className="text-[11px] text-muted-foreground">Started</span>
        <span className={cn("font-mono font-semibold", size)}>{clock(p.started_at)}</span>
      </div>
      <div className="flex flex-col text-right">
        <span className="text-[11px] text-muted-foreground">{paused ? "Paused" : done ? "Finished" : "Done at"}</span>
        <span className={cn("font-mono font-semibold", size)}>{paused ? "On hold" : clock(p.eta_at)}</span>
      </div>
    </div>
  );
}

/** Desktop progress card: fills the column beside the camera, top to bottom. */
function ProgressColumn({ p }: { p: Printer }) {
  const active = isActive(p) || p.state === "complete";
  if (!active) {
    return (
      <>
        <CardTitle>Progress</CardTitle>
        <p className="m-0 text-sm text-muted-foreground">{p.state === "idle" ? "Not printing." : p.filename ? `Last: ${p.filename}` : "No job."}</p>
      </>
    );
  }
  const left = p.state === "printing" ? remaining(p) : null;
  return (
    <>
      <div className="flex flex-col gap-2">
        <CardTitle>Progress</CardTitle>
        <div className="font-mono text-[44px] leading-none font-bold tracking-tight">{Math.round((p.progress ?? 0) * 100)}%</div>
      </div>
      <div className="flex flex-col gap-4">
        {bars(p).map((b) => (
          <div key={b.k} className="flex flex-col gap-2">
            <div className="flex justify-between gap-2 text-[13px]">
              <span className="truncate text-muted-foreground">{b.k}</span>
              <span className="font-mono">{b.v == null ? "--" : `${b.v.toFixed(1)}%`}</span>
            </div>
            <Progress value={b.v} className="h-1.5" />
          </div>
        ))}
        <div className="flex flex-col gap-2">
          <div className="flex justify-between gap-2 text-[13px]">
            <span className="text-muted-foreground">Time</span>
            <span className="truncate font-mono">
              {duration(p.print_duration)}
              {left != null && <span className="text-muted-foreground"> · {duration(left)} left</span>}
            </span>
          </div>
          <Progress value={(p.progress ?? 0) * 100} className="h-1.5" />
        </div>
      </div>
      <Times p={p} size="text-base" />
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
      {/*
        Layout B, sized to the window so it fits a 14" laptop without scrolling:
        camera + progress (with the speed slider) on top; under them the 3D preview,
        fans, temperature, and light + activity. 24 px between everything.
      */}
      <main className="mx-auto flex h-[calc(100dvh-4rem)] min-h-[720px] w-full max-w-[1440px] flex-col gap-6 p-6">
        <StatusNote p={p} />
        <ErrorLine ctl={ctl} />
        <div className="grid min-h-0 flex-1 grid-cols-[repeat(4,minmax(0,1fr))] grid-rows-[minmax(0,1.35fr)_minmax(0,1fr)] gap-6">
        <CameraStage printer={p} className="col-span-3 h-full min-h-0 rounded-xl border">
          <LiveCamera printer={p} />
        </CameraStage>
        <Card className="flex min-h-0 flex-col justify-between gap-4 overflow-hidden p-6">
          <ProgressColumn p={p} />
          <div className="border-t pt-4">
            <SpeedCard p={p} ctl={ctl} bare />
          </div>
        </Card>
        <Card className="flex min-h-0 flex-col gap-3 overflow-hidden p-0">
          <div className="flex items-baseline justify-between px-6 pt-5">
            <CardTitle>Model</CardTitle>
            <span className="text-[11px] text-muted-foreground">drag to turn</span>
          </div>
          <GcodePreview p={p} className="min-h-0 flex-1" />
        </Card>
        <FansCard p={p} ctl={ctl} className="min-h-0 gap-5 overflow-hidden p-6" />
        <TempCard p={p} ctl={ctl} className="min-h-0 gap-5 overflow-hidden p-6" stacked />
        <Card className="flex min-h-0 flex-col gap-4 overflow-hidden p-6">
          <LightCard p={p} ctl={ctl} bare />
          <div className="border-t" />
          <ActivityCard rows={rows} bare empty={ctl.enabled ? "No actions yet." : "Controls are off."} />
        </Card>
        </div>
        {!ctl.enabled && <p className="m-0 -mt-3 text-xs text-muted-foreground">{CONTROLS_OFF}</p>}
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
              <Card className="h-[240px] overflow-hidden p-0">
                <GcodePreview p={p} className="h-full" />
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
