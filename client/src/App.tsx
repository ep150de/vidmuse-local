import { useState, useEffect } from "react";
import { api } from "./api";
import { StudioApp } from "./studio/StudioApp";
import { UploadScreen, type UploadPayload } from "./components/UploadScreen";
import { ChatInterface } from "./components/ChatInterface";
import { ConceptSelector } from "./components/ConceptSelector";
import { StoryboardGrid } from "./components/StoryboardGrid";
import { VideoPreview } from "./components/VideoPreview";
import { FinalOutput } from "./components/FinalOutput";
import { ProgressPanel } from "./components/ProgressPanel";
import { Header } from "./components/Header";

type Phase = "upload" | "chat" | "concepts" | "storyboard" | "video" | "final";

/** Default experience is the media studio. Legacy wizard remains at ?wizard=1 */
function useLegacyWizard(): boolean {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("wizard") === "1";
}

function LegacyWizardApp() {
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

        {phase === "upload" && <UploadScreen onComplete={handleUploadComplete} />}

        {phase === "chat" && <ChatInterface onConceptsReady={handleConceptsReady} />}

        {phase === "concepts" && (
          <ConceptSelector onSelect={handleConceptSelected} onError={setError} />
        )}

        {phase === "storyboard" && (
          <StoryboardGrid
            onApprove={handleStoryboardApproved}
            onBackToConcepts={() => setPhase("concepts")}
            onError={setError}
          />
        )}

        {phase === "video" && (
          <VideoPreview onComplete={handleVideoComplete} onError={setError} />
        )}

        {phase === "final" && (
          <FinalOutput
            technicalQc={technicalQc}
            downloadUrl={api.getDownloadUrl()}
          />
        )}

        {phase !== "upload" && phase !== "final" && (
          <ProgressPanel progress={progress} />
        )}
      </main>
    </div>
  );
}

function PhaseIndicator({ current }: { current: Phase }) {
  const phases: Phase[] = ["upload", "chat", "concepts", "storyboard", "video", "final"];
  return (
    <div className="flex flex-wrap gap-2 mb-6 text-xs">
      {phases.map((p) => (
        <span
          key={p}
          className={`px-2 py-1 rounded border ${
            p === current
              ? "border-[var(--accent)] text-[var(--ink)] phase-active"
              : "border-[var(--border)] text-[var(--muted)]"
          }`}
        >
          {p}
        </span>
      ))}
    </div>
  );
}

export default function App() {
  const legacy = useLegacyWizard();
  if (legacy) return <LegacyWizardApp />;
  return <StudioApp />;
}
