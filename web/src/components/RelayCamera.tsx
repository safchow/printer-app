import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRelayCamera } from "@/hooks/useRelayCamera";
import type { Printer } from "@/lib/types";

/**
 * Live video through the server's camera relay. Starts by itself when shown;
 * the server keeps one connection to the printer however many pages watch.
 * When live video won't play, it shows a still every 2 seconds instead.
 */
export function RelayCamera({ printer, size = "page" }: { printer: Printer; size?: "card" | "page" }) {
  const offline = printer.state === "offline" || printer.state === "connecting";
  const enabled = printer.has_camera && !offline;
  const { state, mode, still, error, videoRef, retry } = useRelayCamera(printer.id, enabled);
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
        className={state === "live" && mode === "video" ? "absolute inset-0 size-full bg-black object-contain" : "absolute size-px opacity-0"}
        aria-label={`${printer.name} live camera`}
      />
      {mode === "stills" && still && (
        <img src={still} alt={`${printer.name} camera`} className="absolute inset-0 size-full bg-black object-contain" />
      )}
      {state === "live" && mode === "video" && size === "page" && (
        <span className="absolute top-3 left-3 rounded-md bg-background px-2 py-0.5 text-xs font-medium text-red-600">● Live</span>
      )}
      {state === "live" && mode === "stills" && (
        <div className="absolute top-3 left-3 flex items-center gap-2">
          <span className="rounded-md bg-background px-2 py-0.5 text-xs font-medium text-muted-foreground">
            {size === "page" ? "Still every 2 s" : "Stills"}
          </span>
          {size === "page" && (
            <Button variant="outline" size="sm" onClick={press(retry)}>
              <RotateCcw /> Try live video
            </Button>
          )}
        </div>
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
        </div>
      )}
    </>
  );
}
