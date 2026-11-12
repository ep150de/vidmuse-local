// SceneMux — API Client
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
  private studioListeners = new Set<(data: any) => void>();

  async createSession(): Promise<string> {
    const res = await fetch(`${SERVER_URL}/api/session/create`, { method: "POST" });
    const data = await parseJson(res);
    this.sessionId = data.session_id;
    this.connectWebSocket(data.session_id);
    return data.session_id;
  }

  /** Drop the current session (closes WS) so the next createSession starts fresh. */
  clearSession() {
    try {
      this.ws?.close();
    } catch {
      /* ignore */
    }
    this.ws = null;
    this.sessionId = null;
  }

  async resetSession(): Promise<string> {
    this.clearSession();
    return this.createSession();
  }

  connectWebSocket(sessionId: string) {
    try {
      this.ws?.close();
    } catch {
      /* ignore */
    }
    this.ws = new WebSocket(`${SERVER_URL.replace("http", "ws")}/api/ws/${sessionId}`);
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "progress") {
        this.progressListeners.forEach((cb) => cb(data));
      }
      if (data.type === "storyboard_frame") {
        this.storyboardFrameListeners.forEach((cb) => cb(data));
      }
      if (
        data.type === "studio_complete" ||
        data.type === "studio_cancelled" ||
        data.type === "studio_error" ||
        data.type === "studio_scene"
      ) {
        this.studioListeners.forEach((cb) => cb(data));
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
    home_agent_url?: string;
    home_agent_password?: string;
    use_home_agent_llm?: boolean;
    use_home_agent_stills?: boolean;
    pipeline_preset?: string;
    pipeline_director?: "cloud_llm" | "home_agent" | "hermes";
    pipeline_stills?: "comfy" | "home_agent" | "fal";
    pipeline_video?: "comfy_cloud" | "comfy_local" | "home_agent" | "fal";
    video_model?:
      | "minimax-h3"
      | "ltx23"
      | "fasth3"
      | "hybrid_ltx"
      | "hybrid_fast";
    hermes_url?: string;
    hermes_token?: string;
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

  // ── Studio (H3-only) ──────────────────────────────────────────────

  onStudioEvent(callback: (data: any) => void) {
    this.studioListeners.add(callback);
    return () => this.studioListeners.delete(callback);
  }

  async studioPlan(
    direction: string,
    options?: { lockIdentity?: boolean; resetIdentity?: boolean }
  ): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        direction,
        lock_identity: options?.lockIdentity ?? true,
        reset_identity: options?.resetIdentity ?? false,
      }),
    });
    return parseJson(res);
  }

  async studioStoryboardStills(options?: {
    maxScenes?: number;
    sceneIndices?: number[];
  }): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const body: Record<string, unknown> = { session_id: this.sessionId };
    if (options?.maxScenes != null) body.max_scenes = options.maxScenes;
    if (options?.sceneIndices?.length) body.scene_indices = options.sceneIndices;
    const res = await fetch(`${SERVER_URL}/api/studio/storyboard-stills`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return parseJson(res);
  }

  async studioUpdatePlan(
    scenes: Array<{
      index: number;
      prompt?: string;
      type?: string;
      video_engine?: string | null;
    }>
  ): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/update-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, scenes }),
    });
    return parseJson(res);
  }

  async cancelStudioRender(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/cancel-render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return parseJson(res);
  }

  async enhancePrompt(
    sceneIndex: number,
    options?: { prompt?: string; direction?: string }
  ): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/enhance-prompt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        scene_index: sceneIndex,
        prompt: options?.prompt,
        direction: options?.direction,
      }),
    });
    return parseJson(res);
  }

  async listMedia(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(
      `${SERVER_URL}/api/studio/${this.sessionId}/media`
    );
    return parseJson(res);
  }

  async useMediaAsRef(name: string, kind: string): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/media/use-as-ref`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        name,
        kind,
      }),
    });
    return parseJson(res);
  }

  async resumeProject(sessionId: string): Promise<any> {
    const res = await fetch(`${SERVER_URL}/api/projects/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await parseJson(res);
    this.clearSession();
    this.sessionId = data.session_id;
    this.connectWebSocket(data.session_id);
    return data;
  }

  async studioRender(
    direction: string,
    rebuildPlan = true,
    scenes?: Array<{ index: number; prompt?: string }>
  ): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        direction,
        rebuild_plan: rebuildPlan,
        scenes,
      }),
    });
    return parseJson(res);
  }

  async studioRenderScene(
    sceneIndex: number,
    options?: { prompt?: string; direction?: string; reassemble?: boolean }
  ): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/render-scene`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        scene_index: sceneIndex,
        prompt: options?.prompt,
        direction: options?.direction,
        reassemble: options?.reassemble ?? true,
      }),
    });
    return parseJson(res);
  }

  async studioState(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/${this.sessionId}/state`);
    return parseJson(res);
  }

  getSessionId(): string | null {
    return this.sessionId;
  }

  getDownloadUrl(): string {
    if (!this.sessionId) throw new Error("No session");
    return `${SERVER_URL}/api/download/${this.sessionId}`;
  }

  async healthCheck(): Promise<any> {
    const res = await fetch(`${SERVER_URL}/api/health`);
    return parseJson(res);
  }

  async listProjects(): Promise<any> {
    const res = await fetch(`${SERVER_URL}/api/projects`);
    return parseJson(res);
  }

  async estimateRender(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/estimate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return parseJson(res);
  }

  async exportVertical(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/studio/export-vertical`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return parseJson(res);
  }

  async diagnoseHomeAgent(
    url: string,
    options?: {
      password?: string;
      probe_comfy?: boolean;
      comfy_url?: string;
    }
  ): Promise<any> {
    const res = await fetch(`${SERVER_URL}/api/home-agent/diagnose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        password: options?.password || "",
        probe_comfy: options?.probe_comfy ?? true,
        comfy_url: options?.comfy_url || undefined,
      }),
    });
    return parseJson(res);
  }

  async testHomeAgent(url: string, password: string): Promise<any> {
    const res = await fetch(`${SERVER_URL}/api/home-agent/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, password }),
    });
    return parseJson(res);
  }

  async homeAgentJob(payload: {
    job_type: string;
    prompt: string;
    url?: string;
    password?: string;
    extras?: Record<string, unknown>;
  }): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/home-agent/job`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, ...payload }),
    });
    return parseJson(res);
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
