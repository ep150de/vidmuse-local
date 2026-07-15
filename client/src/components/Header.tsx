import { Cloud, Cpu, Wifi, WifiOff } from "lucide-react";

interface Props {
  serverHealth: any;
}

export function Header({ serverHealth }: Props) {
  const isOnline = serverHealth?.status === "healthy" || serverHealth?.status === "degraded";
  const mode = serverHealth?.mode || "local";
  const gpu = serverHealth?.gpu || "Unknown";
  const vram = serverHealth?.vram_gb;
  const llm = serverHealth?.services?.llm?.backend || serverHealth?.services?.llm?.name;
  const comfy = serverHealth?.services?.comfyui?.backend;

  const resourceLabel =
    mode === "cloud"
      ? `Cloud · LLM ${llm || "?"} · Comfy ${comfy || "?"}`
      : `${gpu}${vram != null ? ` (${vram}GB)` : ""}`;

  return (
    <header className="border-b border-gray-800 bg-[#0b0f2d]/80 backdrop-blur">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-gradient-to-br from-cyan-500 to-blue-600 rounded-lg flex items-center justify-center">
            <span className="font-bold text-white text-sm">VM</span>
          </div>
          <div className="flex flex-col">
            <span className="font-bold text-lg leading-tight">VidMuse</span>
            <span className="text-[10px] uppercase tracking-wider text-gray-500">
              {mode} mode
            </span>
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs">
          <div className="flex items-center gap-2">
            {mode === "cloud" ? (
              <Cloud className="w-4 h-4 text-cyan-400" />
            ) : (
              <Cpu className="w-4 h-4 text-gray-400" />
            )}
            <span className="text-gray-400 max-w-[280px] truncate" title={resourceLabel}>
              {resourceLabel}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {isOnline ? (
              <>
                <Wifi className="w-4 h-4 text-green-400" />
                <span className="text-green-400">Server Online</span>
              </>
            ) : (
              <>
                <WifiOff className="w-4 h-4 text-red-400" />
                <span className="text-red-400">Server Offline</span>
              </>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
