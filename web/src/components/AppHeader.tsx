import { NavLink, useLocation } from "react-router-dom";
import { Printer as PrinterIcon } from "lucide-react";
import { ThemePicker } from "@/components/ThemePicker";
import { usePrinters, useNow } from "@/hooks/usePrinters";
import { cn } from "@/lib/utils";

function Freshness() {
  const { connected, lastMessageAt } = usePrinters();
  const now = useNow();
  const ago = lastMessageAt ? Math.max(0, Math.round((now - lastMessageAt) / 1000)) : null;
  const text = !connected ? "Reconnecting…" : ago == null ? "Connected" : ago < 60 ? `Updated ${ago}s ago` : `Updated ${Math.round(ago / 60)}m ago`;
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground" aria-live="polite">
      <span className={cn("size-2 rounded-full", connected ? "bg-green-500" : "bg-amber-500")} aria-hidden="true" />
      {text}
    </span>
  );
}

export function AppHeader() {
  // A printer's page counts as part of the dashboard.
  const onPrinter = useLocation().pathname.startsWith("/printers/");
  const link = ({ isActive }: { isActive: boolean }) =>
    cn("rounded-md px-3 py-2 text-sm no-underline", isActive ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground");
  return (
    <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b px-8 py-2.5">
      <div className="flex items-center gap-6">
        <span className="inline-flex items-center gap-2.5 text-[15px] font-semibold">
          <span className="inline-flex size-7 items-center justify-center rounded-[7px] bg-brand text-brand-foreground">
            <PrinterIcon className="size-4" aria-hidden="true" />
          </span>
          Printers
        </span>
        <nav aria-label="Main" className="flex gap-1">
          <NavLink to="/" end className={({ isActive }) => link({ isActive: isActive || onPrinter })}>
            Dashboard
          </NavLink>
          <NavLink to="/files" className={link}>
            Files
          </NavLink>
          <NavLink to="/queue" className={link}>
            Queue
          </NavLink>
          <NavLink to="/history" className={link}>
            History
          </NavLink>
        </nav>
      </div>
      <div className="flex items-center gap-3">
        <Freshness />
        <ThemePicker />
      </div>
    </header>
  );
}
