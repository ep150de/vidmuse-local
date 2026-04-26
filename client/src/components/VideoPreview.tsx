import { useState, useEffect } from "react";
import { Play, Check, ChevronRight, Film } from "lucide-react";
import { api } from "../api";

interface Props {
  onComplete: () => void;
}

interface Clip {
  frame_id: string;
  path: string;
}

export function VideoPreview({ onComplete }: Props) {
  const [clips, setClips] = useState<Clip[]>([]);
  const [generating, setGenerating] = useState(true);
  const [currentPreview, setCurrentPreview] = useState<string | null>(null);

  useEffect(() => {
    api.generateVideo().then((res) => {
      setClips(res.generated || []);
      setGenerating(false);
      if (res.generated?.[0]) {
        setCurrentPreview(res.generated[0].path);
      }
    });
  }, []);

  if (generating) {
    return (
      <div className="max-w-2xl mx-auto text-center py-20">
        <div className="animate-spin w-16 h-16 border-4 border-cyan-400 border-t-transparent rounded-full mx-auto mb-6" />
        <h2 className="text-2xl font-bold mb-2">Animating Your Storyboard</h2>
        <p className="text-gray-400">Generating video clips from each frame... This takes 3-5 minutes per clip.</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold mb-2">Video Clips</h2>
      <p className="text-gray-400 mb-6">Preview the generated clips before final assembly.</p>

      {/* Main preview */}
      {currentPreview && (
        <div className="rounded-xl overflow-hidden bg-black mb-6 aspect-video">
          <video
            src={`${import.meta.env.VITE_SERVER_URL}${currentPreview}`}
            controls
            className="w-full h-full"
            autoPlay
            loop
          />
        </div>
      )}

      {/* Clip thumbnails */}
      <div className="grid grid-cols-4 gap-3 mb-8">
        {clips.map((clip, i) => (
          <button
            key={i}
            onClick={() => setCurrentPreview(clip.path)}
            className={`
              aspect-video rounded-lg overflow-hidden bg-[#1a1f3d] border-2 transition-all relative
              ${currentPreview === clip.path ? "border-cyan-400" : "border-gray-700 hover:border-gray-500"}
            `}
          >
            <div className="absolute inset-0 flex items-center justify-center">
              <Play className="w-8 h-8 text-white/50" />
            </div>
            <span className="absolute bottom-1 left-2 text-xs bg-black/60 px-1 rounded">
              Clip {i + 1}
            </span>
          </button>
        ))}
      </div>

      <div className="text-center">
        <button
          onClick={onComplete}
          className="bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white font-bold py-4 px-8 rounded-xl inline-flex items-center gap-3 transition-all shadow-lg shadow-cyan-500/25"
        >
          <Film className="w-5 h-5" />
          Assemble Final Video
          <ChevronRight className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}
