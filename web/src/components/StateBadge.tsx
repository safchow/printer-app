import { STATE_LABEL } from "@/lib/format";
import type { PrinterState } from "@/lib/types";
import { cn } from "@/lib/utils";

const STYLE: Record<PrinterState, string> = {
  printing: "bg-brand text-brand-foreground",
  paused: "bg-warn text-warn-foreground",
  idle: "border bg-background",
  complete: "border bg-background",
  cancelled: "bg-muted text-muted-foreground",
  error: "bg-offline text-offline-foreground",
  offline: "bg-offline text-offline-foreground",
  connecting: "bg-muted text-muted-foreground",
};

export function StateBadge({ state, className }: { state: PrinterState; className?: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium", STYLE[state], className)}>
      {STATE_LABEL[state]}
    </span>
  );
}
