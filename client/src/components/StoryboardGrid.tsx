import { useState, useEffect } from "react";
import { Check, RefreshCw, ImageOff, ChevronRight } from "lucide-react";
import { api } from "../api";

interface Props {
  onApprove: (approvedIds: string[]) => void;
}

interface Frame {
  id: string;
  timestamp: number;
  prompt: string;
  image_path?: string;
  approved: boolean;
}

export function StoryboardGrid({ onApprove }: Props) {
  const [frames, setFrames] = useState<Frame[]>([]);
  const [approvedIds, setApprovedIds] = useState<Set<string>>(new Set());
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    // Load storyboard from server
    api.generateStoryboard().then((res) => {
      setFrames(res.frames || []);
      generateImages(res.frames || []);
    });
  }, []);

  const generateImages = async (frameList: Frame[]) => {
    setGenerating(true);
    try {
      const res = await api.generateStoryboardImages();
      // Update frames with generated image paths
      const updated = frameList.map((f) => {
        const generated = res.generated?.find((g: any) => g.frame_id === f.id);
        return { ...f, image_path: generated?.path };
      });
      setFrames(updated);
    } finally {
      setGenerating(false);
    }
  };

  const toggleApproval = (id: string) => {
    setApprovedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold">Storyboard</h2>
          <p className="text-gray-400">Review and approve frames for your video</p>
        </div>
        {generating && (
          <div className="flex items-center gap-2 text-cyan-400">
            <RefreshCw className="w-4 h-4 animate-spin" />
            <span className="text-sm">Generating images...</span>
          </div>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {frames.map((frame) => (
          <div
            key={frame.id}
            className={`
              relative rounded-xl overflow-hidden border-2 transition-all
              ${approvedIds.has(frame.id)
                ? "border-cyan-400 shadow-lg shadow-cyan-500/20"
                : "border-gray-700"
              }
            `}
          >
            {/* Image */}
            <div className="aspect-video bg-[#0b0f2d] relative">
              {frame.image_path ? (
                <img
                  src={`${import.meta.env.VITE_SERVER_URL}${frame.image_path}`}
                  alt={frame.prompt}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="flex items-center justify-center h-full">
                  <ImageOff className="w-12 h-12 text-gray-600" />
                </div>
              )}
              
              {/* Timestamp badge */}
              <div className="absolute top-2 left-2 bg-black/60 px-2 py-1 rounded text-xs">
                {frame.timestamp.toFixed(1)}s
              </div>

              {/* Approve button */}
              <button
                onClick={() => toggleApproval(frame.id)}
                className={`
                  absolute top-2 right-2 w-8 h-8 rounded-full flex items-center justify-center transition-all
                  ${approvedIds.has(frame.id)
                    ? "bg-cyan-400 text-black"
                    : "bg-black/60 text-white hover:bg-cyan-400/50"
                  }
                `}
              >
                <Check className="w-4 h-4" />
              </button>
            </div>

            {/* Prompt */}
            <div className="p-4 bg-[#1a1f3d]">
              <p className="text-xs text-gray-400 line-clamp-3">{frame.prompt}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-8 flex items-center justify-between">
        <p className="text-sm text-gray-400">
          {approvedIds.size} of {frames.length} frames approved
        </p>
        <button
          onClick={() => onApprove(Array.from(approvedIds))}
          disabled={approvedIds.size === 0}
          className={`
            flex items-center gap-2 py-3 px-6 rounded-xl font-bold transition-all
            ${approvedIds.size > 0
              ? "bg-gradient-to-r from-cyan-500 to-blue-600 text-white shadow-lg shadow-cyan-500/25"
              : "bg-gray-700 text-gray-500 cursor-not-allowed"
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
