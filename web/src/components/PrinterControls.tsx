import * as React from "react";
import { ControlStub } from "@/components/ControlStub";
import { Pause, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ACTIVITY_LIMIT, type ActionRow } from "@/hooks/useActions";
import type { useControl } from "@/hooks/useControl";
import { clock, isActive } from "@/lib/format";
import type { Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

export type Ctl = ReturnType<typeof useControl> & { enabled: boolean };

const offline = (p: Printer) => p.state === "offline" || p.state === "connecting";

/** Wraps a control so that, when controls are off on the server, it explains why. */
function Gate({ ctl, children, className }: { ctl: Ctl; children: React.ReactNode; className?: string }) {
  return ctl.enabled ? <>{children}</> : <ControlStub className={className}>{children}</ControlStub>;
}

/**
 * A 0-100 style slider that only sends when you let go. Shows the printer's
 * value unless you're dragging.
 */
function Slider({
  label,
  value,
  min,
  max,
  step,
  disabled,
  onCommit,
}: {
  label: string;
  value: number | null;
  min: number;
  max: number;
  step: number;
  disabled?: boolean;
  onCommit: (v: number) => void;
}) {
  const [draft, setDraft] = React.useState<number | null>(null);
  const shown = draft ?? (value == null ? min : Math.round(value / step) * step);
  const commit = () => {
    if (draft != null && draft !== value) onCommit(draft);
    setDraft(null);
  };
  return (
    <input
      type="range"
      aria-label={label}
      min={min}
      max={max}
      step={step}
      value={shown}
      disabled={disabled}
      onChange={(e) => setDraft(Number(e.target.value))}
      onPointerUp={commit}
      onKeyUp={commit}
      onBlur={() => setDraft(null)}
      className="h-6 w-full cursor-pointer accent-[var(--brand)] disabled:cursor-not-allowed disabled:opacity-50"
    />
  );
}

function ErrorLine({ ctl }: { ctl: Ctl }) {
  return ctl.error ? <p className="m-0 text-[13px] text-destructive">{ctl.error}</p> : null;
}

function Shell({ bare, className, children }: { bare?: boolean; className?: string; children: React.ReactNode }) {
  return bare ? <div className={cn("flex flex-col", className)}>{children}</div> : <Card className={cn("flex flex-col p-5", className)}>{children}</Card>;
}

export function SpeedCard({ p, ctl, bare, className, scale }: { p: Printer; ctl: Ctl; bare?: boolean; className?: string; scale?: boolean }) {
  const can = ctl.enabled && isActive(p) && !ctl.busy;
  return (
    <Shell bare={bare} className={cn("gap-3", className)}>
      <div className="flex items-baseline justify-between">
        <CardTitle>Print speed</CardTitle>
        <span className="font-mono text-xl font-semibold">{p.speed_percent ?? "--"}%</span>
      </div>
      <div className="flex flex-col gap-1">
        <Gate ctl={ctl} className="w-full">
          <Slider
            label="Print speed"
            value={p.speed_percent}
            min={20}
            max={160}
            step={10}
            disabled={!can}
            onCommit={(percent) => ctl.run("speed", { percent })}
          />
        </Gate>
        {scale && (
          <div className="flex justify-between font-mono text-[11px] text-muted-foreground">
            <span>20%</span>
            <span>160%</span>
          </div>
        )}
      </div>
      {ctl.enabled && !isActive(p) && <p className="m-0 text-xs text-muted-foreground">Speed can be changed during a print.</p>}
    </Shell>
  );
}

export function FansCard({ p, ctl, bare, className }: { p: Printer; ctl: Ctl; bare?: boolean; className?: string }) {
  const can = ctl.enabled && !offline(p) && !ctl.busy;
  return (
    <Shell bare={bare} className={cn("gap-3.5", className)}>
      <CardTitle>Fans</CardTitle>
      {p.fans.length === 0 && <p className="m-0 text-sm text-muted-foreground">No fan data from this printer.</p>}
      {p.fans.map((f) => (
        <div key={f.name} className="flex flex-col gap-1">
          <div className="flex justify-between text-sm">
            <span>{f.name}</span>
            <span className="font-mono text-muted-foreground">{f.percent == null ? "--" : `${Math.round(f.percent)}%`}</span>
          </div>
          <Gate ctl={ctl} className="w-full">
            <Slider
              label={f.name}
              value={f.percent}
              min={0}
              max={100}
              step={10}
              disabled={!can}
              onCommit={(percent) => ctl.run("fan", { name: f.name, percent }, `fan:${f.name}`)}
            />
          </Gate>
        </div>
      ))}
    </Shell>
  );
}

function TempRow({
  name,
  heater,
  t,
  p,
  ctl,
  max,
  stacked,
}: {
  name: string;
  heater: "nozzle" | "bed";
  t: Printer["nozzle"];
  p: Printer;
  ctl: Ctl;
  max: number;
  stacked?: boolean;
}) {
  const [draft, setDraft] = React.useState("");
  const can = ctl.enabled && !offline(p) && !ctl.busy;
  const n = Number(draft);
  const valid = draft !== "" && Number.isInteger(n) && (n === 0 || (n >= 30 && n <= max));
  const send = async () => {
    if (!valid) return;
    if (await ctl.run("temp", { heater, target: n }, `temp:${heater}`)) setDraft("");
  };
  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void send();
  };
  const input = (
    <Gate ctl={ctl} className={stacked ? "min-w-0 flex-1" : undefined}>
      <input
        inputMode="numeric"
        aria-label={`${name} target, 0 or 30 to ${max} degrees`}
        placeholder={t.target == null ? "°" : `${Math.round(t.target)}°`}
        value={draft}
        disabled={!can}
        onChange={(e) => setDraft(e.target.value.replace(/[^0-9]/g, "").slice(0, 3))}
        className={cn(
          "h-9 rounded-md border bg-background px-2 font-mono text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
          stacked ? "w-full min-w-0 flex-1" : "w-16",
        )}
      />
    </Gate>
  );
  const button = (
    <Gate ctl={ctl}>
      <Button type="submit" variant="secondary" size="sm" className={stacked ? "h-9" : undefined} disabled={!can || !valid}>
        Set
      </Button>
    </Gate>
  );
  if (stacked) {
    return (
      <form className="flex flex-col gap-2" onSubmit={onSubmit}>
        <div className="flex items-baseline justify-between gap-2 text-sm">
          <span>{name}</span>
          <span className="font-mono text-xs text-muted-foreground">
            {t.current == null ? "--" : `${Math.round(t.current)}°`} → {t.target == null ? "--" : `${Math.round(t.target)}°`}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {input}
          {button}
        </div>
      </form>
    );
  }
  return (
    <form className="flex items-center gap-2.5" onSubmit={onSubmit}>
      <div className="flex-1">
        <div className="text-sm">{name}</div>
        <div className="font-mono text-xs text-muted-foreground">
          now {t.current == null ? "--" : `${t.current.toFixed(1)}°`} · target {t.target == null ? "--" : `${Math.round(t.target)}°`}
        </div>
      </div>
      {input}
      {button}
    </form>
  );
}

