// VidMuse Local — API Client
const SERVER_URL = import.meta.env.VITE_SERVER_URL || "http://192.168.1.100:8000";

class VidMuseAPI {
  private ws: WebSocket | null = null;
  private sessionId: string | null = null;
  private onProgress: ((data: any) => void) | null = null;

  async createSession(): Promise<string> {
    const res = await fetch(`${SERVER_URL}/api/session/create`, { method: "POST" });
    const data = await res.json();
    this.sessionId = data.session_id;
    this.connectWebSocket(data.session_id);
    return data.session_id;
  }

  connectWebSocket(sessionId: string) {
    this.ws = new WebSocket(`${SERVER_URL.replace("http", "ws")}/api/ws/${sessionId}`);
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "progress" && this.onProgress) {
        this.onProgress(data);
      }
    };
  }

  onProgressUpdate(callback: (data: any) => void) {
    this.onProgress = callback;
  }

  async uploadAudio(file: File): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const form = new FormData();
    form.append("session_id", this.sessionId);
    form.append("file", file);
    const res = await fetch(`${SERVER_URL}/api/upload/audio`, { method: "POST", body: form });
    return res.json();
  }

  async uploadImages(files: File[]): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const form = new FormData();
    form.append("session_id", this.sessionId);
    files.forEach((f) => form.append("files", f));
    const res = await fetch(`${SERVER_URL}/api/upload/images`, { method: "POST", body: form });
    return res.json();
  }

  async chat(message: string): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, message }),
    });
    return res.json();
  }

  async generateConcepts(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/concepts/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return res.json();
  }

  async selectConcept(index: number): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/concept/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, concept_index: index }),
    });
    return res.json();
  }

  async generateStoryboard(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/storyboard/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return res.json();
  }

  async generateStoryboardImages(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/storyboard/generate-images`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return res.json();
  }

  async approveStoryboard(approvedFrames: string[]): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/storyboard/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, approved_frames: approvedFrames }),
    });
    return res.json();
  }

  async generateVideo(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/video/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return res.json();
  }

  async editFinal(): Promise<any> {
    if (!this.sessionId) throw new Error("No session");
    const res = await fetch(`${SERVER_URL}/api/edit/final`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    });
    return res.json();
  }

  getDownloadUrl(): string {
    if (!this.sessionId) throw new Error("No session");
    return `${SERVER_URL}/api/download/${this.sessionId}`;
  }

  async healthCheck(): Promise<any> {
    const res = await fetch(`${SERVER_URL}/api/health`);
    return res.json();
  }
}

export const api = new VidMuseAPI();
