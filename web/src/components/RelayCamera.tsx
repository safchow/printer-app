import { Play, RotateCcw, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRelayCamera } from "@/hooks/useRelayCamera";
import type { Printer } from "@/lib/types";

/**
 * Live video through the server's camera relay. Starts by itself when shown;
 * the server keeps one connection to the printer however many pages watch.
 */
export function RelayCamera({ printer, size = "page" }: { printer: Printer; size?: "card" | "page" }) {
  const offline = printer.state === "offline" || printer.state === "connecting";
  const enabled = printer.has_camera && !offline;
  const { state, error, stopped, videoRef, retry, stop } = useRelayCamera(printer.id, enabled);
  if (!enabled) return null;

  // Buttons sit inside the dashboard card's link, so they must not navigate.
  const press = (fn: () => void) => (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    fn();
  };

  return (
    <>
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className={state === "live" ? "absolute inset-0 size-full bg-black object-contain" : "absolute size-px opacity-0"}
        aria-label={`${printer.name} live camera`}
      />
      {state === "live" && size === "page" && (
        <>
          <span className="absolute top-3 left-3 rounded-md bg-background px-2 py-0.5 text-xs font-medium text-red-600">● Live</span>
          <Button variant="outline" size="sm" onClick={press(stop)} className="absolute top-2.5 right-2.5 bg-background/90">
            <Square /> Stop
          </Button>
        </>
      )}
      {state !== "live" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-stage px-6 text-center">
          {state === "connecting" && <p className="m-0 text-sm text-muted-foreground">Starting the camera…</p>}
          {state === "error" && (
            <>
              <p className="m-0 max-w-sm text-sm text-muted-foreground">{error}</p>
              <Button variant={size === "card" ? "outline" : "brand"} size={size === "card" ? "sm" : "default"} onClick={press(retry)}>
                <RotateCcw /> Try again
              </Button>
            </>
          )}
          {stopped && (
            <Button variant="brand" onClick={press(retry)}>
              <Play /> Watch live
            </Button>
          )}
        </div>
      )}
    </>
  );
}
