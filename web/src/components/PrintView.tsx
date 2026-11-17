import * as React from "react";
import { Box, Video } from "lucide-react";
import { CameraStage } from "@/components/CameraStage";
import { LiveCamera } from "@/components/LiveCamera";
import type { Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

// three.js is big: load the 3D preview only when a page shows it.
const LazyPreview = React.lazy(() => import("@/components/GcodePreview").then((m) => ({ default: m.GcodePreview })));

export function GcodePreview(props: { p: Printer; className?: string; interactive?: boolean }) {
  return (
    <React.Suspense fallback={<div className={props.className} />}>
      <LazyPreview {...props} />
    </React.Suspense>
  );
}

const LazyFileView = React.lazy(() => import("@/components/GcodePreview").then((m) => ({ default: m.GcodeFileView })));

export function GcodeFileView(props: { url: string; className?: string; interactive?: boolean }) {
  return (
    <React.Suspense fallback={<div className={props.className} />}>
      <LazyFileView {...props} />
    </React.Suspense>
  );
}

/**
 * The main view of a printer: the 3D model of the print, with the live camera
 * one tap away. The camera only connects while it is shown.
 */
export function PrintView({
  p,
  size = "page",
  className,
  overlay,
}: {
  p: Printer;
  size?: "card" | "page";
  className?: string;
  /** Laid over the top-left corner (a state badge on cards). */
  overlay?: React.ReactNode;
}) {
  const [camera, setCamera] = React.useState(false);
  const canCamera = p.has_camera && p.state !== "offline" && p.state !== "connecting";
  const showCamera = camera && canCamera;
  const card = size === "card";

  const toggle = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setCamera((c) => !c);
  };

  return (
    <div className={cn("relative overflow-hidden bg-stage", className)}>
      {showCamera ? (
        // Above a card's stretched link, so the camera's own buttons work.
        <CameraStage printer={p} size="page" className="relative z-10 size-full">
          <LiveCamera printer={p} size="page" />
        </CameraStage>
      ) : (
        <GcodePreview p={p} className="size-full" interactive={!card} />
      )}
      {overlay}
      {canCamera && (
        <button
          type="button"
          onClick={toggle}
          aria-pressed={showCamera}
          className={cn(
            "absolute z-20 inline-flex items-center gap-1.5 rounded-md border bg-background/90 font-medium text-foreground shadow-sm backdrop-blur hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            card ? "top-2.5 right-2.5 h-8 px-2.5 text-xs" : "top-3 right-3 h-9 px-3 text-[13px]",
          )}
        >
          {showCamera ? <Box className="size-4" aria-hidden="true" /> : <Video className="size-4" aria-hidden="true" />}
          {showCamera ? "Model" : "Camera"}
        </button>
      )}
    </div>
  );
}
