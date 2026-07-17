interface Props {
  progress: { phase: string; progress: number; status: string };
}

export function ProgressPanel({ progress }: Props) {
  if (!progress.status) return null;

  return (
    <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 md:w-80 panel panel-shine p-4 z-40 animate-rise shadow-[0_16px_48px_rgba(0,0,0,0.45)]">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-display tracking-widest text-cyan-400 uppercase">
          {progress.phase || "progress"}
        </span>
        <span className="text-xs font-mono text-[var(--muted)]">{progress.progress}%</span>
      </div>
      <div className="w-full h-1.5 bg-navy-900 overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-cyan-400 via-cyan-300 to-gold-500 transition-all duration-500"
          style={{ width: `${Math.max(0, Math.min(100, progress.progress))}%` }}
        />
      </div>
      <p className="text-xs text-[var(--muted)] mt-2 truncate">{progress.status}</p>
    </div>
  );
}
