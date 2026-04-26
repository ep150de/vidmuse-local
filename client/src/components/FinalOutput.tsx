import { Download, Play, RotateCcw, Share2 } from "lucide-react";

interface Props {
  downloadUrl: string;
}

export function FinalOutput({ downloadUrl }: Props) {
  return (
    <div className="max-w-3xl mx-auto text-center">
      <div className="mb-8">
        <div className="w-20 h-20 bg-green-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
          <Play className="w-10 h-10 text-green-400" />
        </div>
        <h2 className="text-3xl font-bold mb-2">Your Video is Ready!</h2>
        <p className="text-gray-400">Synced to your music beats and ready to share.</p>
      </div>

      {/* Video player */}
      <div className="rounded-xl overflow-hidden bg-black aspect-video mb-8 shadow-2xl shadow-cyan-500/10">
        <video
          src={downloadUrl}
          controls
          className="w-full h-full"
          poster="/placeholder-video.jpg"
        />
      </div>

      {/* Actions */}
      <div className="flex flex-wrap justify-center gap-4">
        <a
          href={downloadUrl}
          download
          className="bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white font-bold py-4 px-8 rounded-xl inline-flex items-center gap-3 transition-all shadow-lg shadow-cyan-500/25"
        >
          <Download className="w-5 h-5" />
          Download MP4
        </a>

        <button
          onClick={() => window.location.reload()}
          className="bg-[#1a1f3d] hover:bg-[#252b4d] text-white font-bold py-4 px-8 rounded-xl inline-flex items-center gap-3 transition-all border border-gray-600"
        >
          <RotateCcw className="w-5 h-5" />
          Create Another
        </button>
      </div>

      <p className="text-sm text-gray-500 mt-8">
        Video saved locally. No cloud upload — complete privacy.
      </p>
    </div>
  );
}
