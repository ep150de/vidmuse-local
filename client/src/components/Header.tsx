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

  const resourceLabel =
    mode === "cloud"
      ? `Cloud · LLM ${llm || "?"} · Comfy ${comfy || "?"}${comfyStatus && comfyStatus !== "up" ? ` (${comfyStatus})` : ""}`
      : `${gpu}${vram != null ? ` (${vram}GB)` : ""}`;

  return (
    <header className="border-b border-cyan-500/15 bg-[#070b1c]/75 backdrop-blur-md sticky top-0 z-30">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 bg-gradient-to-br from-cyan-300 via-cyan-400 to-gold-500 flex items-center justify-center shrink-0 shadow-[0_0_18px_rgba(34,211,238,0.25)]">
            <span className="font-display font-bold text-navy-900 text-xs">VM</span>
          </div>
          <div className="min-w-0">
            <span className="font-display font-bold text-lg leading-none tracking-wide block truncate">
              VidMuse
            </span>
            <span className="text-[10px] uppercase tracking-[0.22em] text-cyan-400/80">
              {mode} · LTX-2.3
            </span>
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs shrink-0">
          <div className="hidden sm:flex items-center gap-2 text-[var(--muted)]">
            {mode === "cloud" ? (
              <Cloud className="w-4 h-4 text-cyan-400" />
            ) : (
              <Cpu className="w-4 h-4 text-gold-500" />
            )}
            <span className="max-w-[220px] truncate" title={resourceLabel}>
              {resourceLabel}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {isOnline ? (
              <>
                <Wifi className="w-4 h-4 text-cyan-400" />
                <span className="text-cyan-300 hidden md:inline">Online</span>
              </>
            ) : (
              <>
                <WifiOff className="w-4 h-4 text-red-400" />
                <span className="text-red-400">Offline</span>
              </>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
