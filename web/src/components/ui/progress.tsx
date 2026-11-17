import { cn } from "@/lib/utils";

export function Progress({ value, className, barClassName }: { value: number | null | undefined; className?: string; barClassName?: string }) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div
      className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div className={cn("h-full rounded-full bg-brand transition-[width] duration-500", barClassName)} style={{ width: `${pct}%` }} />
    </div>
  );
}
