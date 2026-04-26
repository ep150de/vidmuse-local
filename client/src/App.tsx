import { useState, useEffect, useRef } from "react";
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

  useEffect(() => {
    // Check server health on mount
    api.healthCheck().then(setServerHealth).catch(() => setServerHealth({ status: "offline" }));
    
    // Setup progress listener
    api.onProgressUpdate((data) => {
      setProgress(data);
    });
  }, []);

  const handleUploadComplete = async (audioFile: File, imageFiles: File[], prompt: string) => {
    setIsLoading(true);
    try {
      const sid = await api.createSession();
      setSessionId(sid);
      
      // Upload audio
      const audioRes = await api.uploadAudio(audioFile);
      console.log("Audio analyzed:", audioRes.analysis);
      
      // Upload images if any
      if (imageFiles.length > 0) {
        await api.uploadImages(imageFiles);
      }
      
      // Send initial prompt
      await api.chat(`Create a music video for this song. Concept: ${prompt}`);
      
      setPhase("chat");
    } catch (e) {
      console.error(e);
      alert("Upload failed. Check server connection.");
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
    <div className="min-h-screen bg-[#0b0f2d] text-white">
      <Header serverHealth={serverHealth} />
      
      <main className="max-w-6xl mx-auto px-4 py-8">
        {/* Phase indicator */}
        <PhaseIndicator current={phase} />
        
        {isLoading && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
            <div className="bg-[#1a1f3d] rounded-xl p-8 text-center">
              <div className="animate-spin w-12 h-12 border-4 border-cyan-400 border-t-transparent rounded-full mx-auto mb-4" />
              <p className="text-lg">{progress.status || "Working..."}</p>
              {progress.progress > 0 && (
                <div className="w-64 h-2 bg-gray-700 rounded-full mt-4 overflow-hidden">
                  <div 
                    className="h-full bg-cyan-400 transition-all duration-300"
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
          <ChatInterface 
            onConceptsReady={handleConceptsReady}
          />
        )}

        {phase === "concepts" && (
          <ConceptSelector 
            onSelect={handleConceptSelected}
          />
        )}

        {phase === "storyboard" && (
          <StoryboardGrid 
            onApprove={handleStoryboardApproved}
          />
        )}

        {phase === "video" && (
          <VideoPreview 
            onComplete={handleVideoComplete}
          />
        )}

        {phase === "final" && (
          <FinalOutput 
            downloadUrl={api.getDownloadUrl()}
          />
        )}
      </main>
      
      <ProgressPanel progress={progress} />
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
    <div className="flex items-center justify-center gap-2 mb-8">
      {phases.map((phase, i) => (
        <div key={phase.id} className="flex items-center gap-2">
          <div className={`
            w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold
            ${i <= currentIndex ? "bg-cyan-500 text-black" : "bg-gray-700 text-gray-400"}
          `}>
            {i + 1}
          </div>
          <span className={`text-sm ${i <= currentIndex ? "text-cyan-400" : "text-gray-500"}`}>
            {phase.label}
          </span>
          {i < phases.length - 1 && (
            <div className={`w-8 h-0.5 ${i < currentIndex ? "bg-cyan-500" : "bg-gray-700"}`} />
          )}
        </div>
      ))}
    </div>
  );
}