export function TempCard({ p, ctl, bare, className, stacked }: { p: Printer; ctl: Ctl; bare?: boolean; className?: string; stacked?: boolean }) {
  return (
    <Shell bare={bare} className={cn("gap-3", className)}>
      <CardTitle>Temperature</CardTitle>
      <TempRow name="Nozzle" heater="nozzle" t={p.nozzle} p={p} ctl={ctl} max={300} stacked={stacked} />
      <TempRow name="Bed" heater="bed" t={p.bed} p={p} ctl={ctl} max={100} stacked={stacked} />
      <p className="m-0 text-xs text-muted-foreground">0 turns a heater off. Nozzle up to 300°, bed up to 100°.</p>
    </Shell>
  );
}

export function LightCard({ p, ctl, bare }: { p: Printer; ctl: Ctl; bare?: boolean }) {
  const can = ctl.enabled && !offline(p) && !ctl.busy && p.light_on != null;
  const body = (
    <>
      <span className="text-sm font-medium">Chamber light</span>
      <Gate ctl={ctl}>
        <Switch checked={!!p.light_on} disabled={!can} onCheckedChange={(on) => ctl.run("light", { on })} aria-label="Chamber light" />
      </Gate>
    </>
  );
  return bare ? (
    <div className="flex items-center justify-between">{body}</div>
  ) : (
    <Card className="flex items-center justify-between px-5 py-4">{body}</Card>
  );
}

