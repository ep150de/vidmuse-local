/** User-facing product identity. Swap name/mark to MuxLane or Framechoir if desired. */
export const BRAND = {
  name: "SceneMux",
  mark: "SM",
  tagline: "Music video studio",
  subtitle: "From track to cut",
} as const;

export type ThemeId = "studio" | "crt" | "pastel" | "plain";

export const THEMES: Array<{ id: ThemeId; label: string }> = [
  { id: "studio", label: "Studio" },
  { id: "crt", label: "CRT" },
  { id: "pastel", label: "Pastel" },
  { id: "plain", label: "Plain" },
];

const THEME_KEY = "scenemux-theme";

export function getStoredTheme(): ThemeId {
  try {
    const raw = localStorage.getItem(THEME_KEY);
    if (raw === "studio" || raw === "crt" || raw === "pastel" || raw === "plain") {
      return raw;
    }
  } catch {
    /* ignore */
  }
  return "studio";
}

export function applyTheme(theme: ThemeId) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* ignore */
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    const colors: Record<ThemeId, string> = {
      studio: "#0e1014",
      crt: "#020803",
      pastel: "#f3eef8",
      plain: "#f4f4f5",
    };
    meta.setAttribute("content", colors[theme]);
  }
}
