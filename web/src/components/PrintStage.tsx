import * as React from "react";
import { Box, Video } from "lucide-react";
import { CameraStage } from "@/components/CameraStage";
import { LiveCamera } from "@/components/LiveCamera";
import type { Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

// three.js is big: load the 3D preview in its own chunk, only when a stage shows it.
const LazyPreview = React.lazy(() => import("@/components/GcodePreview").then((m) => ({ default: m.GcodePreview })));

type View = "model" | "camera";

/**
 * The main picture for a printer: the print's 3D model by default, with the
 * live camera one tap away. The camera only starts when someone picks it.
 *
 * `size="card"` is for dashboard cards, which sit inside a link: the model is
 * not draggable there and the switch's buttons don't navigate.
 */
export function PrintStage({
  p,
  size = "page",
  className,
  children,
}: {
  p: Printer;
  size?: "card" | "page";
  className?: string;
  children?: React.ReactNode;
}) {
  const [view, setView] = React.useState<View>("model");
  const card = size === "card";

  return (
    <div className={cn("relative overflow-hidden bg-stage", className)}>
      {view === "model" ? (
        <React.Suspense fallback={null}>
          <LazyPreview p={p} interactive={!card} className="absolute inset-0" />
        </React.Suspense>
      ) : (
        <CameraStage printer={p} size={size} className="absolute inset-0">
          <LiveCamera printer={p} size={size} />
        </CameraStage>
      )}
      {children}
      {p.has_camera && <ViewSwitch view={view} onChange={setView} small={card} />}
    </div>
  );
}

function ViewSwitch({ view, onChange, small }: { view: View; onChange: (v: View) => void; small?: boolean }) {
  const options: { v: View; label: string; Icon: typeof Box }[] = [
    { v: "model", label: "Model", Icon: Box },
    { v: "camera", label: "Camera", Icon: Video },
  ];
  return (
    <div
      role="radiogroup"
      aria-label="Show"
      className={cn("absolute top-3 right-3 z-10 flex rounded-lg border bg-background/90 p-0.5 shadow-sm backdrop-blur", small && "top-2.5 right-2.5")}
    >
      {options.map(({ v, label, Icon }) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={view === v}
          onClick={(e) => {
            // On dashboard cards this sits inside the card's link.
            e.preventDefault();
            e.stopPropagation();
            onChange(v);
          }}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
            small ? "px-2 py-1 text-[11px]" : "px-2.5 py-1 text-xs",
            view === v ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icon className={small ? "size-3" : "size-3.5"} />
          {label}
        </button>
      ))}
    </div>
  );
}
