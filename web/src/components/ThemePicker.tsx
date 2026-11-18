import * as React from "react";
import { Check, Contrast, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetClose, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { useTheme, type ThemeDef } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";

type Filter = "all" | "dark" | "light";

const FONT_PREVIEW: Record<ThemeDef["font"], string> = {
  inter: '"Inter Variable", sans-serif',
  system: "ui-sans-serif, system-ui, sans-serif",
  mono: '"Geist Mono", monospace',
  serif: "ui-serif, Georgia, serif",
};

/** A tiny picture of the theme: page, text, a card and the accent button. */
function Preview({ t, className, style }: { t: ThemeDef; className?: string; style?: React.CSSProperties }) {
  const c = t.colors;
  const r = Math.min(t.radius, 10);
  return (
    <span className={cn("flex flex-col gap-1 p-1.5", className)} style={{ background: c.background, ...style }}>
      <span className="h-1 w-3/5 rounded-full" style={{ background: c.foreground }} />
      <span className="h-1 w-2/5 rounded-full" style={{ background: c.muted_foreground }} />
      <span
        className="mt-auto flex items-center justify-between border px-1 py-0.5"
        style={{ background: c.card, borderColor: c.border, borderRadius: r * 0.6 }}
      >
        <span className="h-1 w-1/3 rounded-full" style={{ background: c.muted_foreground }} />
        <span
          className="px-1 text-[9px] leading-[13px] font-semibold"
          style={{ background: c.accent, color: c.accent_foreground, borderRadius: r * 0.4, fontFamily: FONT_PREVIEW[t.font] }}
        >
          Aa
        </span>
      </span>
    </span>
  );
}

function Tile({
  selected,
  label,
  title,
  onPick,
  children,
}: {
  selected: boolean;
  label: string;
  title?: string;
  onPick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      title={title ?? label}
      onClick={onPick}
      data-selected={selected || undefined}
      className={cn(
        "group flex min-w-0 cursor-pointer flex-col gap-1 rounded-lg border-2 bg-transparent p-1 text-left text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected ? "border-brand" : "border-transparent hover:border-border",
      )}
    >
      <span className="relative block h-14 overflow-hidden rounded-md border">
        {children}
        {selected && (
          <span className="absolute top-1 right-1 flex size-4 items-center justify-center rounded-full bg-brand text-brand-foreground">
            <Check className="size-3" strokeWidth={3} aria-hidden="true" />
          </span>
        )}
      </span>
      <span className="line-clamp-2 px-0.5 leading-tight break-words text-foreground">{label}</span>
    </button>
  );
}

function Section({ title, themes, choice, pick }: { title: string; themes: ThemeDef[]; choice: string; pick: (id: string) => void }) {
  if (!themes.length) return null;
  return (
    <section>
      <h3 className="sticky top-0 z-10 m-0 bg-popover py-1.5 text-xs font-medium text-muted-foreground">
        {title} <span className="tabular-nums">· {themes.length}</span>
      </h3>
      <div className="grid grid-cols-3 gap-1">
        {themes.map((t) => (
          <Tile key={t.id} selected={choice === t.id} label={t.name} onPick={() => pick(t.id)}>
            <Preview t={t} className="size-full" />
          </Tile>
        ))}
      </div>
    </section>
  );
}

function Choices({ scrollClass }: { scrollClass: string }) {
  const { choice, list, setChoice } = useTheme();
  const [filter, setFilter] = React.useState<Filter>("all");
  const [query, setQuery] = React.useState("");
  const scroller = React.useRef<HTMLDivElement>(null);

  // Open with the current theme in view.
  React.useEffect(() => {
    const el = scroller.current?.querySelector<HTMLElement>("[data-selected]");
    el?.scrollIntoView({ block: "center" });
  }, [list]);

  if (!list) return <p className="m-0 py-8 text-center text-sm text-muted-foreground">Loading themes…</p>;

  const q = query.trim().toLowerCase();
  const shown = list.themes.filter((t) => (filter === "all" || t.mode === filter) && (!q || `${t.name} ${t.id}`.toLowerCase().includes(q)));
  const yours = shown.filter((t) => t.source !== "base");
  const base = shown.filter((t) => t.source === "base");
  const light = list.themes.find((t) => t.id === list.default.light);
  const dark = list.themes.find((t) => t.id === list.default.dark);
  const showSystem = !q || "system".includes(q) || "device".includes(q);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex gap-2">
        <label className="relative flex min-w-0 flex-1 items-center">
          <Search className="pointer-events-none absolute left-2.5 size-4 text-muted-foreground" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search themes"
            aria-label="Search themes"
            className="h-9 w-full min-w-0 rounded-md border border-input bg-background pr-2 pl-8 text-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <div role="radiogroup" aria-label="Show" className="flex shrink-0 rounded-md border bg-muted p-0.5">
          {(["all", "dark", "light"] as const).map((f) => (
            <button
              key={f}
              type="button"
              role="radio"
              aria-checked={filter === f}
              onClick={() => setFilter(f)}
              className={cn(
                "cursor-pointer rounded-[calc(var(--radius)-4px)] border-0 px-2 text-xs font-medium capitalize outline-none focus-visible:ring-2 focus-visible:ring-ring",
                filter === f ? "bg-background text-foreground shadow-sm" : "bg-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <div
        ref={scroller}
        role="radiogroup"
        aria-label="Theme"
        className={cn("-mr-2 flex min-h-0 flex-col gap-2 overflow-y-auto overscroll-contain pr-2", scrollClass)}
      >
        {showSystem && filter === "all" && light && dark && (
          <div className="grid grid-cols-3 gap-1">
            <Tile selected={choice === "system"} label="System" title={`Matches your device: ${light.name} or ${dark.name}`} onPick={() => setChoice("system")}>
              <span className="flex size-full">
                <Preview t={light} className="w-1/2" />
                <Preview t={dark} className="w-1/2" />
              </span>
            </Tile>
            <p className="col-span-2 m-0 self-center px-1 text-xs leading-snug text-muted-foreground">
              Matches your device: {light.name} by day, {dark.name} at night.
            </p>
          </div>
        )}
        <Section title="Yours" themes={yours} choice={choice} pick={setChoice} />
        <Section title="Dark" themes={base.filter((t) => t.mode === "dark")} choice={choice} pick={setChoice} />
        <Section title="Light" themes={base.filter((t) => t.mode === "light")} choice={choice} pick={setChoice} />
        {!shown.length && !(showSystem && filter === "all") && (
          <p className="m-0 py-8 text-center text-sm text-muted-foreground">No themes match “{query}”.</p>
        )}
      </div>

      <p className="m-0 border-t pt-2.5 text-xs leading-snug text-muted-foreground">
        Add your own in <code className="font-mono text-foreground">themes.yaml</code> in the data folder. They show up at the top.
      </p>
    </div>
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
        <SheetContent className="max-h-[88dvh] bg-popover">
          <div className="flex items-center justify-between">
            <SheetTitle className="m-0 text-[17px] font-semibold">Appearance</SheetTitle>
            <SheetClose asChild>
              <Button variant="ghost" size="sm" className="text-muted-foreground">
                Done
              </Button>
            </SheetClose>
          </div>
          <Choices scrollClass="flex-1" />
        </SheetContent>
      </Sheet>
    );
  }
  return (
    <Popover>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent
        className="flex w-[360px] flex-col gap-3 max-h-[min(600px,var(--radix-popover-content-available-height))]"
        collisionPadding={12}
        onOpenAutoFocus={(e) => e.preventDefault()}
        aria-label="Appearance"
      >
        <div className="text-[15px] font-semibold">Appearance</div>
        <Choices scrollClass="max-h-[400px]" />
      </PopoverContent>
    </Popover>
  );
}
