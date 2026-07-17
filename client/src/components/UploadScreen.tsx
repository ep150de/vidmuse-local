import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { Image, Music, Wand2, Sparkles, UserRound } from "lucide-react";

interface Props {
  onComplete: (audio: File, images: File[], prompt: string) => void;
}

export function UploadScreen({ onComplete }: Props) {
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [prompt, setPrompt] = useState("");

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
  });

  const imageDropzone = useDropzone({
    onDrop: onImageDrop,
    accept: { "image/*": [".png", ".jpg", ".jpeg", ".webp"] },
    multiple: true,
  });

  const canSubmit = Boolean(audioFile && prompt.trim().length > 0);

  return (
    <section className="relative min-h-[78vh] flex flex-col justify-center animate-rise">
      <div className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[42vh] overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_0%,rgba(6,182,212,0.28),transparent_58%)]" />
        <div className="absolute -top-24 left-1/2 h-64 w-[70%] -translate-x-1/2 bg-[radial-gradient(ellipse,rgba(245,158,11,0.12),transparent_70%)] blur-2xl" />
        <div className="scanline absolute inset-x-0 top-[38%] h-px opacity-60" />
      </div>

      <div className="text-center mb-10">
        <p className="font-display text-[11px] uppercase tracking-[0.38em] text-cyan-400/90 mb-4 animate-rise">
          Local director · cloud render
        </p>
        <h1 className="font-display text-5xl md:text-7xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-cyan-200 via-cyan-400 to-gold-400 mb-4 tracking-wide">
          VidMuse
        </h1>
        <p className="text-base md:text-lg text-[var(--muted)] max-w-xl mx-auto leading-relaxed">
          Full-song music videos from a track, a vibe, and optional character stills —
          with LTX-2.3 image+audio motion for stronger fidelity.
        </p>
      </div>

      <div className="max-w-2xl mx-auto w-full space-y-4">
        <div
          {...audioDropzone.getRootProps()}
          className={`
            panel panel-shine rounded-sm p-8 text-center cursor-pointer transition-all duration-200
            ${audioFile ? "border-cyan-400/70 shadow-[0_0_0_1px_rgba(34,211,238,0.15)]" : "hover:border-cyan-400/45"}
          `}
        >
          <input {...audioDropzone.getInputProps()} />
          <Music className="w-11 h-11 mx-auto mb-3 text-cyan-400" />
          {audioFile ? (
            <div>
              <p className="font-display text-sm text-cyan-300">{audioFile.name}</p>
              <p className="text-xs text-[var(--muted)] mt-1">
                {(audioFile.size / 1024 / 1024).toFixed(1)} MB · ready
              </p>
            </div>
          ) : (
            <div>
              <p className="font-medium text-lg mb-1">1. Drop your song</p>
              <p className="text-sm text-[var(--muted)]">MP3, WAV, M4A</p>
            </div>
          )}
        </div>

        <div
          {...imageDropzone.getRootProps()}
          className="panel panel-shine rounded-sm p-6 text-center cursor-pointer hover:border-gold-500/45 transition-all duration-200"
        >
          <input {...imageDropzone.getInputProps()} />
          <div className="flex items-center justify-center gap-2 mb-2 text-gold-500">
            <UserRound className="w-6 h-6" />
            <Image className="w-5 h-5 opacity-70" />
          </div>
          <p className="text-sm font-medium mb-1">2. Character references</p>
          <p className="text-xs text-[var(--muted)] max-w-md mx-auto">
            Face / outfit stills lock identity across scenes — strongly recommended for consistency
          </p>
          {imageFiles.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-4 justify-center">
              {imageFiles.map((file, i) => (
                <div
                  key={`${file.name}-${i}`}
                  className="w-14 h-14 overflow-hidden border border-cyan-500/35 ring-1 ring-cyan-400/10"
                >
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

        <div className="panel panel-shine rounded-sm p-5">
          <label className="flex items-center gap-2 text-sm font-medium mb-3 text-cyan-300">
            <Sparkles className="w-4 h-4" />
            3. Your vision
          </label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Neon alley performance, chrome jacket, same singer in every shot…"
            className="w-full h-28 bg-navy-900/70 border border-cyan-500/20 p-4 text-white placeholder:text-[var(--muted)] focus:border-cyan-400 focus:outline-none resize-none transition-colors"
          />
        </div>

        <button
          type="button"
          onClick={() => audioFile && onComplete(audioFile, imageFiles, prompt)}
          disabled={!canSubmit}
          className="cta-primary animate-glow w-full py-4 font-display text-sm tracking-widest uppercase flex items-center justify-center gap-3"
        >
          <Wand2 className="w-5 h-5" />
          Start the director
        </button>
        <p className="text-center text-xs text-[var(--muted)]">
          Song + short vision required · character stills optional but best for consistency
        </p>
      </div>
    </section>
  );
}
