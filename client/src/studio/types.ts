export type ComfyBackend = "cloud" | "local" | "ai-playground";

export type SceneStatus =
  | "queued"
  | "rendering"
  | "ready"
  | "failed"
  | "still_failed";

export interface StudioScene {
  index: number;
  start: number;
  end: number;
  duration: number;
  type: string;
  lyric: string;
  prompt: string;
  status: SceneStatus;
  video_url?: string | null;
  still_url?: string | null;
  error?: string;
}

export interface TimelinePlan {
  version?: number;
  duration?: number;
  direction?: string;
  character?: string;
  character_lock?: string;
  identity_sheet_url?: string | null;
  review_status?: string;
  backend?: string;
  scenes: StudioScene[];
}

export interface StudioMedia {
  audioFile: File | null;
  stillFile: File | null;
  direction: string;
  lyrics: string;
  trackTitle: string;
  duration: number;
  comfyBackend: ComfyBackend;
  comfyUrl: string;
}
