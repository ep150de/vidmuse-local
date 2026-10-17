import { useEffect, useRef } from "react";
import { mediaUrl } from "../api";
import type { StudioScene } from "./types";

interface Props {
  scene: StudioScene | null;
  finalUrl: string | null;
  showFinal: boolean;
  onToggleFinal: (v: boolean) => void;
}

function formatTc(sec: number): string {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${String(m).padStart(2, "0")}:${r.toFixed(1).padStart(4, "0")}`;
}

export function ProgramMonitor({
  scene,
  finalUrl,
  showFinal,
  onToggleFinal,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const src =
    showFinal && finalUrl
      ? finalUrl
      : scene?.video_url
        ? mediaUrl(scene.video_url)
        : "";

  useEffect(() => {
    const el = videoRef.current;
    if (el && src) el.load();
  }, [src]);

  return (
    <section
      id="studio-main"
      className="flex-1 min-w-0 min-h-0 flex flex-col"
      style={{ background: "var(--bg)" }}
      aria-label="Program monitor"
    >
      <div
        className="px-4 py-2 border-b flex items-center justify-between gap-3"
        style={{ borderColor: "var(--border)" }}
      >
        <h2 className="studio-label !mb-0">Monitor</h2>
        <div className="flex items-center gap-2">
          {finalUrl && (
            <div
              className="flex items-center gap-1"
              role="group"
              aria-label="Monitor source"
            >
              <button
                type="button"
                className="studio-chip"
                aria-pressed={!showFinal}
                onClick={() => onToggleFinal(false)}
              >
                Scene
              </button>
              <button
                type="button"
                className="studio-chip"
                aria-pressed={showFinal}
                onClick={() => onToggleFinal(true)}
              >
                Final cut
              </button>
            </div>
          )}
          {scene && !showFinal && (
            <span className="font-mono text-[11px] text-[var(--muted)]">
              {formatTc(scene.start)} – {formatTc(scene.end)}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 flex items-center justify-center p-4 md:p-6">
        <div
          className="relative w-full max-w-5xl aspect-video overflow-hidden"
          style={{
            borderRadius: "calc(var(--radius) + 2px)",
            border: "1px solid var(--border)",
            background:
              "linear-gradient(180deg, #0a0c10 0%, #050608 100%)",
            boxShadow: "0 24px 64px rgba(0,0,0,0.45)",
          }}
        >
          {src ? (
            <video
              ref={videoRef}
              key={src}
              src={src}
              controls
              className="w-full h-full object-contain"
              aria-label={
                showFinal
                  ? "Final assembled music video"
                  : `Preview of scene ${(scene?.index ?? 0) + 1}`
              }
            />
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-8">
              <p className="font-display text-lg font-semibold tracking-tight">
                Ready when you are
              </p>
              <p className="text-sm text-[var(--text-secondary)] mt-2 max-w-md">
                Import a song, write a direction, then render. MiniMax H3 builds
                one unique clip per lyric scene and keeps your original audio.
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
