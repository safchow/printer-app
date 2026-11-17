import { clock, isActive } from "@/lib/format";
import type { Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

/** The camera area with the start / finish timeline over it. Live video is laid over it as children. */
export function CameraStage({
  printer,
  size = "page",
  className,
  children,
}: {
  printer: Printer;
  size?: "card" | "page";
  className?: string;
  children?: React.ReactNode;
}) {
  const offline = printer.state === "offline" || printer.state === "connecting";
  const label = offline
    ? "No camera while offline"
    : printer.has_camera
      ? size === "card"
        ? "Open the printer to watch live"
        : "Camera"
      : "No camera";
  const active = isActive(printer);
  const paused = printer.state === "paused";
  const pct = Math.round((printer.progress ?? 0) * 100);

  return (
    <div
      className={cn(
        "relative flex items-center justify-center overflow-hidden bg-stage text-sm text-muted-foreground",
        offline && "bg-muted",
        className,
      )}
    >
      <span className="px-6 text-center">{label}</span>
      {children}
      {active && size === "card" && (
        <div className="absolute inset-x-2.5 bottom-2.5 flex items-center gap-2 rounded-lg border bg-background px-2.5 py-1.5 font-mono text-xs text-foreground">
          <span>{clock(printer.started_at)}</span>
          <span className="relative h-[3px] flex-1 rounded-full bg-border">
            <span className={cn("absolute inset-y-0 left-0 rounded-full", paused ? "bg-amber-500" : "bg-brand")} style={{ width: `${pct}%` }} />
          </span>
          <span>{paused ? "Paused" : clock(printer.eta_at)}</span>
        </div>
      )}
      {active && size === "page" && (
        <div className="absolute inset-x-2.5 bottom-2.5 flex items-center gap-3.5 rounded-[10px] border bg-background px-3.5 py-2.5 text-foreground md:inset-x-3 md:bottom-3">
          <div className="flex flex-col leading-tight">
            <span className="text-[11px] text-muted-foreground">Started</span>
            <span className="font-mono text-[15px] font-semibold">{clock(printer.started_at)}</span>
          </div>
          <div className="relative h-1 flex-1 rounded-full bg-border">
            <div className={cn("absolute inset-y-0 left-0 rounded-full", paused ? "bg-amber-500" : "bg-brand")} style={{ width: `${pct}%` }} />
          </div>
          <div className="flex flex-col text-right leading-tight">
            <span className="text-[11px] text-muted-foreground">{paused ? "Paused" : "Done at"}</span>
            <span className="font-mono text-[15px] font-semibold">{paused ? "On hold" : clock(printer.eta_at)}</span>
          </div>
        </div>
      )}
    </div>
  );
}
