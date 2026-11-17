import * as React from "react";
import { Play } from "lucide-react";
import { RelayCamera } from "@/components/RelayCamera";
import { Button } from "@/components/ui/button";
import { useCamera } from "@/hooks/useCamera";
import { usePrinters } from "@/hooks/usePrinters";
import type { Printer } from "@/lib/types";

/**
 * The camera, laid over a CameraStage. Through the server's relay when it is
 * set up (starts by itself); otherwise straight from the printer, on a tap.
 */
export function LiveCamera({ printer, size = "page" }: { printer: Printer; size?: "card" | "page" }) {
  const { config } = usePrinters();
  if (!config) return null;
  if (config.camera_mode === "relay") return <RelayCamera printer={printer} size={size} />;
  if (size === "card") return null;
  return <DirectCamera printer={printer} />;
}

/** Direct WebRTC from the printer: video plus a start button. */
function DirectCamera({ printer }: { printer: Printer }) {
  const { state, error, stream, start } = useCamera(printer.id);
  const video = React.useRef<HTMLVideoElement>(null);

  React.useEffect(() => {
    const el = video.current;
    if (!el) return;
    el.srcObject = stream;
    // Safari doesn't always honour autoplay for a stream set later, so ask.
    if (stream) el.play().catch(() => {});
  }, [stream]);

  const offline = printer.state === "offline" || printer.state === "connecting";
  if (!printer.has_camera || offline) return null;

  return (
    <>
      <video
        ref={video}
        autoPlay
        playsInline
        muted
        className={stream ? "absolute inset-0 size-full bg-black object-contain" : "hidden"}
        aria-label={`${printer.name} live camera`}
      />
      {state === "live" ? (
        <span className="absolute top-3 left-3 rounded-md bg-background px-2 py-0.5 text-xs font-medium text-red-600">● Live</span>
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-stage px-6 text-center">
          {state === "error" && <p className="m-0 max-w-sm text-sm text-muted-foreground">{error}</p>}
          <Button variant="brand" onClick={start} disabled={state === "connecting"}>
            <Play /> {state === "connecting" ? "Connecting…" : state === "error" ? "Try again" : "Watch live"}
          </Button>
        </div>
      )}
    </>
  );
}
