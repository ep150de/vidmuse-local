import { RefreshCw } from "lucide-react";
import type { StudioScene } from "./types";

interface Props {
  direction: string;
  lyrics: string;
  scene: StudioScene | null;
  regenerating: boolean;
  onDirection: (v: string) => void;
  onLyrics: (v: string) => void;
  onScenePrompt: (v: string) => void;
  onRegenerate: () => void;
}

export function Inspector({
  direction,
  lyrics,
  scene,
  regenerating,
  onDirection,
  onLyrics,
  onScenePrompt,
  onRegenerate,
}: Props) {
  return (
    <aside
      className="w-80 shrink-0 border-l flex flex-col min-h-0"
      style={{
        borderColor: "var(--border)",
        background: "var(--bg-elevated)",
      }}
      aria-label="Inspector"
    >
      <div
        className="px-4 py-3 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <h2 className="studio-label !mb-0">Inspector</h2>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        <label className="block">
          <span className="studio-label">Direction</span>
          <textarea
            value={direction}
            onChange={(e) => onDirection(e.target.value)}
            rows={4}
            placeholder="Neon West Coast MTV look, golden-hour LA, confident performance, no on-screen text…"
            className="studio-field resize-none text-sm"
          />
        </label>

        <label className="block">
          <span className="studio-label">Lyrics override</span>
          <textarea
            value={lyrics}
            onChange={(e) => onLyrics(e.target.value)}
            rows={3}
            placeholder="Optional — Whisper extracts from the MP3 if blank"
            className="studio-field resize-none text-sm"
          />
        </label>

        {scene ? (
          <div
            className="rounded-[var(--radius)] border p-3 space-y-3"
            style={{
              borderColor: "var(--border)",
              background: "var(--surface)",
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-semibold">
                Scene {scene.index + 1}
                <span className="text-[var(--muted)] font-medium">
                  {" "}
                  · {scene.type}
                </span>
              </p>
              <span className="font-mono text-[11px] text-[var(--muted)]">
                {scene.duration.toFixed(1)}s
              </span>
            </div>
            {scene.lyric && (
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                “{scene.lyric}”
              </p>
            )}
            <label className="block">
              <span className="studio-label">Shot prompt</span>
              <textarea
                value={scene.prompt}
                onChange={(e) => onScenePrompt(e.target.value)}
                rows={7}
                className="studio-field resize-none text-xs leading-relaxed"
              />
            </label>
            <button
              type="button"
              className="studio-btn studio-btn-soft w-full"
              disabled={regenerating}
              onClick={onRegenerate}
              aria-busy={regenerating}
            >
              <RefreshCw
                className={`w-3.5 h-3.5 ${regenerating ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              {regenerating ? "Regenerating…" : "Regenerate scene"}
            </button>
          </div>
        ) : (
          <p className="text-xs text-[var(--muted)] leading-relaxed">
            Select a timeline block to edit its MiniMax H3 prompt and regenerate
            that take.
          </p>
        )}
      </div>
    </aside>
  );
}
