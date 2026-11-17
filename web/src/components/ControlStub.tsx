import * as React from "react";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export const CONTROLS_LATER = "Controls arrive in a later update. This version only shows status.";

/** Wraps a disabled control so it still explains itself on hover or tap. */
export function ControlStub({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className={cn("inline-flex rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}>
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent>{CONTROLS_LATER}</TooltipContent>
    </Tooltip>
  );
}
