import * as React from "react";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export const CONTROLS_OFF = "Controls are turned off on this server (CONTROLS_ENABLED in .env).";

/** Wraps a disabled control so it still explains itself on hover or tap. */
export function ControlStub({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className={cn("inline-flex rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}>
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent>{CONTROLS_OFF}</TooltipContent>
    </Tooltip>
  );
}
