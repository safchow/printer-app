import { Check, Contrast } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetClose, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { ACCENTS, useTheme, type Mode } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";

const MODES: { id: Mode; label: string; left: string; right: string }[] = [
  { id: "system", label: "System", left: "#ffffff", right: "#09090b" },
  { id: "light", label: "Light", left: "#ffffff", right: "#f4f4f5" },
  { id: "dark", label: "Dark", left: "#09090b", right: "#27272a" },
];

function Choices() {
  const { mode, accent, dark, setMode, setAccent } = useTheme();
  return (
    <>
      <div role="radiogroup" aria-label="Mode" className="grid grid-cols-3 gap-2">
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={mode === m.id}
            onClick={() => setMode(m.id)}
            className={cn(
              "flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border-2 p-1.5 text-[13px]",
              mode === m.id ? "border-brand" : "border-transparent",
            )}
          >
            <span className="flex h-12 w-full overflow-hidden rounded-lg border">
              <span className="flex-1" style={{ background: m.left }} />
              <span className="flex-1" style={{ background: m.right }} />
            </span>
            {m.label}
          </button>
        ))}
      </div>
      <div>
        <div className="mb-2 text-[13px] text-muted-foreground">Accent</div>
        <div role="radiogroup" aria-label="Accent colour" className="flex justify-between">
          {ACCENTS.map((a) => {
            const c = dark ? a.dark : a.light;
            return (
              <button
                key={a.id}
                type="button"
                role="radio"
                aria-checked={accent === a.id}
                aria-label={a.name}
                title={a.name}
                onClick={() => setAccent(a.id)}
                className="flex size-10 cursor-pointer items-center justify-center rounded-full border-[3px] border-background"
                style={{ background: c, boxShadow: accent === a.id ? `0 0 0 2px ${c}` : undefined }}
              >
                {accent === a.id && <Check className="size-4" color={a.id === "zinc" && dark ? "#09090b" : "#ffffff"} aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}

export function ThemePicker() {
  const mobile = useIsMobile();
  const trigger = (
    <Button variant="outline" size="icon" aria-label="Appearance" className={mobile ? "size-11" : undefined}>
      <Contrast />
    </Button>
  );
  if (mobile) {
    return (
      <Sheet>
        <SheetTrigger asChild>{trigger}</SheetTrigger>
        <SheetContent>
          <div className="flex items-center justify-between">
            <SheetTitle className="m-0 text-[17px] font-semibold">Appearance</SheetTitle>
            <SheetClose asChild>
              <Button variant="ghost" size="sm" className="text-muted-foreground">
                Done
              </Button>
            </SheetClose>
          </div>
          <Choices />
        </SheetContent>
      </Sheet>
    );
  }
  return (
    <Popover>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent className="flex flex-col gap-4" aria-label="Appearance">
        <div className="text-[15px] font-semibold">Appearance</div>
        <Choices />
      </PopoverContent>
    </Popover>
  );
}
