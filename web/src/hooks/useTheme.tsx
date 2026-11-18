import * as React from "react";
import { useJson } from "@/lib/files";

/** One theme from /api/themes (api/app/themes/base.yaml plus the user's themes file). */
export interface ThemeDef {
  id: string;
  name: string;
  mode: "light" | "dark";
  radius: number;
  font: "inter" | "system" | "mono" | "serif";
  source: "base" | "user" | "edited";
  colors: Record<string, string>;
}

export interface ThemeList {
  default: { light: string; dark: string };
  themes: ThemeDef[];
}

/** "system" follows the device: default.light or default.dark. */
export type Choice = "system" | string;

interface Theme {
  choice: Choice;
  /** The theme in use (null until the list has loaded). */
  active: ThemeDef | null;
  dark: boolean;
  list: ThemeList | null;
  setChoice: (c: Choice) => void;
}

const KEY = "printer-theme";
/** The CSS variables last applied, so index.html can paint them before React starts. */
const APPLIED = "printer-theme-applied";
const ThemeContext = React.createContext<Theme | null>(null);

const FONTS: Record<ThemeDef["font"], string> = {
  inter: '"Inter Variable", ui-sans-serif, system-ui, sans-serif',
  system: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  mono: '"Geist Mono", ui-monospace, monospace',
  serif: 'ui-serif, Georgia, Cambria, "Times New Roman", serif',
};

/** A theme's CSS variables: background -> --background, accent -> --brand. */
export function themeVars(t: ThemeDef): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const [k, v] of Object.entries(t.colors)) {
    vars[`--${k.replace(/^accent/, "brand").replace(/_/g, "-")}`] = v;
  }
  vars["--radius"] = `${t.radius / 16}rem`;
  vars["--app-font"] = FONTS[t.font] ?? FONTS.inter;
  return vars;
}

function loadChoice(): Choice {
  try {
    const t = JSON.parse(localStorage.getItem(KEY) || "{}");
    return typeof t.theme === "string" && t.theme ? t.theme : "system";
  } catch {
    return "system";
  }
}

function save(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode: the theme just isn't remembered */
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const { data: list } = useJson<ThemeList>("/api/themes");
  const [choice, setChoice] = React.useState(loadChoice);
  const [systemDark, setSystemDark] = React.useState(() => matchMedia("(prefers-color-scheme: dark)").matches);

  React.useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const on = () => setSystemDark(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const active = React.useMemo(() => {
    if (!list?.themes.length) return null;
    const find = (id: string) => list.themes.find((t) => t.id === id);
    const fallback = find(systemDark ? list.default.dark : list.default.light) ?? list.themes[0];
    // A theme removed from the file since it was picked falls back to System.
    return (choice !== "system" && find(choice)) || fallback;
  }, [list, choice, systemDark]);

  // Before the list loads, keep whatever index.html painted from the last visit.
  const dark = active ? active.mode === "dark" : document.documentElement.classList.contains("dark");

  // A layout effect, so the variables are in place before children's effects read them (the 3D view).
  React.useLayoutEffect(() => {
    if (!active) return;
    const root = document.documentElement;
    const vars = themeVars(active);
    for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
    root.classList.toggle("dark", active.mode === "dark");
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", active.colors.background);
    save(APPLIED, { dark: active.mode === "dark", vars });
  }, [active]);

  React.useEffect(() => save(KEY, { theme: choice }), [choice]);

  const value = React.useMemo<Theme>(() => ({ choice, active, dark, list, setChoice }), [choice, active, dark, list]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
  const t = React.useContext(ThemeContext);
  if (!t) throw new Error("useTheme outside ThemeProvider");
  return t;
}
