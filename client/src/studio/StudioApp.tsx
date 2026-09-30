import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import { api, mediaUrl } from "../api";
import type { ComfyTarget, StudioScene } from "./types";
import "./studio.css";

function formatTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function StudioApp() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [songTitle, setSongTitle] = useState("Untitled project");
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [stillFile, setStillFile] = useState<File | null>(null);
  const [stillPreview, setStillPreview] = useState<string | null>(null);
  const [direction, setDirection] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [comfyBackend, setComfyBackend] = useState<ComfyTarget>("cloud");
  const [comfyUrl, setComfyUrl] = useState("http://localhost:8188");
  const [duration, setDuration] = useState(0);
  const [timeline, setTimeline] = useState<StudioScene[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Drop an MP3 to start");
  const [progress, setProgress] = useState({ phase: "", progress: 0, status: "" });
  const [error, setError] = useState<string | null>(null);
  const [finalReady, setFinalReady] = useState(false);
  const [health, setHealth] = useState<any>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    api.healthCheck().then(setHealth).catch(() => setHealth({ status: "offline" }));
    return api.onProgressUpdate((data) => {
      setProgress({
        phase: data.phase || "",
        progress: Number(data.progress) || 0,
        status: data.status || "",
      });
      if (data.status) setStatus(data.status);
    });
  }, []);

  useEffect(() => {
    if (!stillFile) {
      setStillPreview(null);
      return;
    }
    const url = URL.createObjectURL(stillFile);
    setStillPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [stillFile]);

  const selected = timeline[selectedIndex] || null;
  const monitorSrc = useMemo(() => {
    if (finalReady && sessionId) return api.getDownloadUrl();
    if (selected?.video_url) return mediaUrl(selected.video_url);
    return null;
  }, [finalReady, sessionId, selected]);

  const h3Ready = useMemo(() => {
    if (comfyBackend === "cloud") return true;
    const vm =
      health?.services?.comfyui?.video_models ||
      health?.comfyui?.video_models ||
      health?.video_models;
    return Boolean(vm?.["minimax-h3"]?.ready);
  }, [comfyBackend, health]);

  const ensureSession = useCallback(async () => {
    if (api.session) {
      setSessionId(api.session);
      return api.session;
    }
    const sid = await api.createSession();
    setSessionId(sid);
    return sid;
  }, []);

  const importAudio = useCallback(
    async (file: File) => {
      setError(null);
      setBusy(true);
      setFinalReady(false);
      setTimeline([]);
      setAudioFile(file);
      setSongTitle(file.name.replace(/\.[^.]+$/, ""));
      setStatus("Analyzing audio…");
      try {
        await ensureSession();
        await api.configureSession({
          video_backend: "minimax-h3",
          creative_direction: direction,
          lyrics,
          project_mode: "mtv",
          performance_mix: "performance",
          comfy_backend: comfyBackend,
          comfy_url: comfyUrl,
        });
        const res = await api.uploadAudio(file);
        const dur = Number(res?.analysis?.duration || 0);
        setDuration(dur);
        if (res?.analysis?.lyrics && !lyrics.trim()) {
          setLyrics(String(res.analysis.lyrics));
        }
        setStatus(
          res?.analysis?.has_lyrics
            ? `Ready · ${formatTime(dur)} · lyrics captured`
            : `Ready · ${formatTime(dur)} · add lyrics or direction, then Plan`
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [comfyBackend, comfyUrl, direction, ensureSession, lyrics]
  );

  const onAudioDrop = useCallback(
    (files: File[]) => {
      if (files[0]) void importAudio(files[0]);
    },
    [importAudio]
  );

  const onStillDrop = useCallback((files: File[]) => {
    if (files[0]) setStillFile(files[0]);
  }, []);

  const audioDz = useDropzone({
    onDrop: onAudioDrop,
    accept: { "audio/*": [".mp3", ".wav", ".m4a", ".ogg"] },
    multiple: false,
    noClick: false,
  });

  const stillDz = useDropzone({
    onDrop: onStillDrop,
    accept: { "image/*": [".png", ".jpg", ".jpeg", ".webp"] },
    multiple: false,
  });

  const handlePlan = async () => {
    if (!audioFile) {
      setError("Import an MP3 first");
      return;
    }
    setBusy(true);
    setError(null);
    setFinalReady(false);
    try {
      await ensureSession();
      await api.configureSession({
        video_backend: "minimax-h3",
        creative_direction: direction,
        lyrics,
        project_mode: "mtv",
        performance_mix: "performance",
        comfy_backend: comfyBackend,
        comfy_url: comfyUrl,
      });
      if (stillFile) {
        await api.uploadImages([stillFile]);
      }
      setStatus("Building H3 scene plan…");
      const res = await api.studioPlan({ direction, clip_seconds: 10 });
      const scenes = (res.timeline || []) as StudioScene[];
      setTimeline(scenes);
      setSelectedIndex(0);
      setDuration(Number(res.duration || duration));
      setStatus(`Plan ready · ${scenes.length} scenes`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleRender = async () => {
    if (!timeline.length) {
      await handlePlan();
    }
    if (!h3Ready && comfyBackend !== "cloud") {
      setError("Local MiniMax H3 weights not detected — check Comfy health or switch to Cloud");
      return;
    }
    setBusy(true);
    setError(null);
    setFinalReady(false);
    try {
      await api.configureSession({
        video_backend: "minimax-h3",
        creative_direction: direction,
        lyrics,
        comfy_backend: comfyBackend,
        comfy_url: comfyUrl,
        performance_mix: "performance",
        project_mode: "mtv",
      });
      if (stillFile) await api.uploadImages([stillFile]);
      setStatus("Rendering MiniMax H3 scenes…");
      const res = await api.studioRender({ assemble: true });
      setTimeline((res.timeline || []) as StudioScene[]);
      if (res.final_video_path || res.download_url) {
        setFinalReady(true);
        setStatus("Final cut ready — original MP3 muxed");
      } else {
        setStatus("Render finished (partial)");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleRegenerate = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.studioRegenerate({
        index: selected.index,
        prompt: selected.prompt,
        assemble: true,
      });
      setTimeline((res.timeline || []) as StudioScene[]);
      if (res.final_video_path || res.download_url) setFinalReady(true);
      setStatus(`Scene ${selected.index + 1} regenerated`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const updateSelectedPrompt = (value: string) => {
    setTimeline((prev) =>
      prev.map((s, i) => (i === selectedIndex ? { ...s, prompt: value } : s))
    );
  };

  const ticks = useMemo(() => {
    if (duration <= 0) return [0];
    const step = duration > 180 ? 30 : duration > 60 ? 15 : 10;
    const out: number[] = [];
    for (let t = 0; t <= duration; t += step) out.push(t);
    if (out[out.length - 1] !== Math.floor(duration)) out.push(duration);
    return out;
  }, [duration]);

  return (
    <div className="studio-root">
      <header className="studio-top">
        <div className="studio-brand">VidMuse</div>
        <div className="studio-song">{songTitle}</div>
        <div className="studio-top-actions">
          <select
            className="studio-select"
            value={comfyBackend}
            onChange={(e) => setComfyBackend(e.target.value as ComfyTarget)}
          >
            <option value="cloud">Comfy Cloud</option>
            <option value="local">Local Comfy</option>
            <option value="ai-playground">AI Playground</option>
          </select>
          {comfyBackend !== "cloud" && (
            <input
              className="studio-input"
              style={{ width: 180 }}
              value={comfyUrl}
              onChange={(e) => setComfyUrl(e.target.value)}
              placeholder="http://localhost:8188"
            />
          )}
          <div className="studio-progress" title={progress.status}>
            <span style={{ width: `${Math.max(0, Math.min(100, progress.progress))}%` }} />
          </div>
          <button className="studio-btn" disabled={busy || !audioFile} onClick={() => void handlePlan()}>
            Plan
          </button>
          <button
            className="studio-btn studio-btn-primary"
            disabled={busy || !audioFile}
            onClick={() => void handleRender()}
          >
            Render H3
          </button>
          <button
            className="studio-btn"
            disabled={!finalReady}
            onClick={() => {
              if (finalReady) window.open(api.getDownloadUrl(), "_blank");
            }}
          >
            Export
          </button>
        </div>
      </header>

      <div className="studio-body">
        <aside className="studio-bin">
          <div className="studio-section-label">Media bin</div>
          <div
            {...audioDz.getRootProps({ className: `studio-drop ${audioDz.isDragActive ? "active" : ""}` })}
          >
            <input {...audioDz.getInputProps()} />
            {audioFile ? `Audio · ${audioFile.name}` : "Drop MP3 / click to import"}
          </div>
          <div
            {...stillDz.getRootProps({ className: `studio-drop ${stillDz.isDragActive ? "active" : ""}` })}
          >
            <input {...stillDz.getInputProps()} />
            {stillFile ? `Still · ${stillFile.name}` : "Optional singer still"}
          </div>
          {stillPreview && (
            <div className="studio-bin-item">
              <img className="studio-thumb" src={stillPreview} alt="Singer still" />
              <span>Identity still</span>
            </div>
          )}
          {timeline.map((scene) => (
            <button
              key={scene.id}
              type="button"
              className={`studio-bin-item ${selectedIndex === scene.index ? "selected" : ""}`}
              onClick={() => {
                setSelectedIndex(scene.index);
                setFinalReady(false);
              }}
              style={{ width: "100%", textAlign: "left", cursor: "pointer" }}
            >
              <div
                className="studio-thumb"
                style={{
                  background:
                    scene.status === "ready"
                      ? "#0f766e"
                      : scene.status === "failed"
                        ? "#7f1d1d"
                        : "#1e293b",
                }}
              />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>
                  {scene.type} {scene.index + 1}
                </div>
                <div style={{ color: "var(--studio-muted)", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {scene.status} · {formatTime(scene.start)}
                </div>
              </div>
            </button>
          ))}
        </aside>

        <section className="studio-monitor">
          <div className="studio-monitor-stage">
            {monitorSrc ? (
              <video ref={videoRef} key={monitorSrc} src={monitorSrc} controls playsInline />
            ) : (
              <div className="studio-monitor-empty">
                Import a track, write a direction, Plan scenes, then Render with MiniMax H3.
                Each lyric block becomes its own clip; the original MP3 is muxed at the end.
              </div>
            )}
          </div>
          <div className="studio-monitor-bar">
            <span>{status}</span>
            {error && <span style={{ color: "#fca5a5" }}>{error}</span>}
            <span style={{ marginLeft: "auto" }}>
              {selected
                ? `${selected.type} · ${formatTime(selected.start)}–${formatTime(selected.end)}`
                : formatTime(duration)}
            </span>
            {!h3Ready && comfyBackend !== "cloud" && (
              <span style={{ color: "var(--studio-warn)" }}>H3 weights missing</span>
            )}
          </div>
        </section>

        <aside className="studio-inspector">
          <div className="studio-section-label">Inspector</div>
          <label>Direction</label>
          <textarea
            className="studio-textarea"
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
            placeholder="West Coast neon MTV look, confident DJ energy…"
          />
          <label>Lyrics (optional override)</label>
          <textarea
            className="studio-textarea"
            style={{ minHeight: 72 }}
            value={lyrics}
            onChange={(e) => setLyrics(e.target.value)}
            placeholder="Paste official lyrics, or leave blank for Whisper"
          />
          {selected && (
            <>
              <div className="studio-meta">
                Scene {selected.index + 1} · {selected.type} · {formatTime(selected.duration)}
                <br />
                {selected.lyric || "(instrumental / no lyric window)"}
              </div>
              <label>Scene prompt</label>
              <textarea
                className="studio-textarea"
                value={selected.prompt}
                onChange={(e) => updateSelectedPrompt(e.target.value)}
              />
              <button
                className="studio-btn studio-btn-primary"
                style={{ width: "100%" }}
                disabled={busy}
                onClick={() => void handleRegenerate()}
              >
                Regenerate scene
              </button>
            </>
          )}
        </aside>
      </div>

      <footer className="studio-timeline">
        <div className="studio-ruler">
          {ticks.map((t) => (
            <span key={t} style={{ left: duration > 0 ? `${(t / duration) * 100}%` : 0 }}>
              {formatTime(t)}
            </span>
          ))}
        </div>
        <div className="studio-track">
          {timeline.map((scene) => {
            const left = duration > 0 ? (scene.start / duration) * 100 : 0;
            const width = duration > 0 ? (scene.duration / duration) * 100 : 8;
            return (
              <div
                key={scene.id}
                className={`studio-block ${scene.type} ${scene.status} ${
                  selectedIndex === scene.index ? "selected" : ""
                }`}
                style={{ left: `${left}%`, width: `${Math.max(width, 1.5)}%` }}
                onClick={() => {
                  setSelectedIndex(scene.index);
                  setFinalReady(false);
                }}
                title={scene.lyric || scene.prompt}
              >
                <div className="studio-block-title">
                  {scene.type} {scene.index + 1}
                </div>
                <div className="studio-block-sub">{scene.lyric || scene.status}</div>
              </div>
            );
          })}
          {!timeline.length && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "grid",
                placeItems: "center",
                color: "var(--studio-muted)",
                fontSize: 12,
              }}
            >
              Timeline empty — Plan to lay out H3 scenes across the song
            </div>
          )}
        </div>
      </footer>
    </div>
  );
}
