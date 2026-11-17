import * as React from "react";

export type Mode = "system" | "light" | "dark";
export const ACCENTS = [
  { id: "zinc", name: "Zinc", light: "#3f3f46", dark: "#d4d4d8" },
  { id: "blue", name: "Blue", light: "#2563eb", dark: "#2563eb" },
  { id: "violet", name: "Violet", light: "#7c3aed", dark: "#7c3aed" },
  { id: "green", name: "Green", light: "#15803d", dark: "#15803d" },
  { id: "orange", name: "Orange", light: "#c2410c", dark: "#c2410c" },
  { id: "rose", name: "Rose", light: "#e11d48", dark: "#e11d48" },
] as const;
export type Accent = (typeof ACCENTS)[number]["id"];

interface Theme {
  mode: Mode;
  accent: Accent;
  dark: boolean;
  setMode: (m: Mode) => void;
  setAccent: (a: Accent) => void;
}

const KEY = "printer-theme";
const ThemeContext = React.createContext<Theme | null>(null);

function load(): { mode: Mode; accent: Accent } {
  try {
    const t = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      mode: ["system", "light", "dark"].includes(t.mode) ? t.mode : "system",
      accent: ACCENTS.some((a) => a.id === t.accent) ? t.accent : "blue",
    };
  } catch {
    return { mode: "system", accent: "blue" };
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState(load);
  const [systemDark, setSystemDark] = React.useState(() => matchMedia("(prefers-color-scheme: dark)").matches);

  React.useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const on = () => setSystemDark(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const dark = state.mode === "dark" || (state.mode === "system" && systemDark);

  React.useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", dark);
    root.dataset.accent = state.accent;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#09090b" : "#ffffff");
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch {
      /* private mode: the theme just isn't remembered */
    }
  }, [state, dark]);

  const value = React.useMemo<Theme>(
    () => ({
      ...state,
      dark,
      setMode: (mode) => setState((s) => ({ ...s, mode })),
      setAccent: (accent) => setState((s) => ({ ...s, accent })),
    }),
    [state, dark],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
  const t = React.useContext(ThemeContext);
  if (!t) throw new Error("useTheme outside ThemeProvider");
  return t;
}
