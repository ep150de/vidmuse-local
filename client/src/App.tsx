import { useState, useEffect } from "react";
import { api } from "./api";
import { UploadScreen } from "./components/UploadScreen";
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

  useEffect(() => {
    api
      .healthCheck()
      .then(setServerHealth)
      .catch(() => setServerHealth({ status: "offline" }));

    api.onProgressUpdate((data) => {
      setProgress(data);
    });
  }, []);

  const handleUploadComplete = async (
    audioFile: File,
    imageFiles: File[],
    prompt: string
  ) => {
    setIsLoading(true);
    setError(null);
    try {
      const sid = await api.createSession();
      setSessionId(sid);

      const audioRes = await api.uploadAudio(audioFile);
      console.log("Audio analyzed:", audioRes.analysis);

      if (imageFiles.length > 0) {
        await api.uploadImages(imageFiles);
      }

      await api.chat(`Create a music video for this song. Concept: ${prompt}`);

      setPhase("chat");
    } catch (e) {
      console.error(e);
      const msg = e instanceof Error ? e.message : String(e);
      setError(`Upload failed: ${msg}`);
    } finally {
      setIsLoading(false);
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
      await api.editFinal();
      setPhase("final");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen text-white">
      <Header serverHealth={serverHealth} />

      <main className="max-w-6xl mx-auto px-4 py-8">
        {phase !== "upload" && <PhaseIndicator current={phase} />}

        {error && (
          <div className="mb-6 panel border-red-400/40 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}

        {isLoading && (
          <div className="fixed inset-0 bg-navy-900/70 backdrop-blur-sm flex items-center justify-center z-50">
            <div className="panel p-8 text-center min-w-[280px]">
              <div className="mx-auto mb-4 h-12 w-12 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin" />
              <p className="font-display text-sm tracking-wide">
                {progress.status || "Working…"}
              </p>
              {progress.progress > 0 && (
                <div className="w-64 h-1.5 bg-navy-900 mt-4 overflow-hidden mx-auto">
                  <div
                    className="h-full bg-gradient-to-r from-cyan-400 to-gold-500 transition-all duration-300"
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
          <FinalOutput downloadUrl={api.getDownloadUrl()} />
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
                    w-8 h-8 flex items-center justify-center text-sm font-display font-bold
                    transition-colors
                    ${active
                      ? "bg-gradient-to-br from-cyan-300 to-cyan-500 text-navy-900 shadow-[0_0_16px_rgba(34,211,238,0.35)]"
                      : done
                        ? "bg-cyan-400/90 text-navy-900"
                        : "bg-navy-700/80 text-[var(--muted)] border border-cyan-500/20"
                    }
                  `}
                >
                  {i + 1}
                </span>
                <span
                  className={`hidden sm:inline text-[11px] uppercase tracking-[0.16em] ${
                    active || done ? "text-cyan-200" : "text-[var(--muted)]"
                  }`}
                >
                  {phase.label}
                </span>
              </div>
              {i < phases.length - 1 && (
                <div
                  className={`w-5 sm:w-8 h-px ${
                    done ? "bg-gradient-to-r from-cyan-400 to-cyan-400/40" : "bg-cyan-500/15"
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
