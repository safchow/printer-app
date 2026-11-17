import { Link } from "react-router-dom";
import { AppHeader } from "@/components/AppHeader";
import { CameraStage } from "@/components/CameraStage";
import { StateBadge } from "@/components/StateBadge";
import { ThemePicker } from "@/components/ThemePicker";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { usePrinters } from "@/hooks/usePrinters";
import { clock, dayAndClock, isActive, summary, temp } from "@/lib/format";
import type { Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

function offlineLine(p: Printer) {
  const seen = p.last_seen ? `Last seen ${dayAndClock(p.last_seen)}` : "Not reached yet";
  return `${seen} · checking every ${Math.round(p.reconnect_seconds)}s`;
}

function subline(p: Printer) {
  if (isActive(p)) {
    const layer = p.total_layers ? ` · layer ${p.layer ?? 0}/${p.total_layers}` : "";
    return `${p.filename ?? "Unknown file"}${layer}`;
  }
  if (p.state === "complete" && p.filename) return `Last: ${p.filename} · finished`;
  if (p.state === "cancelled" && p.filename) return `Last: ${p.filename} · cancelled`;
  if (p.state === "error") return p.message ?? "Printer reported an error";
  if (p.state === "offline" || p.state === "connecting") return offlineLine(p);
  return "Ready";
}

function PrinterCard({ printer: p }: { printer: Printer }) {
  const offline = p.state === "offline" || p.state === "connecting";
  const active = isActive(p);
  return (
    <Link
      to={`/printers/${p.id}`}
      className={cn(
        "flex flex-col overflow-hidden rounded-xl border text-inherit no-underline transition-colors hover:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        offline && "border-dashed",
      )}
    >
      <CameraStage printer={p} size="card" className="aspect-video">
        <StateBadge state={p.state} className="absolute top-3 left-3" />
      </CameraStage>
      <div className="flex flex-col gap-3 px-5 pt-4 pb-5">
        <div className="flex items-baseline justify-between gap-3">
          <div className="min-w-0">
            <div className="text-base font-semibold">{p.name}</div>
            <div className="mt-0.5 truncate text-[13px] text-muted-foreground">{subline(p)}</div>
          </div>
          {active && <div className="font-mono text-2xl font-semibold">{Math.round((p.progress ?? 0) * 100)}%</div>}
        </div>
        {active && <Progress value={(p.progress ?? 0) * 100} barClassName={p.state === "paused" ? "bg-amber-500" : undefined} />}
        {!offline && (
          <div className="font-mono text-[13px] text-muted-foreground">
            Nozzle {temp(p.nozzle.current)} · Bed {temp(p.bed.current)}
            {active && p.speed_percent != null && ` · Speed ${p.speed_percent}%`}
          </div>
        )}
      </div>
    </Link>
  );
}

function MobileRow({ printer: p }: { printer: Printer }) {
  const offline = p.state === "offline" || p.state === "connecting";
  return (
    <Link
      to={`/printers/${p.id}`}
      className={cn("flex min-h-[68px] items-center gap-3 rounded-xl border px-3.5 py-3 text-inherit no-underline", offline && "border-dashed")}
    >
      <span className={cn("size-12 shrink-0 rounded-lg", offline ? "bg-muted" : "bg-stage")} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-semibold">{p.name}</span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
          {offline ? (p.last_seen ? `Last seen ${dayAndClock(p.last_seen)}` : "Not reached yet") : `${subline(p)} · ${temp(p.nozzle.current)}`}
        </span>
      </span>
      <StateBadge state={p.state} />
    </Link>
  );
}

function MobileBigCard({ printer: p }: { printer: Printer }) {
  return (
    <Link to={`/printers/${p.id}`} className="block overflow-hidden rounded-[14px] border text-inherit no-underline">
      <CameraStage printer={p} size="card" className="h-[210px]">
        <StateBadge state={p.state} className="absolute top-2.5 left-2.5" />
      </CameraStage>
      <div className="flex flex-col gap-2.5 px-4 pt-3.5 pb-4">
        <div className="flex items-baseline justify-between gap-3">
          <div className="min-w-0">
            <div className="text-base font-semibold">{p.name}</div>
            <div className="truncate text-xs text-muted-foreground">
              {p.filename}
              {p.total_layers ? ` · L ${p.layer ?? 0}/${p.total_layers}` : ""}
            </div>
          </div>
          <span className="font-mono text-[28px] font-bold">{Math.round((p.progress ?? 0) * 100)}%</span>
        </div>
        <Progress value={(p.progress ?? 0) * 100} barClassName={p.state === "paused" ? "bg-amber-500" : undefined} />
        <div className="font-mono text-[13px] text-muted-foreground">
          {temp(p.nozzle.current)} · {temp(p.bed.current)} · {p.speed_percent ?? "--"}%
          {p.state === "printing" && ` · done ${clock(p.eta_at)}`}
        </div>
      </div>
    </Link>
  );
}

function Empty({ loaded }: { loaded: boolean }) {
  return (
    <Card className="p-8 text-center text-sm text-muted-foreground">
      {loaded ? (
        <>
          No printers yet. Add them to <code className="font-mono">printers.yaml</code> and restart the app.
        </>
      ) : (
        "Connecting to the server…"
      )}
    </Card>
  );
}

export function Dashboard() {
  const { printers, loaded } = usePrinters();
  const mobile = useIsMobile();
  const line = printers.length ? `${printers.length} printer${printers.length === 1 ? "" : "s"} · ${summary(printers)}` : "";

  if (mobile) {
    return (
      <div className="flex min-h-dvh flex-col">
        <header className="flex items-center justify-between px-4 pt-5.5 pb-3.5">
          <div>
            <h1 className="m-0 text-[26px] font-bold tracking-tight">Printers</h1>
            <div className="mt-0.5 text-[13px] text-muted-foreground">{summary(printers) || " "}</div>
          </div>
          <ThemePicker />
        </header>
        <main className="flex flex-col gap-3 px-4 pb-6">
          {printers.length === 0 && <Empty loaded={loaded} />}
          {printers.map((p) => (isActive(p) ? <MobileBigCard key={p.id} printer={p} /> : <MobileRow key={p.id} printer={p} />))}
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-dvh">
      <AppHeader />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 p-8">
        <div>
          <h1 className="m-0 text-[28px] font-bold tracking-tight">Dashboard</h1>
          <p className="mt-1.5 mb-0 text-sm text-muted-foreground">{line || " "}</p>
        </div>
        {printers.length === 0 ? (
          <Empty loaded={loaded} />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(360px,1fr))] gap-4">
            {printers.map((p) => (
              <PrinterCard key={p.id} printer={p} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
