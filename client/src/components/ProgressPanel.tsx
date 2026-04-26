interface Props {
  progress: { phase: string; progress: number; status: string };
}

export function ProgressPanel({ progress }: Props) {
  if (!progress.status) return null;

  return (
    <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 md:w-80 bg-[#1a1f3d]/90 backdrop-blur rounded-xl border border-gray-700 p-4 z-40">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-cyan-400 uppercase">{progress.phase}</span>
        <span className="text-xs text-gray-400">{progress.progress}%</span>
      </div>
      <div className="w-full h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-cyan-400 transition-all duration-500"
          style={{ width: `${progress.progress}%` }}
        />
      </div>
      <p className="text-xs text-gray-400 mt-2 truncate">{progress.status}</p>
    </div>
  );
}
