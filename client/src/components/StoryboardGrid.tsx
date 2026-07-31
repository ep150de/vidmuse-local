import { useState, useEffect, useRef, useCallback } from "react";
import {
  Check,
  RefreshCw,
  ImageOff,
  ChevronRight,
  AlertTriangle,
  Mic2,
  Film,
  X,
  Loader2,
  Sparkles,
  ArrowLeft,
} from "lucide-react";
import { api, mediaUrl } from "../api";

interface Props {
  onApprove: (approvedIds: string[]) => void;
  onBackToConcepts: () => void;
  onError?: (message: string) => void;
}

type RenderStatus = "queued" | "rendering" | "done" | "failed" | "skipped";

interface Frame {
  id: string;
  timestamp: number;
  duration?: number;
  prompt: string;
  segment?: string;
  shot_type?: string;
  lyric_window?: string;
  image_path?: string;
  image_url?: string;
  render_status?: RenderStatus;
  approved: boolean;
}

type Phase =
  | "planning"
  | "rendering_preview"
  | "preview_review"
  | "rendering_full"
  | "ready"
  | "cancelled"
  | "error";

const DEFAULT_PREVIEW = 3;

function withQueuedStatus(frames: Frame[]): Frame[] {
  return frames.map((f) => ({
    ...f,
    render_status: f.image_url || f.image_path ? "done" : ("queued" as RenderStatus),
  }));
}

function patchFrame(list: Frame[], index: number, frame: Partial<Frame>): Frame[] {
  if (index < 0 || index >= list.length) return list;
  const next = [...list];
  next[index] = { ...next[index], ...frame };
  return next;
}

