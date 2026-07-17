import { useState, useEffect, useRef } from "react";
import { Check, RefreshCw, ImageOff, ChevronRight, AlertTriangle } from "lucide-react";
import { api, mediaUrl } from "../api";

interface Props {
  onApprove: (approvedIds: string[]) => void;
  onError?: (message: string) => void;
}

interface Frame {
  id: string;
  timestamp: number;
  duration?: number;
  prompt: string;
  image_path?: string;
  image_url?: string;
  approved: boolean;
}

export function StoryboardGrid({ onApprove, onError }: Props) {
  const [frames, setFrames] = useState<Frame[]>([]);
  const [approvedIds, setApprovedIds] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<"loading" | "images" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [imageSummary, setImageSummary] = useState<{ total: number; succeeded: number } | null>(
    null
  );
  const [plan, setPlan] = useState<{ song_duration?: number; scene_count?: number } | null>(null);
  const startedRef = useRef(false);

  const reportError = (message: string) => {
    setError(message);
    onError?.(message);
  };

  const loadStoryboard = async () => {
    setPhase("loading");
    setError(null);
    const res = await api.generateStoryboard();
    const frameList: Frame[] = res.frames || [];
    setFrames(frameList);
    if (res.plan) setPlan(res.plan);
    return frameList;
  };

  const generateImages = async () => {
    setPhase("images");
    setError(null);
    try {
      const res = await api.generateStoryboardImages();
      const updated: Frame[] = res.frames || [];
      setFrames(updated);
      const summary = res.summary;
      if (summary) {
        setImageSummary({ total: summary.total, succeeded: summary.succeeded });
        if (summary.succeeded === 0) {
          reportError(
            "ComfyUI did not return any storyboard images. Check Comfy Cloud API key and image backend in /api/health."
          );
          setPhase("error");
          return;
        }
        if (summary.failed > 0) {
          reportError(
            `${summary.failed} of ${summary.total} frames failed to render. You can retry or continue with the frames that succeeded.`
          );
        }
      }
      // Full-song default: approve every rendered scene
      const withImg = updated.filter((f) => f.image_url || f.image_path);
      setApprovedIds(new Set(withImg.map((f) => f.id)));
      setPhase("ready");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      reportError(`Image generation failed: ${msg}`);
      setPhase("error");
    }
  };

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    (async () => {
      try {
        await loadStoryboard();
        await generateImages();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        reportError(`Storyboard failed: ${msg}`);
        setPhase("error");
      }
    })();
  }, []);

  const toggleApproval = (id: string) => {
    setApprovedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const framesWithImages = frames.filter((f) => f.image_url || f.image_path);

  if (phase === "loading" || phase === "images") {
    return (
      <div className="max-w-2xl mx-auto text-center py-20 panel animate-rise">
        <div className="animate-spin w-14 h-14 border-2 border-cyan-400 border-t-transparent rounded-full mx-auto mb-6" />
        <h2 className="font-display text-xl mb-2">
          {phase === "loading" ? "Building Full-Song Storyboard" : "Rendering Scene Frames"}
        </h2>
        <p className="text-sm text-[var(--muted)]">
          {phase === "loading"
            ? "Planning scenes across the entire track…"
            : "Generating a still for every scene. Longer songs take more time."}
        </p>
        {plan?.scene_count ? (
          <p className="mt-4 text-xs font-mono text-cyan-300/80">
            {plan.scene_count} scenes
            {plan.song_duration ? ` · ${Math.round(plan.song_duration)}s song` : ""}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto animate-rise">
      <div className="flex items-center justify-between mb-6 flex-wrap gap-4">
        <div>
          <h2 className="font-display text-2xl tracking-wide">Storyboard</h2>
          <p className="text-sm text-[var(--muted)]">
            Full-song scene plan — review frames before LTX-2.3 image+audio animation
          </p>
          {plan?.song_duration ? (
            <p className="mt-1 text-xs font-mono text-cyan-300/80">
              {plan.scene_count || frames.length} scenes covering{" "}
              {Math.round(plan.song_duration)}s
            </p>
          ) : null}
        </div>
        {imageSummary && (
          <p className="text-xs text-cyan-300/80 font-mono">
            {imageSummary.succeeded}/{imageSummary.total} frames rendered
          </p>
        )}
      </div>

      {error && (
        <div className="mb-6 panel border-amber-400/40 px-4 py-3 text-sm text-amber-100 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 shrink-0 text-amber-400 mt-0.5" />
          <div className="flex-1">
            <p>{error}</p>
            {phase === "error" && (
              <button
                onClick={() => generateImages()}
                className="mt-3 inline-flex items-center gap-2 text-xs uppercase tracking-wider text-cyan-300 hover:text-cyan-200"
              >
                <RefreshCw className="w-3 h-3" />
                Retry image generation
              </button>
            )}
          </div>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {frames.map((frame) => (
          <div
            key={frame.id}
            className={`
              relative panel overflow-hidden border-2 transition-all
              ${approvedIds.has(frame.id)
                ? "border-cyan-400 shadow-lg shadow-cyan-500/20"
                : "border-cyan-500/20"
              }
            `}
          >
            <div className="aspect-video bg-navy-900 relative">
              {frame.image_url || frame.image_path ? (
                <img
                  src={mediaUrl(frame.image_url || frame.image_path!)}
                  alt={frame.prompt}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-full gap-2 text-[var(--muted)]">
                  <ImageOff className="w-10 h-10 opacity-50" />
                  <span className="text-xs">Not rendered</span>
                </div>
              )}

              <div className="absolute top-2 left-2 bg-black/70 px-2 py-1 text-xs font-mono">
                {frame.timestamp.toFixed(1)}s
              </div>

              <button
                onClick={() => toggleApproval(frame.id)}
                disabled={!frame.image_url && !frame.image_path}
                className={`
                  absolute top-2 right-2 w-8 h-8 flex items-center justify-center transition-all
                  ${approvedIds.has(frame.id)
                    ? "bg-cyan-400 text-navy-900"
                    : "bg-black/70 text-white hover:bg-cyan-400/40 disabled:opacity-40 disabled:cursor-not-allowed"
                  }
                `}
              >
                <Check className="w-4 h-4" />
              </button>
            </div>

            <div className="p-4 border-t border-cyan-500/10">
              <p className="text-xs text-[var(--muted)] line-clamp-3">{frame.prompt}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-8 flex items-center justify-between flex-wrap gap-4">
        <p className="text-sm text-[var(--muted)]">
          {approvedIds.size} of {framesWithImages.length} rendered frames approved
        </p>
        <button
          onClick={() => onApprove(Array.from(approvedIds))}
          disabled={approvedIds.size === 0}
          className={`
            flex items-center gap-2 py-3 px-6 font-display text-sm tracking-wider transition-all
            ${approvedIds.size > 0
              ? "bg-gradient-to-r from-cyan-500 to-blue-600 text-white shadow-lg shadow-cyan-500/25"
              : "bg-navy-700 text-[var(--muted)] cursor-not-allowed"
            }
          `}
        >
          Generate Video
          <ChevronRight className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}
