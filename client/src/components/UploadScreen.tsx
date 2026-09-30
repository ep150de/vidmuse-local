import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import {
  Image,
  Music,
  Wand2,
  Sparkles,
  UserRound,
  Video,
  Mic2,
  Type,
  Clapperboard,
  Cloud,
  Server,
} from "lucide-react";

export type VideoBackend = "infinitetalk" | "minimax-h3" | "ltx23" | "humo";
export type PerformanceMix = "hybrid" | "performance";
export type ProjectMode = "mtv" | "typography";
export type ComfyBackend = "cloud" | "local" | "ai-playground";

export interface SingerProfile {
  name: string;
  presentation: string;
  age_range: string;
  look: string;
  framing: string;
}

export interface UploadPayload {
  audio: File;
  images: File[];
  prompt: string;
  singer: SingerProfile;
  videoBackend: VideoBackend;
  lyrics: string;
  performanceMix: PerformanceMix;
  projectMode: ProjectMode;
  comfyBackend: ComfyBackend;
  comfyUrl: string;
}

interface Props {
  onComplete: (payload: UploadPayload) => void;
}

const PRESENTATIONS = [
  "Woman",
  "Man",
  "Non-binary",
  "Androgynous",
  "Custom / unspecified",
];

const AGE_RANGES = ["Teen", "Young adult", "Adult", "Mature adult"];

const FRAMINGS = [
  "close-up on face and head",
  "head and shoulders",
  "waist-up performance",
  "full body",
];

const VIDEO_MODES: {
  id: VideoBackend;
  title: string;
  blurb: string;
}[] = [
  {
    id: "minimax-h3",
    title: "Performance · MiniMax H3",
    blurb: "Open-weight lip sync via image+audio refs (local or cloud Comfy ≥0.30)",
  },
  {
    id: "infinitetalk",
    title: "Performance · InfiniteTalk",
    blurb: "Face/head singing, strong lip sync (Wan2.1)",
  },
  {
    id: "ltx23",
    title: "Cinematic · LTX-2.3 IA2V",
    blurb: "Official-style image+audio→video; works on local distilled weights",
  },
  {
    id: "humo",
    title: "Classic · HuMo",
    blurb: "Fallback lip-sync path",
  },
];