export function StoryboardGrid({ onApprove, onBackToConcepts, onError }: Props) {
  const [frames, setFrames] = useState<Frame[]>([]);
  const [approvedIds, setApprovedIds] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<Phase>("planning");
  const [error, setError] = useState<string | null>(null);
  const [statusLine, setStatusLine] = useState("Planning storyboard…");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [previewCount, setPreviewCount] = useState(DEFAULT_PREVIEW);
  const [previewIndices, setPreviewIndices] = useState<number[]>([]);
  const [renderStats, setRenderStats] = useState({ done: 0, failed: 0, total: 0 });
  const [plan, setPlan] = useState<{
    song_duration?: number;
    scene_count?: number;
    avatar_shots?: number;
    cinematic_shots?: number;
    performance_mix?: string;
    preview_count?: number;
    project_mode?: "mtv" | "typography";
    preview_indices?: number[];
  } | null>(null);
  const [identityUrl, setIdentityUrl] = useState<string | null>(null);

  const startedRef = useRef(false);
  const cancelRequestedRef = useRef(false);
  const renderRunRef = useRef(0);

  const reportError = (message: string) => {
    setError(message);
    onError?.(message);
  };

  const applyFrameUpdate = useCallback((index: number, frame: Frame) => {
    setFrames((prev) => {
      const next = patchFrame(prev, index, frame);
      setRenderStats({
        total: next.length,
        done: next.filter((f) => f.image_url || f.image_path).length,
        failed: next.filter((f) => f.render_status === "failed").length,
      });
      return next;
    });
  }, []);

  useEffect(() => {
    const onFrame = (data: { index: number; frame: Frame; total: number }) => {
      if (data.frame) {
        applyFrameUpdate(data.index, data.frame);
        setActiveIndex(null);
      }
    };
    const onProgress = (data: { status?: string; phase?: string }) => {
      if (data.phase === "storyboard" && data.status) {
        setStatusLine(data.status);
        const match = data.status.match(/Rendering frame (\d+)\//);
        if (match) setActiveIndex(parseInt(match[1], 10) - 1);
      }
    };
    const offFrame = api.onStoryboardFrameUpdate(onFrame);
    const offProgress = api.onProgressUpdate(onProgress);
    return () => {
      offFrame();
      offProgress();
    };
  }, [applyFrameUpdate]);

  const renderRange = async (
    start: number,
    end: number,
    runId: number,
    indices?: number[]
  ) => {
    const selected = indices ? new Set(indices) : null;
    setFrames((prev) =>
      prev.map((f, i) =>
        (selected ? selected.has(i) : i >= start && i < end) &&
        f.render_status !== "done"
          ? { ...f, render_status: "queued" as RenderStatus }
          : f
      )
    );

    const res = await api.renderStoryboardRange(start, end, indices);
    if (runId !== renderRunRef.current) return res;

    const updated: Frame[] = res.frames || [];
    setFrames(withQueuedStatus(updated));
    if (res.identity_sheet_url) setIdentityUrl(res.identity_sheet_url);

    const succeeded = (res.generated || []).length;
    const rangeTotal = indices?.length ?? end - start;
    setRenderStats((s) => ({
      total: updated.length,
      done: updated.filter((f) => f.image_url || f.image_path).length,
      failed: updated.filter((f) => f.render_status === "failed").length,
    }));

    if (res.cancelled || cancelRequestedRef.current) {
      setPhase("cancelled");
      setStatusLine("Render cancelled — you can go back to concepts or retry preview.");
      return res;
    }

    if (succeeded === 0 && rangeTotal > 0) {
      reportError(
        "No preview frames rendered. Check Comfy Cloud API key and image backend in /api/health."
      );
      setPhase("error");
      return res;
    }

    if (res.summary?.failed > 0) {
      reportError(
        `${res.summary.failed} frame(s) failed in this batch. You can continue or change direction.`
      );
    }

    return res;
  };

  const startPreviewRender = async () => {
    cancelRequestedRef.current = false;
    const runId = ++renderRunRef.current;
    const indices =
      previewIndices.length > 0
        ? previewIndices
        : Array.from(
            { length: Math.min(previewCount, frames.length || previewCount) },
            (_, i) => i
          );
    const count = indices.length;
    setPhase("rendering_preview");
    setError(null);
    setStatusLine(`Rendering preview — frame 1 of ${count}…`);
    setActiveIndex(0);

    const res = await renderRange(0, frames.length, runId, indices);
    if (runId !== renderRunRef.current || cancelRequestedRef.current) return;

    const updated: Frame[] = res?.frames || [];
    const withImg = indices
      .map((i) => updated[i])
      .filter((f): f is Frame => Boolean(f && (f.image_url || f.image_path)));
    setApprovedIds(new Set(withImg.map((f) => f.id)));
    setPhase("preview_review");
    setStatusLine(
      `Preview complete — ${withImg.length} of ${count} opening scenes rendered. Does this direction look right?`
    );
  };

  const continueFullRender = async () => {
    if (frames.length <= previewIndices.length) {
      setPhase("ready");
      return;
    }
    cancelRequestedRef.current = false;
    const runId = ++renderRunRef.current;
    setPhase("rendering_full");
    const previewSet = new Set(previewIndices);
    const remaining = frames.map((_, i) => i).filter((i) => !previewSet.has(i));
    setStatusLine(`Rendering remaining ${remaining.length} scenes…`);

    await renderRange(0, frames.length, runId, remaining);
    if (runId !== renderRunRef.current || cancelRequestedRef.current) return;

    setFrames((current) => {
      const withImg = current.filter((f) => f.image_url || f.image_path);
      setApprovedIds(new Set(withImg.map((f) => f.id)));
      return current;
    });
    setPhase("ready");
    setStatusLine("Full storyboard rendered — review and approve scenes for video generation.");
  };

  const handleCancelRender = async () => {
    cancelRequestedRef.current = true;
    renderRunRef.current += 1;
    setStatusLine("Cancelling render…");
    try {
      await api.cancelStoryboardRender();
    } catch {
      /* best effort */
    }
    setActiveIndex(null);
    setPhase("cancelled");
    setStatusLine("Render cancelled. Go back to concepts or retry the preview.");
  };

  const handleBackToConcepts = async () => {
    cancelRequestedRef.current = true;
    renderRunRef.current += 1;
    try {
      await api.cancelStoryboardRender();
    } catch {
      /* best effort */
    }
    onBackToConcepts();
  };

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    (async () => {
      try {
        setPhase("planning");
        setStatusLine("Locking performer identity and planning scenes…");
        const res = await api.generateStoryboard();
        const frameList = withQueuedStatus(res.frames || []);
        setFrames(frameList);
        if (res.plan) {
          setPlan(res.plan);
          if (res.plan.preview_count) setPreviewCount(res.plan.preview_count);
          if (res.plan.preview_indices) setPreviewIndices(res.plan.preview_indices);
        }
        if (res.identity_sheet_url) setIdentityUrl(res.identity_sheet_url);
        setRenderStats({ done: 0, failed: 0, total: frameList.length });
        const previewN = res.plan?.preview_count || DEFAULT_PREVIEW;
        const tracerIndices: number[] =
          res.plan?.preview_indices ||
          Array.from({ length: Math.min(previewN, frameList.length) }, (_, i) => i);
        setPreviewCount(previewN);
        setPreviewIndices(tracerIndices);
        setStatusLine(
          `Storyboard planned — ${frameList.length} scenes. Starting ${tracerIndices.length}-scene tracer preview…`
        );
        cancelRequestedRef.current = false;
        const runId = ++renderRunRef.current;
        const count = tracerIndices.length;
        setPhase("rendering_preview");
        setActiveIndex(tracerIndices[0] ?? 0);
        const previewRes = await renderRange(0, frameList.length, runId, tracerIndices);
        if (runId !== renderRunRef.current || cancelRequestedRef.current) return;
        const previewFrames: Frame[] = previewRes?.frames || [];
        const withImg = tracerIndices
          .map((i) => previewFrames[i])
          .filter((f: Frame | undefined): f is Frame =>
            Boolean(f && (f.image_url || f.image_path))
          );
        setApprovedIds(new Set(withImg.map((f: Frame) => f.id)));
        setPhase("preview_review");
        setStatusLine(
          `Preview complete — ${withImg.length} of ${count} tracer scenes rendered. Does this direction look right?`
        );
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        reportError(`Storyboard failed: ${msg}`);
        setPhase("error");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
  const isRendering = phase === "rendering_preview" || phase === "rendering_full";
  const showPreviewGate = phase === "preview_review";
  const showGrid =
    phase !== "planning" || frames.length > 0;

  const frameCardStatus = (frame: Frame, index: number): RenderStatus => {
    if (frame.image_url || frame.image_path) return "done";
    if (activeIndex === index && isRendering) return "rendering";
    return frame.render_status || "queued";
  };

  if (phase === "planning" && frames.length === 0) {
    return (
      <div className="max-w-2xl mx-auto text-center py-20 panel animate-rise">
        <div className="animate-spin w-14 h-14 border-2 border-cyan-400 border-t-transparent rounded-full mx-auto mb-6" />
        <h2 className="font-display text-xl mb-2">Planning storyboard</h2>
        <p className="text-sm text-[var(--muted)]">{statusLine}</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto animate-rise">
      {/* Live status bar */}
      <div className="mb-6 panel border-cyan-500/30 px-4 py-3">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-start gap-3 min-w-0">
            {isRendering ? (
              <Loader2 className="w-5 h-5 text-cyan-400 animate-spin shrink-0 mt-0.5" />
            ) : showPreviewGate ? (
              <Sparkles className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
            ) : (
              <Film className="w-5 h-5 text-cyan-400 shrink-0 mt-0.5" />
            )}
            <div>
              <p className="text-sm font-medium text-[var(--ink)]">{statusLine}</p>
              {plan?.scene_count ? (
                <p className="mt-1 text-xs font-mono text-cyan-300/70">
                  {renderStats.done}/{renderStats.total} rendered
                  {renderStats.failed > 0 ? ` · ${renderStats.failed} failed` : ""}
                  {plan.song_duration ? ` · ${Math.round(plan.song_duration)}s song` : ""}
                  {plan.avatar_shots != null
                    ? ` · ${plan.avatar_shots} avatar / ${plan.cinematic_shots || 0} cinematic`
                    : ""}
                </p>
              ) : null}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {identityUrl && (
              <div className="flex items-center gap-2 mr-2">
                <p className="text-xs text-[var(--text-secondary)] hidden sm:block">Identity</p>
                <div className="w-10 h-10 rounded-md overflow-hidden border border-sky-400/40">
                  <img
                    src={mediaUrl(identityUrl)}
                    alt="Performer identity"
                    className="w-full h-full object-cover"
                  />
                </div>
              </div>
            )}
            {isRendering && (
              <button
                type="button"
                onClick={handleCancelRender}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs uppercase tracking-wider border border-amber-400/50 text-amber-200 hover:bg-amber-400/10 transition-colors"
              >
                <X className="w-3.5 h-3.5" />
                Cancel
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Preview gate */}
      {showPreviewGate && (
        <div className="mb-6 panel border-amber-400/40 bg-amber-950/20 px-5 py-4">
          <h3 className="font-display text-lg text-amber-100 mb-1">Preview check-in</h3>
          <p className="text-sm text-[var(--text-secondary)] mb-4">
            We rendered the first {Math.min(previewCount, frames.length)} scenes so you can catch
            drift early — before spending tokens on the full song. If identity, style, or lyric
            imagery looks wrong, go back and pick a different concept or adjust direction.
          </p>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={continueFullRender}
              className="inline-flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-cyan-500 to-blue-600 text-white text-sm font-display tracking-wider shadow-lg shadow-cyan-500/20"
            >
              <ChevronRight className="w-4 h-4" />
              Looks good — render full storyboard
            </button>
            <button
              type="button"
              onClick={handleBackToConcepts}
              className="inline-flex items-center gap-2 px-4 py-2.5 border border-[var(--border)] text-sm hover:border-cyan-400/50 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Change direction
            </button>
            <button
              type="button"
              onClick={startPreviewRender}
              className="inline-flex items-center gap-2 px-4 py-2.5 text-sm text-cyan-300 hover:text-cyan-200"
            >
              <RefreshCw className="w-4 h-4" />
              Re-render preview
            </button>
          </div>
        </div>
      )}

      {phase === "cancelled" && (
        <div className="mb-6 panel border-[var(--border)] px-4 py-3 flex flex-wrap gap-3 items-center justify-between">
          <p className="text-sm text-[var(--muted)]">Render stopped. No further frames will generate.</p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={startPreviewRender}
              className="text-xs uppercase tracking-wider text-cyan-300 hover:text-cyan-200 inline-flex items-center gap-1"
            >
              <RefreshCw className="w-3 h-3" /> Retry preview
            </button>
            <button
              type="button"
              onClick={handleBackToConcepts}
              className="text-xs uppercase tracking-wider text-[var(--muted)] hover:text-[var(--ink)] inline-flex items-center gap-1"
            >
              <ArrowLeft className="w-3 h-3" /> Back to concepts
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="mb-6 panel border-amber-400/40 px-4 py-3 text-sm text-amber-100 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 shrink-0 text-amber-400 mt-0.5" />
          <div className="flex-1">
            <p>{error}</p>
            {phase === "error" && (
              <button
                type="button"
                onClick={startPreviewRender}
                className="mt-3 inline-flex items-center gap-2 text-xs uppercase tracking-wider text-cyan-300 hover:text-cyan-200"
              >
                <RefreshCw className="w-3 h-3" />
                Retry preview
              </button>
            )}
          </div>
        </div>
      )}

      {showGrid && (
        <>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="font-display text-2xl tracking-tight text-[var(--ink)]">Storyboard</h2>
              <p className="text-sm text-[var(--text-secondary)]">
                {plan?.project_mode === "typography"
                  ? "Images appear live; exact lyric typography is composited during final assembly"
                  : "Images appear live as each scene finishes — Avatar shots lip-sync; cinematic shots are B-roll"}
              </p>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {frames.map((frame, index) => {
              const isAvatar = (frame.shot_type || "") === "avatar";
              const isTypography = plan?.project_mode === "typography";
              const typeLabel = isTypography
                ? frame.shot_type === "lyric"
                  ? "Lyric"
                  : frame.shot_type === "transition"
                    ? "Transition"
                    : "Instrumental"
                : isAvatar
                  ? "Avatar"
                  : "Cinematic";
              const cardStatus = frameCardStatus(frame, index);
              const isPreviewFrame =
                previewIndices.length > 0
                  ? previewIndices.includes(index)
                  : index < previewCount;
              const dimmed =
                (phase === "rendering_full" || phase === "ready") &&
                !isPreviewFrame &&
                cardStatus === "queued";

              return (
                <div
                  key={frame.id}
                  className={`
                    relative panel overflow-hidden border-2 transition-all duration-300
                    ${approvedIds.has(frame.id)
                      ? "border-cyan-400 shadow-lg shadow-cyan-500/20"
                      : "border-cyan-500/20"
                    }
                    ${dimmed ? "opacity-60" : ""}
                    ${cardStatus === "rendering" ? "ring-2 ring-cyan-400/60 ring-offset-2 ring-offset-[var(--bg)]" : ""}
                  `}
                >
                  <div className="aspect-video bg-navy-900 relative">
                    {frame.image_url || frame.image_path ? (
                      <img
                        src={mediaUrl(frame.image_url || frame.image_path!)}
                        alt={frame.prompt}
                        className="w-full h-full object-cover animate-rise"
                      />
                    ) : cardStatus === "rendering" ? (
                      <div className="flex flex-col items-center justify-center h-full gap-3 text-cyan-300/90">
                        <Loader2 className="w-10 h-10 animate-spin" />
                        <span className="text-xs font-mono uppercase tracking-wider">
                          Rendering…
                        </span>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center h-full gap-2 text-[var(--muted)]">
                        {cardStatus === "failed" ? (
                          <>
                            <ImageOff className="w-10 h-10 opacity-50 text-amber-400/80" />
                            <span className="text-xs text-amber-200/80">Render failed</span>
                          </>
                        ) : (
                          <>
                            <div className="w-10 h-10 rounded border border-dashed border-cyan-500/30 flex items-center justify-center">
                              <span className="text-xs font-mono text-cyan-500/50">{index + 1}</span>
                            </div>
                            <span className="text-xs">
                              {isPreviewFrame && phase === "rendering_preview"
                                ? "Queued"
                                : !isPreviewFrame &&
                                    phase !== "rendering_full" &&
                                    phase !== "ready"
                                  ? "Waiting for approval"
                                  : "Queued"}
                            </span>
                          </>
                        )}
                      </div>
                    )}

                    <div className="absolute top-2 left-2 flex gap-1.5 flex-wrap max-w-[70%]">
                      <span className="bg-black/70 px-2 py-1 text-xs font-mono">
                        {frame.timestamp.toFixed(1)}s
                      </span>
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-1 text-xs font-semibold rounded-sm ${
                          isAvatar
                            ? "bg-emerald-500/90 text-navy-950"
                            : "bg-sky-500/90 text-navy-950"
                        }`}
                      >
                        {isAvatar ? <Mic2 className="w-3 h-3" /> : <Film className="w-3 h-3" />}
                        {typeLabel}
                      </span>
                      {isPreviewFrame && phase !== "ready" && (
                        <span className="bg-amber-500/90 text-navy-950 px-2 py-1 text-xs font-semibold rounded-sm">
                          Preview
                        </span>
                      )}
                    </div>

                    {(phase === "ready" || showPreviewGate) && (frame.image_url || frame.image_path) && (
                      <button
                        type="button"
                        onClick={() => toggleApproval(frame.id)}
                        className={`
                          absolute top-2 right-2 w-8 h-8 flex items-center justify-center transition-all
                          ${approvedIds.has(frame.id)
                            ? "bg-cyan-400 text-navy-900"
                            : "bg-black/70 text-white hover:bg-cyan-400/40"
                          }
                        `}
                      >
                        <Check className="w-4 h-4" />
                      </button>
                    )}
                  </div>

                  <div className="p-4 border-t border-[var(--border)]">
                    {frame.segment && (
                      <p className="text-xs font-mono text-cyan-400/80 mb-1 uppercase tracking-wider">
                        {frame.segment}
                      </p>
                    )}
                    <p className="text-sm text-[var(--ink)]/90 line-clamp-3 leading-relaxed">
                      {frame.prompt}
                    </p>
                    {frame.lyric_window ? (
                      <p className="mt-2 text-xs text-sky-100/80 line-clamp-2">
                        Lyrics: {frame.lyric_window}
                      </p>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>

          {phase === "ready" && (
            <div className="mt-8 flex items-center justify-between flex-wrap gap-4">
              <p className="text-sm text-[var(--muted)]">
                {approvedIds.size} of {framesWithImages.length} rendered frames approved
              </p>
              <button
                type="button"
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
          )}
        </>
      )}
    </div>
  );
}
