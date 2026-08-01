import { useState, useEffect } from "react";
import { api } from "./api";
import { UploadScreen, type UploadPayload } from "./components/UploadScreen";
import { ChatInterface } from "./components/ChatInterface";
import { ConceptSelector } from "./components/ConceptSelector";
import { StoryboardGrid } from "./components/StoryboardGrid";
import { VideoPreview } from "./components/VideoPreview";
import { FinalOutput } from "./components/FinalOutput";
import { ProgressPanel } from "./components/ProgressPanel";
import { Header } from "./components/Header";

type Phase = "upload" | "chat" | "concepts" | "storyboard" | "video" | "final";

export default function App() {
  const [phase, setPhase] = useState<Phase>("upload");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState({ phase: "", progress: 0, status: "" });
  const [serverHealth, setServerHealth] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingHint, setLoadingHint] = useState<string>("");
  const [technicalQc, setTechnicalQc] = useState<any>(null);


  useEffect(() => {
    api
      .healthCheck()
      .then(setServerHealth)
      .catch(() => setServerHealth({ status: "offline" }));

    api.onProgressUpdate((data) => {
      setProgress(data);
    });
  }, []);

  const handleUploadComplete = async (payload: UploadPayload) => {
    setIsLoading(true);
    setError(null);
    setLoadingHint("Creating session…");
    try {
      const sid = await api.createSession();
      setSessionId(sid);

      setLoadingHint("Saving singer profile…");
      await api.configureSession({
        singer_profile: payload.singer,
        video_backend: payload.videoBackend,
        lyrics: payload.lyrics,
        creative_direction: payload.prompt,
        performance_mix: payload.performanceMix,
        project_mode: payload.projectMode,
        comfy_backend: payload.comfyBackend,
        comfy_url: payload.comfyUrl,
      });

      setLoadingHint(
        payload.lyrics.trim()
          ? "Analyzing audio…"
          : "Analyzing audio + extracting lyrics from the track…"
      );
      const audioRes = await api.uploadAudio(payload.audio);
      console.log("Audio analyzed:", audioRes.analysis);
      const hasLyrics = Boolean(audioRes.analysis?.has_lyrics);

      if (payload.images.length > 0) {
        setLoadingHint("Uploading reference stills…");
        await api.uploadImages(payload.images);
      }

      const singerBits = [
        payload.singer.name && `singer ${payload.singer.name}`,
        payload.singer.presentation,
        payload.singer.age_range,
        payload.singer.look,
        payload.singer.framing,
      ]
        .filter(Boolean)
        .join(", ");

      setLoadingHint("Briefing the director…");
      await api.chat(
        `Create a ${payload.projectMode === "typography" ? "typography-led lyric video" : "MTV-style music video"} for this song. Concept: ${payload.prompt}` +
          (payload.projectMode === "mtv" && singerBits ? `\nSinger: ${singerBits}` : "") +
          `\nProject mode: ${payload.projectMode}` +
          `\nGeneration engine: ${payload.comfyBackend}` +
          `\nAnimation model: ${payload.videoBackend}` +
          (hasLyrics
            ? "\nLyrics were captured — ground every concept in the lyrical story and imagery."
            : "\nLyrics were not captured — ask clarifying questions about the song's meaning before proposing concepts.") +
          (payload.projectMode === "mtv" && payload.images.length
            ? "\nReference photo uploaded — lock the lead singer identity to that face."
            : payload.projectMode === "mtv"
              ? "\nNo reference photo — warn that face consistency will be weaker."
              : "\nTreat exact lyrics as the protagonist; reserve text-safe composition space.")
      );

      // Fast path: with lyrics, go straight to concepts
      if (hasLyrics || payload.lyrics.trim()) {
        setPhase("concepts");
      } else {
        setPhase("chat");
      }
    } catch (e) {
      console.error(e);
      const msg = e instanceof Error ? e.message : String(e);
      setError(`Upload failed: ${msg}`);
    } finally {
      setIsLoading(false);
      setLoadingHint("");
    }
  };

  const handleConceptsReady = () => {
    setPhase("concepts");
  };

  const handleConceptSelected = async (index: number) => {
    setIsLoading(true);
    try {
      await api.selectConcept(index);
      setPhase("storyboard");
    } finally {
      setIsLoading(false);
    }
  };

  const handleStoryboardApproved = async (approvedIds: string[]) => {
    setIsLoading(true);
    try {
      await api.approveStoryboard(approvedIds);
      setPhase("video");
    } finally {
      setIsLoading(false);
    }
  };

  const handleVideoComplete = async () => {
    setIsLoading(true);
    try {
      const result = await api.editFinal();
      setTechnicalQc(result?.technical_qc || null);
      setPhase("final");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen text-[var(--ink)]">
      <Header serverHealth={serverHealth} />

      <main className="max-w-6xl mx-auto px-4 py-8">
        {phase !== "upload" && <PhaseIndicator current={phase} />}

        {error && (
          <div className="mb-6 panel rounded-xl border-red-400/40 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        )}

        {isLoading && (
          <div className="fixed inset-0 bg-[#070b14]/75 backdrop-blur-sm flex items-center justify-center z-50">
            <div className="panel rounded-xl p-8 text-center min-w-[280px]">
              <div className="mx-auto mb-4 h-12 w-12 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
              <p className="text-sm font-medium tracking-wide text-[var(--ink)]">
                {loadingHint || progress.status || "Working…"}
              </p>
              {progress.progress > 0 && (
                <div className="w-64 h-1.5 bg-[var(--surface-2)] mt-4 overflow-hidden mx-auto rounded-full">
                  <div
                    className="h-full bg-gradient-to-r from-sky-400 to-amber-400 transition-all duration-300"
                    style={{ width: `${progress.progress}%` }}
                  />
                </div>
              )}
            </div>
          </div>
        )}

        {phase === "upload" && (
          <UploadScreen onComplete={handleUploadComplete} />
        )}

        {phase === "chat" && (
          <ChatInterface onConceptsReady={handleConceptsReady} />
        )}

        {phase === "concepts" && (
          <ConceptSelector
            onSelect={handleConceptSelected}
            onError={setError}
          />
        )}

        {phase === "storyboard" && (
          <StoryboardGrid
            onApprove={handleStoryboardApproved}
            onBackToConcepts={() => setPhase("concepts")}
            onError={setError}
          />
        )}

        {phase === "video" && (
          <VideoPreview
            onComplete={handleVideoComplete}
            onError={setError}
          />
        )}

        {phase === "final" && (
          <FinalOutput downloadUrl={api.getDownloadUrl()} technicalQc={technicalQc} />
        )}
      </main>

      <ProgressPanel progress={progress} />
      {/* silence unused sessionId warning in legacy flow */}
      <span className="sr-only">{sessionId}</span>
    </div>
  );
}

function PhaseIndicator({ current }: { current: Phase }) {
  const phases: { id: Phase; label: string }[] = [
    { id: "upload", label: "Upload" },
    { id: "chat", label: "Ideate" },
    { id: "concepts", label: "Concepts" },
    { id: "storyboard", label: "Storyboard" },
    { id: "video", label: "Video" },
    { id: "final", label: "Final" },
  ];

  const currentIndex = phases.findIndex((p) => p.id === current);

  return (
    <nav
      aria-label="Pipeline progress"
      className="mb-8 animate-rise"
    >
      <ol className="flex items-center justify-center gap-1 sm:gap-2 flex-wrap">
        {phases.map((phase, i) => {
          const done = i < currentIndex;
          const active = i === currentIndex;
          return (
            <li key={phase.id} className="flex items-center gap-1 sm:gap-2">
              <div
                className={`
                  flex items-center gap-2 px-2 py-1.5
                  ${active ? "phase-active" : ""}
                `}
              >
                <span
                  className={`
                    w-8 h-8 rounded-lg flex items-center justify-center text-sm font-display font-bold
                    transition-colors
                    ${active
                      ? "bg-gradient-to-br from-sky-300 to-sky-500 text-[#071018] shadow-[0_0_16px_rgba(56,189,248,0.28)]"
                      : done
                        ? "bg-sky-400/90 text-[#071018]"
                        : "bg-[var(--surface-2)] text-[var(--muted)] border border-[var(--border)]"
                    }
                  `}
                >
                  {i + 1}
                </span>
                <span
                  className={`hidden sm:inline text-[11px] uppercase tracking-[0.16em] ${
                    active || done ? "text-[var(--ink)]" : "text-[var(--muted)]"
                  }`}
                >
                  {phase.label}
                </span>
              </div>
              {i < phases.length - 1 && (
                <div
                  className={`w-5 sm:w-8 h-px ${
                    done ? "bg-gradient-to-r from-sky-400 to-sky-400/40" : "bg-[var(--border)]"
                  }`}
                />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
