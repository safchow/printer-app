import * as React from "react";
import { Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCamera } from "@/hooks/useCamera";
import type { Printer } from "@/lib/types";

/** Video plus start / stop controls, laid over a CameraStage. */
export function LiveCamera({ printer }: { printer: Printer }) {
  const { state, error, stream, start, stop } = useCamera(printer.id);
  const video = React.useRef<HTMLVideoElement>(null);

  React.useEffect(() => {
    if (video.current) video.current.srcObject = stream;
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
        <>
          <span className="absolute top-3 left-3 rounded-md bg-background px-2 py-0.5 text-xs font-medium text-red-600">● Live</span>
          <Button variant="outline" size="sm" onClick={stop} className="absolute top-2.5 right-2.5 bg-background/90">
            <Square /> Stop
          </Button>
        </>
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
