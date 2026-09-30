// VidMuse Local — API Client
const SERVER_URL = import.meta.env.VITE_SERVER_URL || "http://localhost:8000";

async function parseJson(res: Response): Promise<any> {
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail =
      (typeof data?.detail === "string" && data.detail) ||
      data?.error ||
      res.statusText ||
      `HTTP ${res.status}`;
    throw new Error(detail);
  }
  if (data?.error) {
    throw new Error(String(data.error));
  }
  return data;
}

class VidMuseAPI {
  private ws: WebSocket | null = null;
  private sessionId: string | null = null;
  private progressListeners = new Set<(data: any) => void>();
  private storyboardFrameListeners = new Set<(data: any) => void>();

  async createSession(): Promise<string> {
    const res = await fetch(`${SERVER_URL}/api/session/create`, { method: "POST" });
    const data = await parseJson(res);
    this.sessionId = data.session_id;
    this.connectWebSocket(data.session_id);
    return data.session_id;
  }

  connectWebSocket(sessionId: string) {
    this.ws = new WebSocket(`${SERVER_URL.replace("http", "ws")}/api/ws/${sessionId}`);
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "progress") {
        this.progressListeners.forEach((cb) => cb(data));
      }
      if (data.type === "storyboard_frame") {
        this.storyboardFrameListeners.forEach((cb) => cb(data));
      }
    };
  }

  onProgressUpdate(callback: (data: any) => void) {
    this.progressListeners.add(callback);
    return () => this.progressListeners.delete(callback);
  }

  onStoryboardFrameUpdate(callback: (data: any) => void) {
    this.storyboardFrameListeners.add(callback);
    return () => this.storyboardFrameListeners.delete(callback);
  }

  async uploadAudio(file: File): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const form = new FormData();
    form.append("session_id", this.sessionId);
    form.append("file", file);
    const res = await fetch(`${SERVER_URL}/api/upload/audio`, { method: "POST", body: form });
    return parseJson(res);
  }

  async uploadImages(files: File[]): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const form = new FormData();
    form.append("session_id", this.sessionId);
    files.forEach((f) => form.append("files", f));
    const res = await fetch(`${SERVER_URL}/api/upload/images`, { method: "POST", body: form });
    return parseJson(res);
  }

  async configureSession(payload: {
    singer_profile?: Record<string, string>;
    video_backend?: string;
    lyrics?: string;
    creative_direction?: string;
    performance_mix?: string;
    project_mode?: "mtv" | "typography";
    comfy_backend?: "cloud" | "local" | "ai-playground";
    comfy_url?: string;
  }): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/session/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, ...payload }),
    });
    return parseJson(res);
  }

  async chat(message: string): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, message }),
    });
    return parseJson(res);
  }

  async generateConcepts(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/concepts/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return parseJson(res);
  }

  async selectConcept(index: number): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/concept/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, concept_index: index }),
    });
    return parseJson(res);
  }

  async generateStoryboard(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/storyboard/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return parseJson(res);
  }

  async generateStoryboardImages(options?: {
    startIndex?: number;
    endIndex?: number;
  }): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const body: Record<string, unknown> = { session_id: this.sessionId };
    if (options?.startIndex != null) body.start_index = options.startIndex;
    if (options?.endIndex != null) body.end_index = options.endIndex;
    const res = await fetch(`${SERVER_URL}/api/storyboard/generate-images`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return parseJson(res);
  }

  async renderStoryboardRange(
    startIndex: number,
    endIndex?: number,
    frameIndices?: number[]
  ): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const body: Record<string, unknown> = {
      session_id: this.sessionId,
      start_index: startIndex,
    };
    if (endIndex != null) body.end_index = endIndex;
    if (frameIndices?.length) body.frame_indices = frameIndices;
    const res = await fetch(`${SERVER_URL}/api/storyboard/render-range`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return parseJson(res);
  }

  async cancelStoryboardRender(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/storyboard/cancel-render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return parseJson(res);
  }

  async approveStoryboard(approvedFrames: string[]): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/storyboard/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, approved_frames: approvedFrames }),
    });
    return parseJson(res);
  }

  async generateVideo(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/video/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return parseJson(res);
  }

  async editFinal(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/edit/final`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return parseJson(res);
  }

  getDownloadUrl(): string {
    if (!this.sessionId) throw new Error("No session");
    return `${SERVER_URL}/api/download/${this.sessionId}`;
  }

  async healthCheck(): Promise<any> {
    const res = await fetch(`${SERVER_URL}/api/health`);
    return parseJson(res);
  }

  async studioPlan(payload: {
    direction?: string;
    clip_seconds?: number;
  } = {}): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, ...payload }),
    });
    return parseJson(res);
  }

  async studioRender(payload: {
    indices?: number[];
    assemble?: boolean;
  } = {}): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        assemble: true,
        ...payload,
      }),
    });
    return parseJson(res);
  }

  async studioRegenerate(payload: {
    index: number;
    prompt?: string;
    assemble?: boolean;
  }): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        assemble: true,
        ...payload,
      }),
    });
    return parseJson(res);
  }

  async studioCancel(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return parseJson(res);
  }

  get session(): string | null {
    return this.sessionId;
  }
}

export const api = new VidMuseAPI();

/** Build a full URL for session assets or API paths returned by the server. */
export function mediaUrl(path: string): string {
  if (!path) return "";
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  if (path.startsWith("/")) return `${SERVER_URL}${path}`;
  return `${SERVER_URL}/${path}`;
}
