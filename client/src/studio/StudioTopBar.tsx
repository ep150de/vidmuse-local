import { Cloud, Download, Play, Server } from "lucide-react";
import type { ComfyBackend } from "./types";

interface Props {
  trackTitle: string;
  comfyBackend: ComfyBackend;
  comfyUrl: string;
  h3Ready: boolean | null;
  status: string;
  progressLabel: string;
  progressPct: number;
  canRender: boolean;
  rendering: boolean;
  downloadUrl: string | null;
  onComfyBackend: (b: ComfyBackend) => void;
  onComfyUrl: (url: string) => void;
  onRender: () => void;
  onExport: () => void;
}

export function StudioTopBar({
  trackTitle,
  comfyBackend,
  comfyUrl,
  h3Ready,
  status,
  progressLabel,
  progressPct,
  canRender,
  rendering,
  downloadUrl,
  onComfyBackend,
  onComfyUrl,
  onRender,
  onExport,
}: Props) {
  return (
    <header
      className="h-14 shrink-0 border-b px-4 flex items-center gap-4"
      style={{
        borderColor: "var(--border)",
        background: "color-mix(in srgb, var(--bg-elevated) 92%, transparent)",
      }}
      role="banner"
    >
      <div className="flex items-center gap-3 min-w-0">
        <div
          className="w-8 h-8 rounded-lg flex items-center justify-center font-display text-xs font-bold"
          style={{ background: "var(--accent)", color: "var(--accent-ink)" }}
          aria-hidden="true"
        >
          VM
        </div>
        <div className="min-w-0">
          <p className="font-display text-sm font-semibold truncate leading-tight">
            {trackTitle || "Untitled project"}
          </p>
          <p className="text-[11px] text-[var(--muted)] truncate">
            SceneMux · MiniMax H3 · {status || "idle"}
          </p>
        </div>
      </div>

      <div className="flex-1" />

      <div
        className="hidden md:flex items-center gap-2"
        role="group"
        aria-label="Comfy engine"
      >
        {(
          [
            { id: "cloud" as const, label: "Cloud", Icon: Cloud },
            { id: "local" as const, label: "Local", Icon: Server },
          ] as const
        ).map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            className="studio-chip"
            aria-pressed={comfyBackend === id}
            onClick={() => onComfyBackend(id)}
          >
            <Icon className="w-3.5 h-3.5" aria-hidden="true" />
            {label}
          </button>
        ))}
        {comfyBackend !== "cloud" && (
          <label className="flex items-center gap-2">
            <span className="sr-only">Local Comfy URL</span>
            <input
              className="studio-field w-48 !py-1.5 !text-xs font-mono"
              value={comfyUrl}
              onChange={(e) => onComfyUrl(e.target.value)}
              placeholder="http://localhost:8188"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
        )}
        {comfyBackend !== "cloud" && h3Ready != null && (
          <span
            className="text-[11px] font-medium"
            style={{ color: h3Ready ? "var(--success)" : "var(--accent)" }}
          >
            {h3Ready ? "H3 weights ready" : "H3 weights missing"}
          </span>
        )}
        {comfyBackend === "cloud" && (
          <span className="text-[11px] text-[var(--muted)]">
            cloud.comfy.org
          </span>
        )}
      </div>

      {(rendering || progressPct > 0) && (
        <div
          className="hidden sm:flex flex-col w-44 gap-1"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <span className="text-[11px] text-[var(--muted)] truncate">
            {progressLabel || `${progressPct}%`}
          </span>
          <div
            className="h-1.5 rounded-full overflow-hidden"
            style={{ background: "var(--surface-2)" }}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progressPct)}
            aria-label="Render progress"
          >
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{
                width: `${Math.min(100, progressPct)}%`,
                background: "linear-gradient(90deg, var(--accent), #f0c27a)",
              }}
            />
          </div>
        </div>
      )}

      <button
        type="button"
        className="studio-btn studio-btn-primary"
        disabled={!canRender || rendering}
        onClick={onRender}
        aria-busy={rendering}
      >
        <Play className="w-3.5 h-3.5" aria-hidden="true" />
        {rendering ? "Rendering…" : "Render"}
      </button>

      <button
        type="button"
        className="studio-btn studio-btn-ghost"
        disabled={!downloadUrl}
        onClick={onExport}
      >
        <Download className="w-3.5 h-3.5" aria-hidden="true" />
        Export
      </button>
    </header>
  );
}
