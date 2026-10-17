import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { useDropzone } from "react-dropzone";
import {
  Clapperboard,
  Cloud,
  Download,
  Image as ImageIcon,
  LayoutList,
  Music2,
  Pencil,
  Play,
  RefreshCw,
  RotateCcw,
  Server,
  Sparkles,
  X,
} from "lucide-react";
import { api, mediaUrl } from "../api";
import {
  BRAND,
  THEMES,
  applyTheme,
  getStoredTheme,
  type ThemeId,
} from "../brand";
import type { ComfyBackend, StudioScene, TimelinePlan } from "./types";
import {
  PIPELINE_PRESETS,
  comfyBackendFromVideo,
  needsComfyUrl,
  needsHomeAgent,
  type PipelineDirector,
  type PipelineLanes,
  type PipelinePresetId,
  type PipelineStills,
  type PipelineVideo,
} from "./pipeline";

interface Props {
  serverHealth: any;
}

type StudioPhase = "import" | "brief" | "review" | "rendering" | "done";

function formatTc(sec: number): string {
  const m = Math.floor(Math.max(0, sec) / 60);
  const s = Math.floor(Math.max(0, sec) % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function sceneBadge(scene: StudioScene): { label: string; kind: string } {
  if (scene.status === "failed") return { label: "failed", kind: "failed" };
  if (scene.status === "ready" || scene.video_url)
    return { label: "ready", kind: "ready" };
  if (scene.status === "rendering") return { label: "rendering", kind: "busy" };
  if (scene.still_url) return { label: "still", kind: "still" };
  return { label: "queued", kind: "queued" };
}

function RecentProjects() {
  const [projects, setProjects] = useState<
    Array<{ session_id: string; creative_direction?: string; has_final?: boolean }>
  >([]);
  useEffect(() => {
    api
      .listProjects()
      .then((res) => setProjects(res.projects || []))
      .catch(() => setProjects([]));
  }, []);
  if (!projects.length) return null;
  return (
    <div className="recent-projects">
      <p className="studio-label">Recent projects</p>
      <ul>
        {projects.slice(0, 6).map((p) => (
          <li key={p.session_id}>
            <span className="recent-id">{p.session_id.slice(0, 8)}</span>
            <span className="recent-dir">
              {(p.creative_direction || "Untitled").slice(0, 48)}
            </span>
            {p.has_final ? <span className="recent-ready">ready</span> : null}
          </li>
        ))}
      </ul>
      <p className="drop-hint">
        Resume: reopen the same session folder under outputs/ or re-upload and
        replan — full in-app resume loads next.
      </p>
    </div>
  );
}

export function StudioApp({ serverHealth }: Props) {
  const titleId = useId();
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [stillFile, setStillFile] = useState<File | null>(null);
  const [stillPreviewUrl, setStillPreviewUrl] = useState<string | null>(null);
  const [direction, setDirection] = useState("");
  const [performerLook, setPerformerLook] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [trackTitle, setTrackTitle] = useState("");
  const [duration, setDuration] = useState(0);
  const [comfyBackend, setComfyBackend] = useState<ComfyBackend>("cloud");
  const [comfyUrl, setComfyUrl] = useState(
    () => localStorage.getItem("scenemux-comfy-url") || "http://192.168.4.34:8188"
  );
  const [theme, setTheme] = useState<ThemeId>(() => getStoredTheme());
  const [pipelinePreset, setPipelinePreset] =
    useState<PipelinePresetId>("cloud");
  const [pipelineDirector, setPipelineDirector] =
    useState<PipelineDirector>("cloud_llm");
  const [pipelineStills, setPipelineStills] =
    useState<PipelineStills>("comfy");
  const [pipelineVideo, setPipelineVideo] =
    useState<PipelineVideo>("comfy_cloud");
  const [homeAgentUrl, setHomeAgentUrl] = useState(
    () => localStorage.getItem("scenemux-ha-url") || "http://192.168.4.34:8777"
  );
  const [homeAgentPassword, setHomeAgentPassword] = useState("");
  const [hermesUrl, setHermesUrl] = useState(
    () => localStorage.getItem("scenemux-hermes-url") || "http://127.0.0.1:8642"
  );
  const [hermesToken, setHermesToken] = useState("");
  const [haTesting, setHaTesting] = useState(false);
  const [haStatus, setHaStatus] = useState<string | null>(null);
  const [plan, setPlan] = useState<TimelinePlan | null>(null);
  const [planStale, setPlanStale] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [sessionReady, setSessionReady] = useState(false);
  const [phase, setPhase] = useState<StudioPhase>("import");
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [updatingStill, setUpdatingStill] = useState(false);
  const [status, setStatus] = useState("idle");
  const [progressLabel, setProgressLabel] = useState("");
  const [progressPct, setProgressPct] = useState(0);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [showFinal, setShowFinal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [renderFailed, setRenderFailed] = useState(false);

  const scenes = plan?.scenes || [];
  const selectedScene: StudioScene | null =
    selectedIndex != null
      ? scenes.find((s) => s.index === selectedIndex) || null
      : null;

  const hasProject = Boolean(audioFile);
  const readyCount = scenes.filter((s) => s.status === "ready").length;
  const failedCount = scenes.filter((s) => s.status === "failed").length;
  const remainingCount = scenes.filter(
    (s) => s.status !== "ready" || !s.video_url
  ).length;
  const stillCount = scenes.filter((s) => s.still_url).length;
  const identityUrl = plan?.identity_sheet_url
    ? mediaUrl(plan.identity_sheet_url)
    : null;
  const locked = busy || previewing || updatingStill;

  const lanes: PipelineLanes = useMemo(
    () => ({
      pipeline_director: pipelineDirector,
      pipeline_stills: pipelineStills,
      pipeline_video: pipelineVideo,
    }),
    [pipelineDirector, pipelineStills, pipelineVideo]
  );

  const h3Ready = useMemo(() => {
    const vm = serverHealth?.services?.comfyui?.video_models;
    if (!vm || comfyBackend === "cloud") return null;
    return Boolean(vm["minimax-h3"]?.ready);
  }, [serverHealth, comfyBackend]);

  const cloudOk = useMemo(() => {
    if (pipelineVideo !== "comfy_cloud") return true;
    const comfy = serverHealth?.services?.comfyui;
    return Boolean(
      comfy?.auth || comfy?.status === "up" || comfy?.status === "auth_required"
    );
  }, [serverHealth, pipelineVideo]);

  const applyPreset = useCallback((id: PipelinePresetId) => {
    setPipelinePreset(id);
    const preset = PIPELINE_PRESETS.find((p) => p.id === id);
    if (!preset?.lanes) return;
    setPipelineDirector(preset.lanes.pipeline_director);
    setPipelineStills(preset.lanes.pipeline_stills);
    setPipelineVideo(preset.lanes.pipeline_video);
    setComfyBackend(comfyBackendFromVideo(preset.lanes.pipeline_video));
  }, []);

  const sessionConfig = useCallback(
    () => ({
      video_backend: "minimax-h3" as const,
      lyrics,
      creative_direction: direction,
      project_mode: "mtv" as const,
      performance_mix: "performance" as const,
      comfy_backend: comfyBackendFromVideo(pipelineVideo),
      singer_profile: {
        look: performerLook.trim(),
        framing: "close-up on face and head",
      },
      ...(pipelineVideo === "comfy_cloud" && pipelineStills === "comfy"
        ? {}
        : { comfy_url: comfyUrl }),
      home_agent_url: homeAgentUrl.trim(),
      home_agent_password: homeAgentPassword,
      hermes_url: hermesUrl.trim(),
      hermes_token: hermesToken,
      pipeline_preset: pipelinePreset === "custom" ? undefined : pipelinePreset,
      pipeline_director: pipelineDirector,
      pipeline_stills: pipelineStills,
      pipeline_video: pipelineVideo,
      use_home_agent_llm: pipelineDirector === "home_agent",
      use_home_agent_stills: pipelineStills === "home_agent",
    }),
    [
      comfyUrl,
      direction,
      hermesToken,
      hermesUrl,
      homeAgentPassword,
      homeAgentUrl,
      lyrics,
      performerLook,
      pipelineDirector,
      pipelinePreset,
      pipelineStills,
      pipelineVideo,
    ]
  );

  const handleThemeChange = useCallback((next: ThemeId) => {
    setTheme(next);
    applyTheme(next);
  }, []);

  const handleDiagnoseHomeAgent = useCallback(async () => {
    setHaTesting(true);
    setHaStatus(null);
    setError(null);
    try {
      localStorage.setItem("scenemux-ha-url", homeAgentUrl.trim());
      const res = await api.diagnoseHomeAgent(homeAgentUrl.trim());
      if (res.tcp_ok) {
        setHaStatus(
          `TCP open to ${res.host}:${res.port} (${res.latency_ms ?? "?"} ms) — try Test with password`
        );
      } else {
        setHaStatus(null);
        setError(res.hint || res.error || "Home Agent port unreachable");
      }
    } catch (e) {
      setHaStatus(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setHaTesting(false);
    }
  }, [homeAgentUrl]);

  const handleTestHomeAgent = useCallback(async () => {
    setHaTesting(true);
    setHaStatus(null);
    setError(null);
    try {
      localStorage.setItem("scenemux-ha-url", homeAgentUrl.trim());
      const res = await api.testHomeAgent(
        homeAgentUrl.trim(),
        homeAgentPassword
      );
      setHaStatus(`Connected (${res.latency_ms ?? "?"} ms)`);
    } catch (e) {
      setHaStatus(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setHaTesting(false);
    }
  }, [homeAgentPassword, homeAgentUrl]);

  useEffect(() => {
    if (!stillFile) {
      setStillPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(stillFile);
    setStillPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [stillFile]);

  useEffect(() => {
    const offProgress = api.onProgressUpdate((data) => {
      setProgressPct(Number(data.progress) || 0);
      setProgressLabel(String(data.status || data.phase || ""));
    });
    const offStudio = api.onStudioEvent((data) => {
      if (data.type === "studio_complete") {
        if (data.timeline_plan) setPlan(data.timeline_plan);
        if (data.download_url) {
          setDownloadUrl(mediaUrl(data.download_url));
          setShowFinal(true);
        }
        setBusy(false);
        setRenderFailed(false);
        setPhase("done");
        setStatus("done");
        setProgressPct(100);
        setProgressLabel("Full cut ready");
      }
      if (data.type === "studio_error") {
        setBusy(false);
        setRenderFailed(true);
        setPhase("review");
        setStatus("failed");
        setError(String(data.error || "Studio render failed"));
        setProgressLabel("Render stopped — resume remaining scenes when ready");
      }
    });
    return () => {
      offProgress();
      offStudio();
    };
  }, []);

  useEffect(() => {
    if (phase !== "rendering" || !sessionReady) return;
    const id = window.setInterval(async () => {
      try {
        const state = await api.studioState();
        if (state.timeline_plan) setPlan(state.timeline_plan);
        if (state.download_url) setDownloadUrl(mediaUrl(state.download_url));
        if (state.status) setStatus(state.status);
        if (state.status === "done" || state.status === "studio_failed") {
          setBusy(false);
          if (state.status === "done") {
            setRenderFailed(false);
            setPhase("done");
            if (state.download_url) setShowFinal(true);
          } else {
            setRenderFailed(true);
            setPhase("review");
          }
          if (state.error) setError(String(state.error));
        }
      } catch {
        /* ignore */
      }
    }, 4000);
    return () => window.clearInterval(id);
  }, [phase, sessionReady]);

  const ensureSession = useCallback(async () => {
    if (api.getSessionId()) {
      setSessionReady(true);
      return api.getSessionId()!;
    }
    const sid = await api.createSession();
    setSessionReady(true);
    return sid;
  }, []);

  const guardEngine = useCallback(() => {
    if (pipelineVideo === "comfy_cloud" && !cloudOk) {
      setError(
        "Comfy Cloud needs COMFY_CLOUD_API_KEY in .env — then restart the API."
      );
      return false;
    }
    if (needsHomeAgent(lanes) && (!homeAgentUrl.trim() || !homeAgentPassword)) {
      setError("Home Agent URL and password are required for the selected lanes.");
      return false;
    }
    if (pipelineDirector === "hermes" && !hermesUrl.trim()) {
      setError("Hermes URL is required when Director is Hermes.");
      return false;
    }
    if (needsComfyUrl(lanes) && !comfyUrl.trim()) {
      setError("Comfy URL is required for Playground / local Comfy lanes.");
      return false;
    }
    if (pipelineVideo === "comfy_local" && h3Ready === false) {
      setError(
        "Local Comfy is missing MiniMax H3 weights. Switch to Cloud or install them."
      );
      return false;
    }
    return true;
  }, [
    cloudOk,
    comfyUrl,
    h3Ready,
    hermesUrl,
    homeAgentPassword,
    homeAgentUrl,
    lanes,
    pipelineDirector,
    pipelineVideo,
  ]);

  const importAudio = useCallback(
    async (file: File) => {
      setError(null);
      setBusy(true);
      setAudioFile(file);
      setTrackTitle(file.name.replace(/\.[^.]+$/, ""));
      setDownloadUrl(null);
      setPlan(null);
      setPlanStale(false);
      setRenderFailed(false);
      setShowFinal(false);
      setPhase("brief");
      try {
        await ensureSession();
        await api.configureSession(sessionConfig());
        setStatus("analyzing");
        setProgressLabel("Reading song…");
        const audioRes = await api.uploadAudio(file);
        const analysis = audioRes.analysis || {};
        setDuration(Number(analysis.duration) || 0);
        if (analysis.track_title) setTrackTitle(analysis.track_title);
        setStatus("ready");
        setProgressLabel("Add direction, then plan the storyboard");
        setProgressPct(0);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setStatus("error");
        setPhase("import");
      } finally {
        setBusy(false);
      }
    },
    [ensureSession, sessionConfig]
  );

  const audioDz = useDropzone({
    onDrop: (files) => files[0] && importAudio(files[0]),
    accept: { "audio/*": [".mp3", ".wav", ".m4a", ".ogg"] },
    multiple: false,
    noClick: Boolean(audioFile),
  });

  const stillDz = useDropzone({
    onDrop: async (files) => {
      if (!files.length) return;
      setStillFile(files[0]);
      try {
        await ensureSession();
        await api.uploadImages(files);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    accept: { "image/*": [".png", ".jpg", ".jpeg", ".webp"] },
    multiple: true,
  });

  const runPlanAndStills = useCallback(
    async (opts?: { resetIdentity?: boolean }) => {
      await ensureSession();
      await api.configureSession(sessionConfig());
      if (stillFile) await api.uploadImages([stillFile]);
      const planned = await api.studioPlan(direction, {
        lockIdentity: true,
        resetIdentity: opts?.resetIdentity ?? false,
      });
      if (planned.timeline_plan) {
        setPlan(planned.timeline_plan);
        setSelectedIndex(0);
      }
      setProgressLabel(
        pipelineStills === "home_agent"
          ? "Generating storyboard stills via Home Agent…"
          : "Generating storyboard stills (image credits)…"
      );
      setProgressPct(35);
      const stills = await api.studioStoryboardStills();
      if (stills.timeline_plan) {
        setPlan(stills.timeline_plan);
        setSelectedIndex(0);
      }
      setPlanStale(false);
      setRenderFailed(false);
      setDownloadUrl(null);
      setShowFinal(false);
      setPhase("review");
      setStatus("studio_review");
      setProgressLabel("Review storyboard — then preview or render");
      setProgressPct(100);
    },
    [direction, ensureSession, pipelineStills, sessionConfig, stillFile]
  );

  const handlePlanStoryboard = useCallback(async () => {
    if (!audioFile) {
      setError("Drop an MP3 to start");
      return;
    }
    if (!direction.trim()) {
      setError("Add a short creative direction before planning");
      return;
    }
    if (!guardEngine()) return;

    setError(null);
    setBusy(true);
    setStatus("studio_planning");
    setProgressPct(8);
    setProgressLabel("Planning scenes + locking performer…");
    setShowFinal(false);
    try {
      await runPlanAndStills({ resetIdentity: Boolean(plan) });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("failed");
      setPhase(plan ? "review" : "brief");
    } finally {
      setBusy(false);
    }
  }, [audioFile, direction, guardEngine, plan, runPlanAndStills]);

  const handleStartOver = useCallback(async () => {
    if (
      !window.confirm(
        "Start over? This clears the current song, storyboard, and renders."
      )
    ) {
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await api.resetSession();
      setSessionReady(true);
      setAudioFile(null);
      setStillFile(null);
      setDirection("");
      setPerformerLook("");
      setLyrics("");
      setTrackTitle("");
      setDuration(0);
      setPlan(null);
      setPlanStale(false);
      setSelectedIndex(null);
      setDownloadUrl(null);
      setShowFinal(false);
      setRenderFailed(false);
      setProgressLabel("");
      setProgressPct(0);
      setStatus("idle");
      setPhase("import");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const handleEditDirection = useCallback(() => {
    if (locked) return;
    setPhase("brief");
    if (plan) setPlanStale(true);
    setShowFinal(false);
    setProgressLabel(
      plan
        ? "Edit direction — replan storyboard to apply a new concept"
        : "Add direction, then plan the storyboard"
    );
  }, [locked, plan]);

  const handleReplanStoryboard = useCallback(async () => {
    if (!direction.trim()) {
      setError("Add a creative direction before replanning");
      setPhase("brief");
      return;
    }
    if (
      !window.confirm(
        "Replan storyboard? This rebuilds identity + stills and clears existing scene clips. Image credits will be used."
      )
    ) {
      return;
    }
    if (!guardEngine()) return;
    setError(null);
    setBusy(true);
    setStatus("studio_planning");
    setProgressPct(8);
    setProgressLabel("Replanning storyboard + new identity lock…");
    try {
      await runPlanAndStills({ resetIdentity: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("failed");
      setPhase("review");
    } finally {
      setBusy(false);
    }
  }, [direction, guardEngine, runPlanAndStills]);

  const syncPlanPrompts = useCallback(async () => {
    if (!plan?.scenes?.length) return;
    await api.studioUpdatePlan(
      plan.scenes.map((s) => ({
        index: s.index,
        prompt: s.prompt,
        type: s.type,
      }))
    );
  }, [plan]);

  const startFullRender = useCallback(async () => {
    if (!plan?.scenes?.length) {
      setError("Plan the storyboard first");
      return;
    }
    if (!direction.trim()) {
      setError("Creative direction required");
      return;
    }
    if (!guardEngine()) return;

    setError(null);
    setBusy(true);
    setRenderFailed(false);
    setPhase("rendering");
    setStatus("studio_rendering");
    setProgressPct(5);
    setProgressLabel(
      remainingCount > 0
        ? `Rendering ${remainingCount} remaining H3 scene(s)…`
        : "Assembling final cut…"
    );
    setShowFinal(false);
    try {
      await ensureSession();
      await api.configureSession(sessionConfig());
      if (stillFile) await api.uploadImages([stillFile]);
      await syncPlanPrompts();
      await api.studioRender(
        direction,
        false,
        plan.scenes.map((s) => ({ index: s.index, prompt: s.prompt }))
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
      setRenderFailed(true);
      setPhase("review");
      setStatus("failed");
    }
  }, [
    direction,
    ensureSession,
    guardEngine,
    plan,
    remainingCount,
    sessionConfig,
    stillFile,
    syncPlanPrompts,
  ]);

  const handleRenderFull = useCallback(async () => {
    if (!plan?.scenes?.length) {
      setError("Plan the storyboard first");
      return;
    }
    if (planStale) {
      setError("Direction changed — replan the storyboard before full render");
      return;
    }
    let estimateNote = "";
    try {
      const est = await api.estimateRender();
      estimateNote = `\n\nEstimate: ~${est.scenes_to_render} × ${est.per_scene} (${est.est_minutes_low}–${est.est_minutes_high} min). ${est.note || ""}`;
    } catch {
      /* optional */
    }
    const n = remainingCount;
    if (
      !window.confirm(
        n > 0
          ? `Render full video?\n\nAbout ${n} H3 job(s) for scenes that are not ready yet (ready scenes are skipped).${estimateNote}`
          : `All scenes already have clips. Reassemble the final cut?${estimateNote}`
      )
    ) {
      return;
    }
    await startFullRender();
  }, [plan, planStale, remainingCount, startFullRender]);

  const handleMakeVideo = useCallback(async () => {
    if (!audioFile) {
      setError("Drop an MP3 to start");
      return;
    }
    if (!direction.trim()) {
      setError("Add a short creative direction first");
      return;
    }
    if (!guardEngine()) return;
    if (
      !window.confirm(
        "Make video will plan the storyboard, generate stills, then ask you to confirm full H3 render. Continue?"
      )
    ) {
      return;
    }
    setError(null);
    setBusy(true);
    setStatus("studio_planning");
    setProgressPct(8);
    setProgressLabel("Make video — planning…");
    try {
      await runPlanAndStills({ resetIdentity: Boolean(plan) });
      setBusy(false);
      await handleRenderFull();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("failed");
      setPhase(plan ? "review" : "brief");
      setBusy(false);
    }
  }, [
    audioFile,
    direction,
    guardEngine,
    handleRenderFull,
    plan,
    runPlanAndStills,
  ]);

  const handleExportVertical = useCallback(async () => {
    if (!downloadUrl) {
      setError("Finish a full render before exporting vertical");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const res = await api.exportVertical();
      if (res.download_url) {
        window.open(mediaUrl(res.download_url), "_blank", "noopener,noreferrer");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [downloadUrl]);

  const handleResumeRemaining = useCallback(async () => {
    if (!plan?.scenes?.length) return;
    if (
      !window.confirm(
        `Resume remaining scenes?\n\nAbout ${remainingCount} H3 job(s) left. Ready clips will be kept.`
      )
    ) {
      return;
    }
    await startFullRender();
  }, [plan, remainingCount, startFullRender]);

  const handlePreviewScene = useCallback(async () => {
    if (selectedIndex == null || !selectedScene) {
      setError("Select a scene to preview");
      return;
    }
    if (!guardEngine()) return;
    setError(null);
    setPreviewing(true);
    setProgressLabel(`Previewing scene ${selectedIndex + 1} (1 H3 clip)…`);
    setProgressPct(15);
    try {
      await api.configureSession(sessionConfig());
      await syncPlanPrompts();
      const res = await api.studioRenderScene(selectedIndex, {
        prompt: selectedScene.prompt,
        direction,
        reassemble: false,
      });
      if (res.timeline_plan) setPlan(res.timeline_plan);
      setShowFinal(false);
      setProgressLabel("Preview clip ready — check lip sync & identity");
      setProgressPct(100);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewing(false);
    }
  }, [
    direction,
    guardEngine,
    selectedIndex,
    selectedScene,
    sessionConfig,
    syncPlanPrompts,
  ]);

  const handleRerenderClip = useCallback(async () => {
    if (selectedIndex == null || !selectedScene) return;
    if (!guardEngine()) return;
    setPreviewing(true);
    setError(null);
    setProgressLabel(`Re-rendering scene ${selectedIndex + 1} (1 H3 clip)…`);
    setProgressPct(20);
    try {
      await api.configureSession(sessionConfig());
      await syncPlanPrompts();
      const hasFinal = Boolean(downloadUrl) || phase === "done";
      const res = await api.studioRenderScene(selectedIndex, {
        prompt: selectedScene.prompt,
        direction,
        reassemble: hasFinal,
      });
      if (res.timeline_plan) setPlan(res.timeline_plan);
      if (res.download_url) {
        setDownloadUrl(mediaUrl(res.download_url));
        setPhase("done");
      }
      setShowFinal(false);
      setProgressLabel(
        hasFinal
          ? "Scene updated and final cut reassembled"
          : "Scene clip updated"
      );
      setProgressPct(100);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewing(false);
    }
  }, [
    direction,
    downloadUrl,
    guardEngine,
    phase,
    selectedIndex,
    selectedScene,
    sessionConfig,
    syncPlanPrompts,
  ]);

  const handleUpdateStill = useCallback(async () => {
    if (selectedIndex == null || !selectedScene) return;
    if (!guardEngine()) return;
    setUpdatingStill(true);
    setError(null);
    setProgressLabel(`Updating storyboard still for scene ${selectedIndex + 1}…`);
    setProgressPct(25);
    try {
      await api.configureSession(sessionConfig());
      await syncPlanPrompts();
      const stills = await api.studioStoryboardStills({
        sceneIndices: [selectedIndex],
      });
      if (stills.timeline_plan) setPlan(stills.timeline_plan);
      setShowFinal(false);
      setProgressLabel("Still updated (image credits)");
      setProgressPct(100);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUpdatingStill(false);
    }
  }, [
    guardEngine,
    selectedIndex,
    selectedScene,
    sessionConfig,
    syncPlanPrompts,
  ]);

  const goToStep = useCallback(
    (step: "song" | "direction" | "review") => {
      if (locked || phase === "rendering") return;
      if (step === "song") {
        void handleStartOver();
        return;
      }
      if (step === "direction") {
        handleEditDirection();
        return;
      }
      if (step === "review") {
        if (!plan?.scenes?.length) {
          setError("Plan the storyboard before opening review");
          return;
        }
        setPhase("review");
        setShowFinal(false);
      }
    },
    [handleEditDirection, handleStartOver, locked, phase, plan]
  );

  const previewSrc =
    showFinal && downloadUrl
      ? downloadUrl
      : selectedScene?.video_url
        ? mediaUrl(selectedScene.video_url)
        : selectedScene?.still_url
          ? mediaUrl(selectedScene.still_url)
          : "";

  const previewIsVideo = Boolean(
    (showFinal && downloadUrl) || selectedScene?.video_url
  );

  const canPlan =
    Boolean(audioFile) &&
    Boolean(direction.trim()) &&
    !locked &&
    (pipelineVideo === "comfy_cloud"
      ? cloudOk
      : pipelineVideo === "home_agent" || h3Ready !== false);

  const canRenderFull =
    Boolean(plan?.scenes?.length) &&
    !locked &&
    phase !== "rendering" &&
    !planStale;

  return (
    <div className="studio-shell">
      <a className="skip-link" href="#workspace">
        Skip to workspace
      </a>

      <header className="studio-top">
        <div className="studio-top-inner">
          <div className="brand-lockup">
            <span className="brand-mark" aria-hidden="true">
              {BRAND.mark}
            </span>
            <div>
              <p className="brand-name">{BRAND.name}</p>
              <p className="brand-sub">{BRAND.tagline}</p>
            </div>
          </div>

          <div className="header-controls">
            <div className="engine-toggle" role="group" aria-label="Theme">
              {THEMES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="studio-chip"
                  aria-pressed={theme === t.id}
                  onClick={() => handleThemeChange(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="engine-toggle" role="group" aria-label="Pipeline preset">
              {PIPELINE_PRESETS.filter((p) => p.id !== "custom").map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className="studio-chip"
                  aria-pressed={pipelinePreset === p.id}
                  title={p.hint}
                  onClick={() => applyPreset(p.id)}
                >
                  {p.id === "cloud" ? (
                    <Cloud className="w-3.5 h-3.5" aria-hidden="true" />
                  ) : (
                    <Server className="w-3.5 h-3.5" aria-hidden="true" />
                  )}
                  {p.label}
                </button>
              ))}
              <button
                type="button"
                className="studio-chip"
                aria-pressed={pipelinePreset === "custom"}
                onClick={() => setPipelinePreset("custom")}
              >
                Custom
              </button>
            </div>
          </div>
        </div>
      </header>

      {error && (
        <div className="studio-banner" role="alert">
          <p>{error}</p>
          <button
            type="button"
            className="banner-dismiss"
            onClick={() => setError(null)}
            aria-label="Dismiss error"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      <main id="workspace" className="studio-center" aria-labelledby={titleId}>
        {!hasProject ? (
          <section className="hero-card studio-panel">
            <h1 id={titleId} className="hero-title">
              {BRAND.subtitle}
            </h1>
            <p className="hero-copy">
              Drop a song, lock a look, review the storyboard, then spend H3
              credits only when you approve. Use pipeline presets for Cloud,
              Playground Comfy, Home Agent, or hybrid.
            </p>

            <div
              {...audioDz.getRootProps()}
              className={`drop-zone ${audioDz.isDragActive ? "active" : ""}`}
            >
              <input {...audioDz.getInputProps()} />
              <Music2 className="drop-icon" aria-hidden="true" />
              <p className="drop-title">Drop your MP3 here</p>
              <p className="drop-hint">or click to choose a file</p>
            </div>
            <RecentProjects />
          </section>
        ) : (
          <div className="project-stack">
            <section className="project-head">
              <div>
                <h1 id={titleId} className="project-title">
                  {trackTitle || "Untitled"}
                </h1>
                <p className="project-meta">
                  {duration > 0 ? `${formatTc(duration)} · ` : ""}
                  {phase === "brief" &&
                    (planStale
                      ? "Direction edited — replan to apply"
                      : "Plan storyboard first")}
                  {phase === "review" &&
                    `Review · ${stillCount}/${scenes.length || 0} stills · ${readyCount} clips`}
                  {phase === "rendering" && "Rendering full video…"}
                  {phase === "done" &&
                    `Done · ${readyCount}/${scenes.length} scenes`}
                  {failedCount > 0 ? ` · ${failedCount} failed` : ""}
                </p>
              </div>
              <div className="project-actions">
                {downloadUrl && (
                  <>
                    <button
                      type="button"
                      className="studio-btn studio-btn-ghost"
                      onClick={() =>
                        window.open(downloadUrl, "_blank", "noopener,noreferrer")
                      }
                    >
                      <Download className="w-4 h-4" aria-hidden="true" />
                      Export
                    </button>
                    <button
                      type="button"
                      className="studio-btn studio-btn-ghost"
                      disabled={locked}
                      onClick={handleExportVertical}
                    >
                      9:16
                    </button>
                  </>
                )}
                {(phase === "brief" || !plan) && (
                  <>
                    <button
                      type="button"
                      className="studio-btn studio-btn-ghost"
                      disabled={!canPlan}
                      onClick={handlePlanStoryboard}
                      aria-busy={busy}
                    >
                      <LayoutList className="w-4 h-4" aria-hidden="true" />
                      {busy ? "Planning…" : plan ? "Replan storyboard" : "Plan storyboard"}
                    </button>
                    <button
                      type="button"
                      className="studio-btn studio-btn-primary"
                      disabled={!canPlan}
                      onClick={handleMakeVideo}
                      aria-busy={busy}
                    >
                      <Clapperboard className="w-4 h-4" aria-hidden="true" />
                      Make video
                    </button>
                  </>
                )}
                {(phase === "review" || phase === "done") && (
                  <>
                    <button
                      type="button"
                      className="studio-btn studio-btn-ghost"
                      disabled={locked || selectedIndex == null}
                      onClick={handlePreviewScene}
                    >
                      <Sparkles className="w-4 h-4" aria-hidden="true" />
                      {previewing ? "Previewing…" : "Preview scene"}
                    </button>
                    <button
                      type="button"
                      className="studio-btn studio-btn-primary"
                      disabled={!canRenderFull}
                      onClick={handleRenderFull}
                    >
                      <Play className="w-4 h-4" aria-hidden="true" />
                      {remainingCount < scenes.length && remainingCount > 0
                        ? `Render remaining (${remainingCount})`
                        : "Render full video"}
                    </button>
                  </>
                )}
                {phase === "done" && (
                  <button
                    type="button"
                    className="studio-btn studio-btn-soft"
                    disabled={locked}
                    onClick={() => {
                      setPhase("review");
                      setShowFinal(false);
                    }}
                  >
                    <Clapperboard className="w-4 h-4" aria-hidden="true" />
                    Back to review
                  </button>
                )}
              </div>
            </section>

            <div className="project-menu" role="toolbar" aria-label="Project">
              <button
                type="button"
                className="studio-btn studio-btn-ghost"
                disabled={locked}
                onClick={handleStartOver}
              >
                <RotateCcw className="w-3.5 h-3.5" aria-hidden="true" />
                Start over
              </button>
              <button
                type="button"
                className="studio-btn studio-btn-ghost"
                disabled={locked || phase === "rendering"}
                onClick={handleEditDirection}
              >
                <Pencil className="w-3.5 h-3.5" aria-hidden="true" />
                Edit direction
              </button>
              <button
                type="button"
                className="studio-btn studio-btn-ghost"
                disabled={locked || !direction.trim()}
                onClick={handleReplanStoryboard}
              >
                <LayoutList className="w-3.5 h-3.5" aria-hidden="true" />
                Replan storyboard
              </button>
            </div>

            <section className="ha-row studio-panel" aria-label="Pipeline routing">
              <div className="ha-row-head">
                <h2 className="studio-label !mb-0">Pipeline routing</h2>
                <p className="brand-sub">
                  {PIPELINE_PRESETS.find((p) => p.id === pipelinePreset)?.hint ||
                    "Custom lanes"}
                </p>
              </div>
              <p className="identity-hint">
                Choose who runs director chat, storyboard stills, and H3 video.
                SceneMux always slices audio and stitches the final cut.
              </p>

              <div className="pipeline-lanes">
                <label className="pipeline-lane">
                  <span>Director</span>
                  <select
                    className="studio-field"
                    value={pipelineDirector}
                    onChange={(e) => {
                      setPipelinePreset("custom");
                      setPipelineDirector(e.target.value as PipelineDirector);
                    }}
                  >
                    <option value="cloud_llm">Cloud LLM</option>
                    <option value="home_agent">Home Agent</option>
                    <option value="hermes">Hermes Agent</option>
                  </select>
                </label>
                <label className="pipeline-lane">
                  <span>Stills</span>
                  <select
                    className="studio-field"
                    value={pipelineStills}
                    onChange={(e) => {
                      setPipelinePreset("custom");
                      setPipelineStills(e.target.value as PipelineStills);
                    }}
                  >
                    <option value="comfy">Comfy</option>
                    <option value="home_agent">Home Agent</option>
                  </select>
                </label>
                <label className="pipeline-lane">
                  <span>Video (H3)</span>
                  <select
                    className="studio-field"
                    value={pipelineVideo}
                    onChange={(e) => {
                      const next = e.target.value as PipelineVideo;
                      setPipelinePreset("custom");
                      setPipelineVideo(next);
                      setComfyBackend(comfyBackendFromVideo(next));
                    }}
                  >
                    <option value="comfy_cloud">Comfy Cloud</option>
                    <option value="comfy_local">Playground / LAN Comfy</option>
                    <option value="home_agent">Home Agent</option>
                  </select>
                </label>
              </div>

              {(needsComfyUrl(lanes) ||
                pipelineStills === "comfy" ||
                pipelineVideo === "comfy_local") && (
                <input
                  className="studio-field"
                  value={comfyUrl}
                  onChange={(e) => {
                    setComfyUrl(e.target.value);
                    localStorage.setItem("scenemux-comfy-url", e.target.value);
                  }}
                  aria-label="Comfy URL"
                  placeholder="http://192.168.4.34:8188"
                />
              )}

              {(needsHomeAgent(lanes) || pipelinePreset !== "cloud") && (
                <div className="ha-fields">
                  <input
                    className="studio-field"
                    value={homeAgentUrl}
                    onChange={(e) => setHomeAgentUrl(e.target.value)}
                    aria-label="Home Agent URL"
                    placeholder="http://192.168.4.34:8777"
                  />
                  <input
                    className="studio-field"
                    type="password"
                    value={homeAgentPassword}
                    onChange={(e) => setHomeAgentPassword(e.target.value)}
                    aria-label="Home Agent password"
                    placeholder="LAN password"
                    autoComplete="off"
                  />
                  <button
                    type="button"
                    className="studio-btn studio-btn-ghost"
                    disabled={haTesting || !homeAgentUrl.trim()}
                    onClick={handleDiagnoseHomeAgent}
                  >
                    {haTesting ? "Checking…" : "Diagnose"}
                  </button>
                  <button
                    type="button"
                    className="studio-btn studio-btn-ghost"
                    disabled={
                      haTesting || !homeAgentUrl.trim() || !homeAgentPassword
                    }
                    onClick={handleTestHomeAgent}
                  >
                    {haTesting ? "Testing…" : "Test"}
                  </button>
                </div>
              )}

              {pipelineDirector === "hermes" && (
                <div className="ha-fields">
                  <input
                    className="studio-field"
                    value={hermesUrl}
                    onChange={(e) => {
                      setHermesUrl(e.target.value);
                      localStorage.setItem("scenemux-hermes-url", e.target.value);
                    }}
                    aria-label="Hermes URL"
                    placeholder="http://127.0.0.1:8642"
                  />
                  <input
                    className="studio-field"
                    type="password"
                    value={hermesToken}
                    onChange={(e) => setHermesToken(e.target.value)}
                    aria-label="Hermes token"
                    placeholder="Optional token"
                    autoComplete="off"
                  />
                </div>
              )}
              {haStatus && <p className="ha-status">{haStatus}</p>}
            </section>

            <ol className="step-rail" aria-label="Workflow steps">
              <li>
                <button
                  type="button"
                  className={`step-btn ${phase !== "import" ? "done" : "active"}`}
                  disabled={locked || phase === "rendering"}
                  onClick={() => goToStep("song")}
                >
                  1. Song
                </button>
              </li>
              <li>
                <button
                  type="button"
                  className={`step-btn ${
                    phase === "brief"
                      ? "active"
                      : ["review", "rendering", "done"].includes(phase)
                        ? "done"
                        : ""
                  }`}
                  disabled={locked || phase === "rendering"}
                  onClick={() => goToStep("direction")}
                >
                  2. Direction
                </button>
              </li>
              <li>
                <button
                  type="button"
                  className={`step-btn ${
                    phase === "review"
                      ? "active"
                      : ["rendering", "done"].includes(phase)
                        ? "done"
                        : ""
                  }`}
                  disabled={locked || phase === "rendering" || !plan?.scenes?.length}
                  onClick={() => goToStep("review")}
                >
                  3. Review
                </button>
              </li>
              <li>
                <span
                  className={`step-btn static ${
                    phase === "rendering"
                      ? "active"
                      : phase === "done"
                        ? "done"
                        : ""
                  }`}
                >
                  4. Render
                </span>
              </li>
            </ol>

            {planStale && phase === "brief" && (
              <p className="credit-note warn">
                Direction edits are staged. Replan storyboard to rebuild identity
                + stills (image credits). Existing H3 clips will be cleared.
              </p>
            )}

            {renderFailed && phase === "review" && remainingCount > 0 && (
              <div className="resume-banner" role="status">
                <div>
                  <p className="resume-title">Full render interrupted</p>
                  <p className="resume-copy">
                    {readyCount} scene(s) ready · {remainingCount} left · failed
                    clips stay skipped until you retry.
                  </p>
                </div>
                <button
                  type="button"
                  className="studio-btn studio-btn-primary"
                  disabled={locked}
                  onClick={handleResumeRemaining}
                >
                  Resume remaining ({remainingCount})
                </button>
              </div>
            )}

            {(busy || previewing || updatingStill || progressPct > 0) && (
              <div className="progress-block" role="status" aria-live="polite">
                <div className="progress-row">
                  <span>{progressLabel || "Working…"}</span>
                  <span className="font-mono">{Math.round(progressPct)}%</span>
                </div>
                <div
                  className="progress-track"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(progressPct)}
                >
                  <div
                    className="progress-fill"
                    style={{ width: `${Math.min(100, progressPct)}%` }}
                  />
                </div>
              </div>
            )}

            {(phase === "review" || phase === "rendering" || phase === "done") &&
              (identityUrl || plan?.character_lock) && (
                <section className="identity-card studio-panel">
                  <div className="identity-copy">
                    <h2 className="studio-label !mb-0">Locked performer</h2>
                    <p className="identity-lock">
                      {plan?.character_lock ||
                        plan?.character ||
                        "Identity sheet will keep the same face across scenes."}
                    </p>
                    <p className="identity-hint">
                      Without a photo upload we invent one locked look from your
                      direction / look notes, then reuse it for every H3 clip.
                    </p>
                  </div>
                  {identityUrl && (
                    <img
                      src={identityUrl}
                      alt="Locked performer identity"
                      className="identity-thumb"
                    />
                  )}
                </section>
              )}

            {(phase === "review" ||
              phase === "rendering" ||
              phase === "done") && (
              <section className="monitor-card studio-panel">
                <div className="monitor-toolbar">
                  <h2 className="studio-label !mb-0">
                    {previewIsVideo ? "Preview" : "Storyboard frame"}
                  </h2>
                  {downloadUrl && (
                    <div
                      className="segmented"
                      role="group"
                      aria-label="Preview source"
                    >
                      <button
                        type="button"
                        aria-pressed={!showFinal}
                        onClick={() => setShowFinal(false)}
                      >
                        Scene
                      </button>
                      <button
                        type="button"
                        aria-pressed={showFinal}
                        onClick={() => setShowFinal(true)}
                      >
                        Final
                      </button>
                    </div>
                  )}
                </div>
                <div className="monitor-frame">
                  {previewSrc ? (
                    previewIsVideo ? (
                      <video
                        key={previewSrc}
                        src={previewSrc}
                        controls
                        className="monitor-video"
                        aria-label={
                          showFinal
                            ? "Final music video"
                            : "Selected scene preview"
                        }
                      />
                    ) : (
                      <img
                        src={previewSrc}
                        alt="Storyboard still"
                        className="monitor-video"
                      />
                    )
                  ) : (
                    <div className="monitor-empty">
                      <p>
                        Plan the storyboard to see stills before spending H3
                        credits.
                      </p>
                    </div>
                  )}
                </div>
              </section>
            )}

            {scenes.length > 0 && (
              <section className="scene-strip" aria-label="Scenes">
                <div className="scene-strip-head">
                  <h2 className="studio-label !mb-0">Scenes</h2>
                  <span className="font-mono text-xs text-[var(--muted)]">
                    {formatTc(0)}–{formatTc(duration || plan?.duration || 0)}
                  </span>
                </div>
                <div
                  className="scene-rail"
                  role="listbox"
                  aria-label="Scene list"
                >
                  {scenes.map((scene) => {
                    const selected = selectedIndex === scene.index;
                    const badge = sceneBadge(scene);
                    return (
                      <button
                        key={scene.index}
                        type="button"
                        role="option"
                        aria-selected={selected}
                        className={`scene-card ${selected ? "selected" : ""} status-${scene.status}`}
                        onClick={() => setSelectedIndex(scene.index)}
                      >
                        {scene.still_url ? (
                          <img
                            src={mediaUrl(scene.still_url)}
                            alt=""
                            className="scene-thumb"
                          />
                        ) : null}
                        <span className={`scene-badge kind-${badge.kind}`}>
                          {badge.label}
                        </span>
                        <span className="scene-index">S{scene.index + 1}</span>
                        <span className="scene-type">{scene.type}</span>
                        <span className="scene-time font-mono">
                          {formatTc(scene.start)}
                        </span>
                        <span className="scene-lyric">
                          {scene.lyric || "Instrumental"}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>
            )}

            <section className="controls-grid">
              <label className="control-block studio-panel">
                <span className="studio-label">Direction</span>
                <textarea
                  className="studio-field"
                  rows={4}
                  value={direction}
                  onChange={(e) => {
                    setDirection(e.target.value);
                    if (plan) setPlanStale(true);
                  }}
                  placeholder="Neon West Coast MTV energy, golden-hour LA, confident performance, no on-screen text…"
                />
              </label>

              <div className="control-block studio-panel">
                <span className="studio-label">References</span>
                <div className="ref-row">
                  <div className="ref-chip">
                    <Music2 className="w-4 h-4" aria-hidden="true" />
                    <span className="truncate">{audioFile?.name}</span>
                    <button
                      type="button"
                      className="ref-clear"
                      onClick={handleStartOver}
                      aria-label="Start over"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <div
                    {...stillDz.getRootProps()}
                    className="ref-chip clickable"
                  >
                    <input {...stillDz.getInputProps()} />
                    <ImageIcon className="w-4 h-4" aria-hidden="true" />
                    <span className="truncate">
                      {stillFile?.name || "Add singer still (optional)"}
                    </span>
                    {stillFile && (
                      <button
                        type="button"
                        className="ref-clear"
                        onClick={(e) => {
                          e.stopPropagation();
                          setStillFile(null);
                        }}
                        aria-label="Remove still"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </div>
                {stillPreviewUrl && (
                  <img
                    src={stillPreviewUrl}
                    alt="Singer still preview"
                    className="still-thumb"
                  />
                )}
                <label className="block mt-3">
                  <span className="studio-label">Performer look</span>
                  <textarea
                    className="studio-field"
                    rows={2}
                    value={performerLook}
                    onChange={(e) => setPerformerLook(e.target.value)}
                    placeholder="If no photo: e.g. Black woman, mid-20s, gold hoop earrings, braided bun, warm brown skin…"
                  />
                </label>
                <label className="block mt-3">
                  <span className="studio-label">Lyrics override</span>
                  <textarea
                    className="studio-field"
                    rows={2}
                    value={lyrics}
                    onChange={(e) => setLyrics(e.target.value)}
                    placeholder="Optional — auto-extracted if blank"
                  />
                </label>
              </div>
            </section>

            {selectedScene && (phase === "review" || phase === "done") && (
              <section className="control-block studio-panel">
                <div className="scene-tools-head">
                  <h2 className="text-sm font-semibold">
                    Scene {selectedScene.index + 1} prompt
                  </h2>
                  <div className="scene-tools">
                    <button
                      type="button"
                      className="studio-btn studio-btn-ghost"
                      disabled={locked}
                      onClick={handleUpdateStill}
                    >
                      <ImageIcon
                        className={`w-3.5 h-3.5 ${updatingStill ? "animate-spin" : ""}`}
                        aria-hidden="true"
                      />
                      {updatingStill ? "Updating…" : "Update still"}
                    </button>
                    <button
                      type="button"
                      className="studio-btn studio-btn-soft"
                      disabled={locked}
                      onClick={handleRerenderClip}
                    >
                      <RefreshCw
                        className={`w-3.5 h-3.5 ${previewing ? "animate-spin" : ""}`}
                        aria-hidden="true"
                      />
                      {previewing ? "Working…" : "Re-render clip"}
                    </button>
                  </div>
                </div>
                <textarea
                  className="studio-field"
                  rows={5}
                  value={selectedScene.prompt}
                  onChange={(e) => {
                    if (!plan) return;
                    setPlan({
                      ...plan,
                      scenes: plan.scenes.map((s) =>
                        s.index === selectedIndex
                          ? { ...s, prompt: e.target.value }
                          : s
                      ),
                    });
                  }}
                />
                <p className="credit-note">
                  Update still = image credits. Re-render clip / Preview scene =
                  1 H3 video job. Render full = only remaining queued scenes.
                </p>
              </section>
            )}

            {phase === "brief" && (
              <p className="credit-note">
                Plan storyboard = identity lock + still previews (image credits).
                Preview one scene to check lip sync before rendering the full cut
                (H3 video credits).
              </p>
            )}

            {(phase === "review" || phase === "done") && (
              <p className="credit-note">
                Full render confirm shows how many H3 jobs remain. Ready scenes
                are skipped. Use Start over for a new song, or Replan storyboard
                to keep this track with a new concept.
              </p>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
