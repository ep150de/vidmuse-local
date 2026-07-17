import { useState, useEffect, useRef } from "react";
import { Play, ChevronRight, Film, AlertTriangle, RefreshCw } from "lucide-react";
import { api, mediaUrl } from "../api";

interface Props {
  onComplete: () => void;
  onError?: (message: string) => void;
}

interface Clip {
  frame_id?: string;
  path: string;
  url?: string;
}

export function VideoPreview({ onComplete, onError }: Props) {
  const [clips, setClips] = useState<Clip[]>([]);
  const [generating, setGenerating] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentPreview, setCurrentPreview] = useState<string | null>(null);
  const startedRef = useRef(false);

  const loadClips = async () => {
    setGenerating(true);
    setError(null);
    try {
      const res = await api.generateVideo();
      const list: Clip[] = res.clips || res.generated || [];
      setClips(list);
      if (list.length === 0) {
        const msg =
          "No video clips were generated. Storyboard images may be missing or ComfyUI i2v failed.";
        setError(msg);
        onError?.(msg);
      } else if (res.summary?.failed > 0) {
        const msg = `${res.summary.failed} clip(s) failed to animate.`;
        setError(msg);
        onError?.(msg);
      }
      const firstUrl = list[0]?.url || list[0]?.path;
      if (firstUrl) setCurrentPreview(firstUrl);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      onError?.(msg);
    } finally {
      setGenerating(false);
    }
  };

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    loadClips();
  }, []);

  if (generating) {
    return (
      <div className="max-w-2xl mx-auto text-center py-20 panel animate-rise">
        <div className="animate-spin w-16 h-16 border-2 border-cyan-400 border-t-transparent rounded-full mx-auto mb-6" />
        <h2 className="font-display text-2xl mb-2">Animating Full Song</h2>
        <p className="text-sm text-[var(--muted)]">
          Running LTX-2.3 image+audio motion for each scene (HuMo fallback if needed).
          The editor then loops/fills clips to cover the entire track.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto animate-rise">
      <h2 className="font-display text-2xl tracking-wide mb-2">Video Clips</h2>
      <p className="text-sm text-[var(--muted)] mb-6">
        Preview the generated clips before final assembly.
      </p>

      {error && (
        <div className="mb-6 panel border-amber-400/40 px-4 py-3 text-sm text-amber-100 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 shrink-0 text-amber-400 mt-0.5" />
          <div className="flex-1">
            <p>{error}</p>
            {clips.length === 0 && (
              <button
                onClick={loadClips}
                className="mt-3 inline-flex items-center gap-2 text-xs uppercase tracking-wider text-cyan-300 hover:text-cyan-200"
              >
                <RefreshCw className="w-3 h-3" />
                Retry
              </button>
            )}
          </div>
        </div>
      )}

      {currentPreview && (
        <div className="panel overflow-hidden bg-black mb-6 aspect-video">
          <video
            src={mediaUrl(currentPreview)}
            controls
            className="w-full h-full"
            autoPlay
            loop
          />
        </div>
      )}

      {clips.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
          {clips.map((clip, i) => {
            const clipUrl = clip.url || clip.path;
            return (
              <button
                key={clip.frame_id || i}
                onClick={() => setCurrentPreview(clipUrl)}
                className={`
                  aspect-video panel overflow-hidden border-2 transition-all relative
                  ${currentPreview === clipUrl ? "border-cyan-400" : "border-cyan-500/20 hover:border-cyan-400/40"}
                `}
              >
                <div className="absolute inset-0 flex items-center justify-center">
                  <Play className="w-8 h-8 text-white/50" />
                </div>
                <span className="absolute bottom-1 left-2 text-xs bg-black/70 px-1 font-mono">
                  Clip {i + 1}
                </span>
              </button>
            );
          })}
        </div>
      )}

      <div className="text-center">
        <button
          onClick={onComplete}
          disabled={clips.length === 0}
          className={`
            font-display text-sm tracking-wider py-4 px-8 inline-flex items-center gap-3 transition-all
            ${clips.length > 0
              ? "bg-gradient-to-r from-cyan-500 to-blue-600 text-white shadow-lg shadow-cyan-500/25"
              : "bg-navy-700 text-[var(--muted)] cursor-not-allowed"
            }
          `}
        >
          <Film className="w-5 h-5" />
          Assemble Final Video
          <ChevronRight className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}
