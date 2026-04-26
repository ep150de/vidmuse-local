import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { Upload, Image, Music, Wand2 } from "lucide-react";

interface Props {
  onComplete: (audio: File, images: File[], prompt: string) => void;
}

export function UploadScreen({ onComplete }: Props) {
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [prompt, setPrompt] = useState("");
  const [isDragging, setIsDragging] = useState(false);

  const onAudioDrop = useCallback((files: File[]) => {
    if (files[0]) setAudioFile(files[0]);
  }, []);

  const onImageDrop = useCallback((files: File[]) => {
    setImageFiles((prev) => [...prev, ...files].slice(0, 8));
  }, []);

  const audioDropzone = useDropzone({
    onDrop: onAudioDrop,
    accept: { "audio/*": [".mp3", ".wav", ".m4a", ".ogg"] },
    multiple: false,
    onDragEnter: () => setIsDragging(true),
    onDragLeave: () => setIsDragging(false),
  });

  const imageDropzone = useDropzone({
    onDrop: onImageDrop,
    accept: { "image/*": [".png", ".jpg", ".jpeg", ".webp"] },
    multiple: true,
    onDragEnter: () => setIsDragging(true),
    onDragLeave: () => setIsDragging(false),
  });

  const canSubmit = audioFile && prompt.trim().length > 0;

  return (
    <div className="max-w-2xl mx-auto">
      <div className="text-center mb-10">
        <h1 className="text-4xl font-bold bg-gradient-to-r from-cyan-400 to-gold-400 bg-clip-text text-transparent mb-4">
          VidMuse Local
        </h1>
        <p className="text-gray-400 text-lg">
          Turn your Suno songs into stunning AI music videos
        </p>
      </div>

      {/* Audio Upload */}
      <div
        {...audioDropzone.getRootProps()}
        className={`
          border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all mb-6
          ${isDragging ? "border-cyan-400 bg-cyan-400/10" : "border-gray-600 hover:border-gray-500"}
          ${audioFile ? "bg-green-500/10 border-green-500" : ""}
        `}
      >
        <input {...audioDropzone.getInputProps()} />
        <Music className="w-12 h-12 mx-auto mb-4 text-cyan-400" />
        {audioFile ? (
          <div>
            <p className="text-green-400 font-medium">{audioFile.name}</p>
            <p className="text-sm text-gray-400">{(audioFile.size / 1024 / 1024).toFixed(1)} MB</p>
          </div>
        ) : (
          <div>
            <p className="text-lg font-medium mb-2">Drop your song here</p>
            <p className="text-sm text-gray-400">MP3, WAV, M4A (max 20MB)</p>
          </div>
        )}
      </div>

      {/* Reference Images */}
      <div
        {...imageDropzone.getRootProps()}
        className={`
          border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all mb-6
          ${isDragging ? "border-gold-400 bg-gold-400/10" : "border-gray-600 hover:border-gray-500"}
        `}
      >
        <input {...imageDropzone.getInputProps()} />
        <Image className="w-8 h-8 mx-auto mb-3 text-gold-400" />
        <p className="text-sm font-medium mb-1">Add reference images (optional)</p>
        <p className="text-xs text-gray-400">Characters, art style, scenes — up to 8 images</p>
        
        {imageFiles.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-4 justify-center">
            {imageFiles.map((file, i) => (
              <div key={i} className="w-16 h-16 rounded-lg overflow-hidden bg-gray-800">
                <img
                  src={URL.createObjectURL(file)}
                  alt=""
                  className="w-full h-full object-cover"
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Concept Prompt */}
      <div className="mb-8">
        <label className="block text-sm font-medium mb-2 text-gray-300">
          Describe your video vision
        </label>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g., 80s synthwave lunar mass driver, neon cyan and gold, robot swarm building on the Moon, dark navy space background"
          className="w-full h-32 bg-[#1a1f3d] border border-gray-600 rounded-xl p-4 text-white placeholder-gray-500 focus:border-cyan-400 focus:outline-none resize-none"
        />
        <p className="text-xs text-gray-500 mt-2">
          The AI agent will chat with you to refine this concept
        </p>
      </div>

      {/* Submit */}
      <button
        onClick={() => audioFile && onComplete(audioFile, imageFiles, prompt)}
        disabled={!canSubmit}
        className={`
          w-full py-4 rounded-xl font-bold text-lg flex items-center justify-center gap-3 transition-all
          ${canSubmit 
            ? "bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white shadow-lg shadow-cyan-500/25" 
            : "bg-gray-700 text-gray-500 cursor-not-allowed"
          }
        `}
      >
        <Wand2 className="w-6 h-6" />
        Start Creating
      </button>
    </div>
  );
}
