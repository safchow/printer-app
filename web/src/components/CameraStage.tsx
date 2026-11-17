import type { Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

/** The camera area. Live video is laid over it as children. */
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
    </div>
  );
}
