export type ComfyTarget = "cloud" | "local" | "ai-playground";

export type SceneStatus = "queued" | "rendering" | "ready" | "failed";

export interface StudioScene {
  id: string;
  index: number;
  start: number;
  end: number;
  duration: number;
  type: "PERF" | "THEME" | "OUTRO" | string;
  lyric: string;
  prompt: string;
  status: SceneStatus | string;
  video_path?: string | null;
  video_url?: string | null;
  has_still?: boolean;
}

export interface StudioProject {
  songTitle: string;
  audioFile: File | null;
  stillFile: File | null;
  direction: string;
  lyrics: string;
  comfyBackend: ComfyTarget;
  comfyUrl: string;
  duration: number;
  timeline: StudioScene[];
  selectedIndex: number;
  finalReady: boolean;
  progress: { phase: string; progress: number; status: string };
}
