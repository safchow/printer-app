import { Link, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { CameraStage } from "@/components/CameraStage";
import { LiveCamera } from "@/components/LiveCamera";
import { CONTROLS_LATER, ControlStub } from "@/components/ControlStub";
import { StateBadge } from "@/components/StateBadge";
import { ThemePicker } from "@/components/ThemePicker";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { usePrinters } from "@/hooks/usePrinters";
import { dayAndClock, duration, isActive, SPEED_PRESETS, speedName, temp } from "@/lib/format";
import type { Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

const SPEED_MIN = 50;
const SPEED_MAX = 180;

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

function ReadOnlyBar({ label, value, suffix = "%", max = 100 }: { label: string; value: number | null; suffix?: string; max?: number }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span className="font-mono text-muted-foreground">{value == null ? "--" : `${Math.round(value)}${suffix}`}</span>
      </div>
      <Progress value={value == null ? 0 : (value / max) * 100} className="h-1.5" />
    </div>
  );
}

function SpeedScale({ value }: { value: number | null }) {
  const left = (v: number) => `${(((v - SPEED_MIN) / (SPEED_MAX - SPEED_MIN)) * 100).toFixed(1)}%`;
  return (
    <div className="flex flex-col gap-2">
      <div className="relative h-1.5 rounded-full bg-muted">
        {value != null && (
          <div className="absolute inset-y-0 left-0 rounded-full bg-brand" style={{ width: left(Math.min(Math.max(value, SPEED_MIN), SPEED_MAX)) }} />
        )}
      </div>
      <div className="relative h-8">
        {SPEED_PRESETS.map(([name, v], i) => {
          const hit = v === value;
          return (
            <div
              key={name}
              className={cn("absolute top-0 flex flex-col gap-1", i === 0 ? "items-start" : "-translate-x-1/2 items-center")}
              style={{ left: left(v) }}
            >
              <span className={cn("h-2 w-0.5 rounded-sm", hit ? "bg-brand" : "bg-muted-foreground")} />
              <span className={cn("whitespace-nowrap text-[11px]", hit ? "font-semibold text-foreground" : "text-muted-foreground")}>
                {name}
                <span className="hidden lg:inline"> {v}%</span>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SpeedCard({ p, bare }: { p: Printer; bare?: boolean }) {
  const body = (
    <>
      <div className="flex items-baseline justify-between">
        <CardTitle>Print speed</CardTitle>
        <span className="text-sm text-muted-foreground">
          <span className="font-mono text-xl font-semibold text-foreground">{p.speed_percent ?? "--"}%</span> {speedName(p.speed_percent)}
        </span>
      </div>
      <SpeedScale value={p.speed_percent} />
    </>
  );
  return bare ? <div className="flex flex-col gap-3">{body}</div> : <Card className="flex flex-col gap-3 p-5">{body}</Card>;
}

function FansCard({ p, bare }: { p: Printer; bare?: boolean }) {
  const body = (
    <>
      <CardTitle>Fans</CardTitle>
      {p.fans.length === 0 && <p className="m-0 text-sm text-muted-foreground">No fan data from this printer.</p>}
      {p.fans.map((f) => (
        <ReadOnlyBar key={f.name} label={f.name} value={f.percent} />
      ))}
    </>
  );
  return bare ? <div className="flex flex-col gap-3.5">{body}</div> : <Card className="flex flex-col gap-3.5 p-5">{body}</Card>;
}

function TempRow({ name, t }: { name: string; t: Printer["nozzle"] }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex-1">
        <div className="text-sm">{name}</div>
        <div className="font-mono text-xs text-muted-foreground">now {t.current == null ? "--" : `${t.current.toFixed(1)}°`}</div>
      </div>
      <span className="text-[13px] text-muted-foreground">Target</span>
      <span className="inline-flex h-9 w-16 items-center rounded-md border px-2 font-mono text-sm">{temp(t.target)}</span>
      <ControlStub>
        <Button variant="secondary" size="sm" disabled>
          Set
        </Button>
      </ControlStub>
    </div>
  );
}

function TempCard({ p, bare }: { p: Printer; bare?: boolean }) {
  const body = (
    <>
      <CardTitle>Temperature</CardTitle>
      <TempRow name="Nozzle" t={p.nozzle} />
      <TempRow name="Bed" t={p.bed} />
    </>
  );
  return bare ? <div className="flex flex-col gap-3">{body}</div> : <Card className="flex flex-col gap-3 p-5">{body}</Card>;
}

function LightCard({ p, bare }: { p: Printer; bare?: boolean }) {
  const body = (
    <>
      <span className="text-sm font-medium">Chamber light</span>
      <ControlStub>
        <Switch checked={!!p.light_on} disabled aria-label="Chamber light" />
      </ControlStub>
    </>
  );
  return bare ? (
    <div className="flex items-center justify-between">{body}</div>
  ) : (
    <Card className="flex items-center justify-between px-5 py-4">{body}</Card>
  );
}

function ActionButtons({ p, mobile }: { p: Printer; mobile?: boolean }) {
  if (!isActive(p)) return null;
  const cls = mobile ? "h-[52px] w-full rounded-[10px] text-base" : undefined;
  const wrap = mobile ? "w-full" : undefined;
  return (
    <>
      <ControlStub className={wrap}>
        {p.state === "paused" ? (
          <Button variant="brand" disabled className={cls}>
            Resume
          </Button>
        ) : (
          <Button variant="outline" disabled className={cls}>
            Pause
          </Button>
        )}
      </ControlStub>
      <ControlStub className={wrap}>
        <Button variant="destructive" disabled className={cls}>
          Stop
        </Button>
      </ControlStub>
    </>
  );
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
    </>
  );
}

function Desktop({ p }: { p: Printer }) {
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
          <ActionButtons p={p} />
          <ThemePicker />
        </div>
      </header>
      <main className="mx-auto flex max-w-[1360px] flex-wrap items-start gap-5 px-8 pt-6 pb-12">
        <section className="flex min-w-0 flex-[999_1_620px] flex-col gap-5">
          <StatusNote p={p} />
          <CameraStage printer={p} className="aspect-video rounded-xl border">
            <LiveCamera printer={p} />
          </CameraStage>
          <Card className="flex flex-col gap-4.5 p-6">
            <ProgressBody p={p} />
          </Card>
        </section>
        <aside className="flex min-w-0 flex-[1_1_380px] flex-col gap-4">
          <SpeedCard p={p} />
          <FansCard p={p} />
          <TempCard p={p} />
          <LightCard p={p} />
          <p className="m-0 text-xs text-muted-foreground">{CONTROLS_LATER}</p>
        </aside>
      </main>
    </div>
  );
}

function Mobile({ p }: { p: Printer }) {
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
          <SpeedCard p={p} bare />
          <div className="border-t pt-3.5">
            <FansCard p={p} bare />
          </div>
          <div className="border-t pt-3.5">
            <TempCard p={p} bare />
          </div>
          <div className="border-t pt-3.5">
            <LightCard p={p} bare />
          </div>
          <p className="m-0 text-xs text-muted-foreground">{CONTROLS_LATER}</p>
        </TabsContent>
      </Tabs>
      {isActive(p) && (
        <div className="sticky bottom-0 grid grid-cols-2 gap-2.5 border-t bg-background px-4 pt-3 pb-[max(1.75rem,env(safe-area-inset-bottom))]">
          <ActionButtons p={p} mobile />
        </div>
      )}
    </div>
  );
}

export function PrinterPage() {
  const { id } = useParams();
  const { printers, loaded } = usePrinters();
  const mobile = useIsMobile();
  const p = printers.find((x) => x.id === id);

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
  return mobile ? <Mobile p={p} /> : <Desktop p={p} />;
}
