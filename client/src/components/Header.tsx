import { Cloud, Cpu, Wifi, WifiOff } from "lucide-react";

interface Props {
  serverHealth: any;
}

export function Header({ serverHealth }: Props) {
  const isOnline =
    serverHealth?.status === "healthy" || serverHealth?.status === "degraded";
  const mode = serverHealth?.mode || "local";
  const gpu = serverHealth?.gpu || "Unknown";
  const vram = serverHealth?.vram_gb;
  const llm =
    serverHealth?.services?.llm?.backend || serverHealth?.services?.llm?.name;
  const comfy = serverHealth?.services?.comfyui?.backend;
  const comfyStatus = serverHealth?.services?.comfyui?.status;
  const comfyUrl = serverHealth?.services?.comfyui?.url;
  const queue = serverHealth?.services?.comfyui?.queue;
  const modelCount = serverHealth?.services?.comfyui?.model_count;

  const queueLabel =
    queue && typeof queue.pending === "number"
      ? ` · q ${queue.running || 0}/${queue.pending}`
      : "";
  const resourceLabel =
    `LLM ${llm || "?"} · Comfy ${comfy || "?"}` +
    (comfyStatus && comfyStatus !== "up" ? ` (${comfyStatus})` : "") +
    (modelCount != null ? ` · ${modelCount} models` : "") +
    queueLabel +
    (mode !== "cloud" && gpu ? ` · ${gpu}${vram != null ? ` ${vram}GB` : ""}` : "");

  return (
    <header className="border-b border-[var(--border)] bg-[#070b14]/80 backdrop-blur-md sticky top-0 z-30">
      <div className="max-w-6xl mx-auto px-4 py-3.5 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-sky-300 via-sky-400 to-amber-400 flex items-center justify-center shrink-0">
            <span className="font-display font-bold text-[#071018] text-xs">VM</span>
          </div>
          <div className="min-w-0">
            <span className="font-display font-semibold text-lg leading-none tracking-tight block truncate text-[var(--ink)]">
              VidMuse
            </span>
            <span className="text-[10px] uppercase tracking-[0.2em] text-[var(--text-secondary)]">
              {mode} studio
            </span>
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs shrink-0">
          <div className="hidden sm:flex items-center gap-2 text-[var(--text-secondary)]">
            {mode === "cloud" ? (
              <Cloud className="w-4 h-4 text-[var(--accent)]" />
            ) : (
              <Cpu className="w-4 h-4 text-amber-400" />
            )}
            <span
              className="max-w-[280px] truncate"
              title={`${resourceLabel}${comfyUrl ? ` · ${comfyUrl}` : ""}`}
            >
              {resourceLabel}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {isOnline ? (
              <>
                <Wifi className="w-4 h-4 text-emerald-400" />
                <span className="text-emerald-300 hidden md:inline">Online</span>
              </>
            ) : (
              <>
                <WifiOff className="w-4 h-4 text-red-400" />
                <span className="text-red-300">Offline</span>
              </>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