export function UploadScreen({ onComplete }: Props) {
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [prompt, setPrompt] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [videoBackend, setVideoBackend] = useState<VideoBackend>("minimax-h3");
  const [performanceMix, setPerformanceMix] = useState<PerformanceMix>("hybrid");
  const [projectMode, setProjectMode] = useState<ProjectMode>("mtv");
  const [comfyBackend, setComfyBackend] = useState<ComfyBackend>("cloud");
  const [comfyUrl, setComfyUrl] = useState("http://localhost:8188");
  const [singer, setSinger] = useState<SingerProfile>({
    name: "",
    presentation: "Woman",
    age_range: "Young adult",
    look: "",
    framing: "close-up on face and head",
  });

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

  const field =
    "w-full rounded-lg bg-[var(--surface-2)] border border-[var(--border)] px-3 py-2.5 text-sm text-[var(--ink)] placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-soft)] transition";

  return (
    <section className="relative min-h-[78vh] flex flex-col justify-center animate-rise">
      <div className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[36vh] overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_0%,rgba(56,189,248,0.16),transparent_60%)]" />
      </div>

      <div className="text-center mb-10">
        <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-[var(--accent)] mb-3">
          Music video studio
        </p>
        <h1 className="font-display text-4xl md:text-6xl font-bold text-[var(--ink)] mb-3 tracking-tight">
          VidMuse
        </h1>
        <p className="text-base md:text-lg text-[var(--text-secondary)] max-w-xl mx-auto leading-relaxed">
          Choose MTV performance style or typography lyric style, then generate a
          full-song video from your track.
        </p>
      </div>

      <div className="max-w-3xl mx-auto w-full space-y-4">
        <div className="panel panel-shine rounded-xl p-5 md:p-6 space-y-3">
          <div className="flex items-center gap-2 text-[var(--accent)]">
            <Clapperboard className="w-5 h-5" />
            <h2 className="text-sm font-semibold tracking-wide text-[var(--ink)]">
              Choose your video format
            </h2>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {(
              [
                {
                  id: "mtv" as const,
                  title: "MTV Music Video",
                  blurb: "Performer identity, lip-sync, cinematic story and B-roll",
                  icon: Video,
                },
                {
                  id: "typography" as const,
                  title: "Typography Lyric Video",
                  blurb: "Kinetic exact lyrics, graphic compositions and instrumental arcs",
                  icon: Type,
                },
              ] as const
            ).map((option) => {
              const active = projectMode === option.id;
              const Icon = option.icon;
              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setProjectMode(option.id)}
                  className={`text-left rounded-lg border p-4 transition ${
                    active
                      ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                      : "border-[var(--border)] bg-[var(--surface-2)] hover:border-[var(--accent)]/40"
                  }`}
                >
                  <Icon className="w-5 h-5 text-[var(--accent)] mb-2" />
                  <p className="text-sm font-semibold text-[var(--ink)]">{option.title}</p>
                  <p className="text-xs text-[var(--text-secondary)] mt-1 leading-snug">
                    {option.blurb}
                  </p>
                </button>
              );
            })}
          </div>
        </div>

        <div
          {...audioDropzone.getRootProps()}
          className={`
            panel panel-shine rounded-xl p-7 text-center cursor-pointer transition-all duration-200
            ${audioFile ? "border-[var(--accent)]/60" : "hover:border-[var(--accent)]/40"}
          `}
        >
          <input {...audioDropzone.getInputProps()} />
          <Music className="w-10 h-10 mx-auto mb-3 text-[var(--accent)]" />
          {audioFile ? (
            <div>
              <p className="text-sm font-semibold text-[var(--ink)]">{audioFile.name}</p>
              <p className="text-xs text-[var(--text-secondary)] mt-1">
                {(audioFile.size / 1024 / 1024).toFixed(1)} MB · ready
              </p>
            </div>
          ) : (
            <div>
              <p className="font-semibold text-lg mb-1 text-[var(--ink)]">1. Drop your song</p>
              <p className="text-sm text-[var(--text-secondary)]">MP3, WAV, M4A</p>
            </div>
          )}
        </div>

        {projectMode === "mtv" && (
        <div className="panel panel-shine rounded-xl p-5 md:p-6 space-y-4">
          <div className="flex items-center gap-2 text-[var(--accent)]">
            <Mic2 className="w-5 h-5" />
            <h2 className="text-sm font-semibold tracking-wide text-[var(--ink)]">
              2. Singer profile
            </h2>
          </div>
          <p className="text-sm text-[var(--text-secondary)]">
            Lock who appears on camera. Reference stills plus these details keep identity consistent.
          </p>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-[var(--text-secondary)]">Name / persona</span>
              <input
                className={field}
                value={singer.name}
                onChange={(e) => setSinger((s) => ({ ...s, name: e.target.value }))}
                placeholder="e.g. Nova Vale"
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-[var(--text-secondary)]">Presentation</span>
              <select
                className={field}
                value={singer.presentation}
                onChange={(e) => setSinger((s) => ({ ...s, presentation: e.target.value }))}
              >
                {PRESENTATIONS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-[var(--text-secondary)]">Age range</span>
              <select
                className={field}
                value={singer.age_range}
                onChange={(e) => setSinger((s) => ({ ...s, age_range: e.target.value }))}
              >
                {AGE_RANGES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-[var(--text-secondary)]">Camera framing</span>
              <select
                className={field}
                value={singer.framing}
                onChange={(e) => setSinger((s) => ({ ...s, framing: e.target.value }))}
              >
                {FRAMINGS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-[var(--text-secondary)]">
              Look notes (hair, makeup, outfit)
            </span>
            <textarea
              className={`${field} h-20 resize-none`}
              value={singer.look}
              onChange={(e) => setSinger((s) => ({ ...s, look: e.target.value }))}
              placeholder="Silver bob, chrome jacket, cyan eyeliner…"
            />
          </label>

          <div
            {...imageDropzone.getRootProps()}
            className={`rounded-lg border border-dashed p-4 text-center cursor-pointer transition ${
              imageFiles.length > 0
                ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                : "border-amber-400/50 bg-amber-500/5 hover:border-amber-300/70"
            }`}
          >
            <input {...imageDropzone.getInputProps()} />
            <div className="flex items-center justify-center gap-2 mb-1 text-[var(--accent)]">
              <UserRound className="w-5 h-5" />
              <Image className="w-4 h-4 opacity-70" />
            </div>
            <p className="text-sm font-semibold text-[var(--ink)]">
              Character reference photo {imageFiles.length === 0 ? "(strongly recommended)" : "ready"}
            </p>
            <p className="text-xs text-[var(--text-secondary)] mt-1">
              Clear face still — used as identity lock for every storyboard frame
            </p>
            {imageFiles.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-3 justify-center">
                {imageFiles.map((file, i) => (
                  <div
                    key={`${file.name}-${i}`}
                    className="w-14 h-14 overflow-hidden rounded-md border border-[var(--border)]"
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
        </div>
        )}

        {projectMode === "mtv" && (
        <div className="panel panel-shine rounded-xl p-5 md:p-6 space-y-3">
          <div className="flex items-center gap-2 text-[var(--accent)]">
            <Video className="w-5 h-5" />
            <h2 className="text-sm font-semibold text-[var(--ink)]">3. Animation model</h2>
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            {VIDEO_MODES.map((mode) => {
              const active = videoBackend === mode.id;
              return (
                <button
                  key={mode.id}
                  type="button"
                  onClick={() => setVideoBackend(mode.id)}
                  className={`
                    text-left rounded-lg border px-3 py-3 transition
                    ${active
                      ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                      : "border-[var(--border)] bg-[var(--surface-2)] hover:border-[var(--accent)]/40"
                    }
                  `}
                >
                  <p className="text-sm font-semibold text-[var(--ink)]">{mode.title}</p>
                  <p className="text-xs text-[var(--text-secondary)] mt-1 leading-snug">
                    {mode.blurb}
                  </p>
                </button>
              );
            })}
          </div>
          <div className="pt-2 space-y-2">
            <p className="text-xs font-medium text-[var(--text-secondary)]">Performance mix</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {(
                [
                  {
                    id: "hybrid" as const,
                    title: "Hybrid (VidMuse-style)",
                    blurb: "Avatar lip-sync on lyric peaks + cinematic B-roll",
                  },
                  {
                    id: "performance" as const,
                    title: "Performance-first",
                    blurb: "Mostly face/head singing from your reference photo",
                  },
                ] as const
              ).map((opt) => {
                const active = performanceMix === opt.id;
                return (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setPerformanceMix(opt.id)}
                    className={`
                      text-left rounded-lg border px-3 py-3 transition
                      ${active
                        ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                        : "border-[var(--border)] bg-[var(--surface-2)] hover:border-[var(--accent)]/40"
                      }
                    `}
                  >
                    <p className="text-sm font-semibold text-[var(--ink)]">{opt.title}</p>
                    <p className="text-xs text-[var(--text-secondary)] mt-1 leading-snug">
                      {opt.blurb}
                    </p>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
        )}

        <div className="panel panel-shine rounded-xl p-5 md:p-6 space-y-3">
          <div className="flex items-center gap-2 text-[var(--accent)]">
            <Server className="w-5 h-5" />
            <h2 className="text-sm font-semibold text-[var(--ink)]">Generation engine</h2>
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            {(
              [
                { id: "cloud" as const, title: "Comfy Cloud", icon: Cloud },
                { id: "local" as const, title: "Local ComfyUI", icon: Server },
                { id: "ai-playground" as const, title: "Intel AI Playground", icon: Server },
              ] as const
            ).map((option) => {
              const active = comfyBackend === option.id;
              const Icon = option.icon;
              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setComfyBackend(option.id)}
                  className={`text-left rounded-lg border px-3 py-3 transition ${
                    active
                      ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                      : "border-[var(--border)] bg-[var(--surface-2)] hover:border-[var(--accent)]/40"
                  }`}
                >
                  <Icon className="w-4 h-4 text-[var(--accent)] mb-1.5" />
                  <p className="text-sm font-semibold text-[var(--ink)]">{option.title}</p>
                </button>
              );
            })}
          </div>
          {comfyBackend !== "cloud" && (
            <label className="block space-y-1.5 pt-1">
              <span className="text-xs font-medium text-[var(--text-secondary)]">
                Comfy queue URL
              </span>
              <input
                className={field}
                value={comfyUrl}
                onChange={(event) => setComfyUrl(event.target.value)}
                placeholder="http://localhost:8188"
              />
              <p className="text-xs text-[var(--muted)]">
                Start the Comfy server first (ComfyUI ≥ 0.30 for MiniMax H3). AI
                Playground commonly exposes the same port. Install open weights into
                your local Comfy models folders — H3 from Hugging Face{" "}
                <span className="font-mono">Comfy-Org/MiniMax-H3</span> (ref2va +
                VAEs + Qwen3VL), LTX distilled checkpoint + Gemma text encoder.
                Health → <span className="font-mono">video_models</span> reports
                which backends are ready.
              </p>
            </label>
          )}
        </div>

        <div className="panel panel-shine rounded-xl p-5">
          <label className="block space-y-1.5">
            <span className="text-sm font-semibold text-[var(--ink)]">
              4. Lyrics
            </span>
            <p className="text-xs text-[var(--text-secondary)]">
              Extracted automatically from your MP3 after upload (Whisper). Leave blank
              unless you want to override with official lyrics.
            </p>
            <textarea
              value={lyrics}
              onChange={(e) => setLyrics(e.target.value)}
              placeholder="Optional override — otherwise auto-extracted from the song…"
              className={`${field} h-28 resize-none mt-2`}
            />
          </label>
        </div>

        <div className="panel panel-shine rounded-xl p-5">
          <label className="flex items-center gap-2 text-sm font-semibold mb-3 text-[var(--ink)]">
            <Sparkles className="w-4 h-4 text-[var(--accent)]" />
            5. Creative direction
          </label>
          <p className="text-xs text-[var(--text-secondary)] mb-2">
            Camera, palette, and vibe only — lyrics drive the story.
          </p>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Handheld close-ups, warm tungsten, rain on glass…"
            className={`${field} h-28 resize-none`}
          />
        </div>

        <button
          type="button"
          onClick={() =>
            audioFile &&
            onComplete({
              audio: audioFile,
              images: imageFiles,
              prompt,
              singer,
              videoBackend,
              lyrics,
              performanceMix,
              projectMode,
              comfyBackend,
              comfyUrl:
                comfyBackend === "cloud" ? "https://cloud.comfy.org" : comfyUrl,
            })
          }
          disabled={!canSubmit}
          className="cta-primary w-full py-4 rounded-xl text-sm tracking-wide uppercase flex items-center justify-center gap-3"
        >
          <Wand2 className="w-5 h-5" />
          Start production
        </button>
        <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-2)]/60 px-4 py-3 text-xs text-[var(--text-secondary)] space-y-1.5">
          <p className="font-semibold text-[var(--ink)] text-sm">Before you start</p>
          <p className={audioFile ? "text-emerald-300" : ""}>
            {audioFile ? "✓" : "○"} Song uploaded
          </p>
          <p className="text-[var(--text-secondary)]">
            ✓ Lyrics auto-extracted from the MP3 on upload
            {lyrics.trim() ? " (manual override set)" : ""}
          </p>
          {projectMode === "mtv" && (
            <p className={imageFiles.length ? "text-emerald-300" : "text-amber-200"}>
              {imageFiles.length ? "✓" : "○"} Singer reference photo for face consistency
            </p>
          )}
          <p className={prompt.trim() ? "text-emerald-300" : ""}>
            {prompt.trim() ? "✓" : "○"} Creative direction
          </p>
        </div>
      </div>
    </section>
  );
}
