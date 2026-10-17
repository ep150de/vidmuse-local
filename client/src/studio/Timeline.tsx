import type { StudioScene } from "./types";

interface Props {
  scenes: StudioScene[];
  duration: number;
  selectedIndex: number | null;
  onSelect: (index: number) => void;
}

function formatTc(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

const TYPE_STYLE: Record<string, { bg: string; border: string }> = {
  PERF: { bg: "rgba(232,165,75,0.28)", border: "rgba(232,165,75,0.55)" },
  THEME: { bg: "rgba(61,204,156,0.18)", border: "rgba(61,204,156,0.4)" },
  INTRO_BROLL: { bg: "rgba(141,150,168,0.18)", border: "rgba(141,150,168,0.4)" },
  OUTRO: { bg: "rgba(240,113,120,0.16)", border: "rgba(240,113,120,0.4)" },
};

export function Timeline({ scenes, duration, selectedIndex, onSelect }: Props) {
  const total = Math.max(duration || 1, 1);
  const ticks = 8;

  return (
    <section
      className="h-40 shrink-0 border-t flex flex-col"
      style={{
        borderColor: "var(--border)",
        background: "var(--bg-elevated)",
      }}
      aria-label="Scene timeline"
    >
      <div
        className="px-4 py-2 flex items-center justify-between border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <h2 className="studio-label !mb-0">Timeline</h2>
        <p className="font-mono text-[11px] text-[var(--muted)]">
          {formatTc(0)} — {formatTc(total)}
        </p>
      </div>

      <div className="flex-1 relative px-4 py-3 overflow-x-auto">
        <div
          className="relative h-full min-w-[720px]"
          role="listbox"
          aria-label="Lyric scenes"
          aria-activedescendant={
            selectedIndex != null ? `scene-block-${selectedIndex}` : undefined
          }
        >
          <div className="absolute inset-x-0 top-0 h-4" aria-hidden="true">
            {Array.from({ length: ticks + 1 }).map((_, i) => {
              const t = (total * i) / ticks;
              return (
                <div
                  key={i}
                  className="absolute top-0 h-full border-l"
                  style={{
                    left: `${(i / ticks) * 100}%`,
                    borderColor: "var(--border)",
                  }}
                >
                  <span className="ml-1 font-mono text-[10px] text-[var(--muted)]">
                    {formatTc(t)}
                  </span>
                </div>
              );
            })}
          </div>

          <div className="absolute inset-x-0 top-6 bottom-1">
            {scenes.map((scene) => {
              const left = (scene.start / total) * 100;
              const width = Math.max(
                0.9,
                ((scene.end - scene.start) / total) * 100
              );
              const selected = selectedIndex === scene.index;
              const style = TYPE_STYLE[scene.type] || TYPE_STYLE.THEME;
              return (
                <button
                  key={scene.index}
                  id={`scene-block-${scene.index}`}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  title={`${scene.type}: ${scene.lyric || "instrumental"}`}
                  onClick={() => onSelect(scene.index)}
                  className="absolute top-0 bottom-0 rounded-md px-2 overflow-hidden text-left transition-[box-shadow,opacity]"
                  style={{
                    left: `${left}%`,
                    width: `${width}%`,
                    background: style.bg,
                    border: `1px solid ${selected ? "var(--ink)" : style.border}`,
                    boxShadow: selected
                      ? "0 0 0 1px var(--accent)"
                      : "none",
                    opacity:
                      scene.status === "queued"
                        ? 0.55
                        : scene.status === "failed"
                          ? 0.85
                          : 1,
                  }}
                >
                  <p className="text-[11px] font-semibold truncate text-[var(--ink)]">
                    S{scene.index + 1} {scene.type}
                  </p>
                  <p className="text-[10px] truncate text-[var(--text-secondary)]">
                    {scene.lyric || "—"}
                  </p>
                  <span className="sr-only">{scene.status}</span>
                </button>
              );
            })}
            {scenes.length === 0 && (
              <div
                className="h-full rounded-md border border-dashed flex items-center justify-center text-xs text-[var(--muted)]"
                style={{ borderColor: "var(--border-strong)" }}
              >
                Scene blocks appear after you render
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