/** Pause / Resume / Stop. Pause and Stop ask first. */
export function ActionButtons({ p, ctl, mobile }: { p: Printer; ctl: Ctl; mobile?: boolean }) {
  const [confirm, setConfirm] = React.useState<"pause" | "cancel" | null>(null);
  if (!isActive(p)) return null;
  const cls = mobile ? "h-[52px] w-full rounded-[10px] text-base" : undefined;
  const wrap = mobile ? "w-full" : undefined;
  const pct = Math.round((p.progress ?? 0) * 100);
  const busy = !!ctl.busy;
  // Desktop: icon buttons with a tooltip. Phone: full-width buttons with icon and word.
  const action = (label: string, icon: React.ReactNode, variant: "outline" | "brand" | "destructive", onClick: () => void) => {
    const button = mobile ? (
      <Button variant={variant} disabled={!ctl.enabled || busy} className={cls} onClick={onClick}>
        {icon}
        {label}
      </Button>
    ) : (
      <Button variant={variant} size="icon" aria-label={label} disabled={!ctl.enabled || busy} onClick={onClick}>
        {icon}
      </Button>
    );
    if (mobile || !ctl.enabled) return button;
    return (
      <Tooltip>
        <TooltipTrigger asChild>{button}</TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
    );
  };
  return (
    <>
      <Gate ctl={ctl} className={wrap}>
        {p.state === "paused"
          ? action(ctl.busy === "resume" ? "Resuming…" : "Resume", <Play />, "brand", () => ctl.run("resume"))
          : action(ctl.busy === "pause" ? "Pausing…" : "Pause", <Pause />, "outline", () => setConfirm("pause"))}
      </Gate>
      <Gate ctl={ctl} className={wrap}>
        {action(ctl.busy === "cancel" ? "Stopping…" : "Stop", <Square />, "destructive", () => setConfirm("cancel"))}
      </Gate>
      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          <DialogTitle className="m-0 text-lg font-semibold">{confirm === "cancel" ? "Stop this print?" : "Pause this print?"}</DialogTitle>
          <DialogDescription className="m-0 text-sm text-muted-foreground">
            {confirm === "cancel"
              ? `${p.filename ?? "The print"} is ${pct}% done. Stopping it can't be undone.`
              : `${p.filename ?? "The print"} will pause at ${pct}%. You can resume it from here.`}
          </DialogDescription>
          <div className="flex justify-end gap-2">
            <DialogClose asChild>
              <Button variant="outline">Keep printing</Button>
            </DialogClose>
            <Button
              variant={confirm === "cancel" ? "destructive" : "brand"}
              onClick={() => {
                const action = confirm!;
                setConfirm(null);
                void ctl.run(action);
              }}
            >
              {confirm === "cancel" ? "Stop print" : "Pause"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

const ACTION_NAMES: Record<string, string> = {
  pause: "Pause",
  resume: "Resume",
  cancel: "Stop",
  speed: "Speed",
  fan: "Fan",
  temp: "Temperature",
  light: "Light",
};

function describe(r: ActionRow): string {
  const v = r.values as Record<string, string | number | boolean>;
  switch (r.action) {
    case "speed":
      return `Speed ${v.percent}%`;
    case "fan":
      return `${v.name} ${v.percent}%`;
    case "temp":
      return `${v.heater === "bed" ? "Bed" : "Nozzle"} ${v.target === 0 ? "off" : `${v.target}°`}`;
    case "light":
      return `Light ${v.on ? "on" : "off"}`;
    default:
      return ACTION_NAMES[r.action] ?? r.action;
  }
}

export function ActivityCard({ rows, bare, empty }: { rows: ActionRow[]; bare?: boolean; empty?: string }) {
  if (rows.length === 0 && !empty) return null;
  return (
    <Shell bare={bare} className="gap-2.5">
      <CardTitle>Activity</CardTitle>
      {rows.length === 0 && <p className="m-0 text-[13px] text-muted-foreground">{empty}</p>}
      {rows.length > 0 && (
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {rows.slice(0, ACTIVITY_LIMIT).map((r) => (
          <li key={r.id} className="flex items-baseline justify-between gap-3 text-[13px]">
            <span className="min-w-0 truncate">
              {describe(r)}
              {r.result !== "sent" && (
                <span className="text-destructive" title={r.detail ?? undefined}>
                  {" "}
                  · {r.result}
                </span>
              )}
            </span>
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              {clock(r.at)}
              {r.device ? ` · ${r.device}` : ""}
            </span>
          </li>
        ))}
      </ul>
      )}
    </Shell>
  );
}

export { ErrorLine };
