/** Studio pipeline lane presets and types. */

export type PipelineDirector = "cloud_llm" | "home_agent" | "hermes";
export type PipelineStills = "comfy" | "home_agent" | "fal";
export type PipelineVideo =
  | "comfy_cloud"
  | "comfy_local"
  | "home_agent"
  | "fal";
export type VideoModel =
  | "minimax-h3"
  | "ltx23"
  | "fasth3"
  | "hybrid_ltx"
  | "hybrid_fast";
export type PipelinePresetId =
  | "cloud"
  | "playground"
  | "playground_agent"
  | "hybrid"
  | "hybrid_fast"
  | "fast_draft"
  | "custom";

export type PipelineLanes = {
  pipeline_director: PipelineDirector;
  pipeline_stills: PipelineStills;
  pipeline_video: PipelineVideo;
  video_model: VideoModel;
};

export const VIDEO_MODEL_OPTIONS: Array<{
  id: VideoModel;
  label: string;
  hint: string;
}> = [
  {
    id: "minimax-h3",
    label: "H3 Ref2VA",
    hint: "True phoneme lip sync — use for PERF / quality",
  },
  {
    id: "fasth3",
    label: "FastH3",
    hint: "8-step I2V/T2V drafts only — no Ref2VA",
  },
  {
    id: "ltx23",
    label: "LTX-2.3",
    hint: "IA2V audio-conditioned drafts — faster, not Ref2VA",
  },
  {
    id: "hybrid_fast",
    label: "Hybrid Fast",
    hint: "PERF → H3 Ref2VA; other scenes → FastH3",
  },
  {
    id: "hybrid_ltx",
    label: "Hybrid LTX",
    hint: "PERF → H3 Ref2VA; B-roll → LTX IA2V",
  },
];

export const PIPELINE_PRESETS: Array<{
  id: PipelinePresetId;
  label: string;
  hint: string;
  simple?: boolean;
  lanes?: PipelineLanes;
}> = [
  {
    id: "cloud",
    label: "Cloud",
    hint: "Comfy Cloud stills + H3 Ref2VA; cloud LLM",
    simple: true,
    lanes: {
      pipeline_director: "cloud_llm",
      pipeline_stills: "comfy",
      pipeline_video: "comfy_cloud",
      video_model: "minimax-h3",
    },
  },
  {
    id: "playground",
    label: "Playground",
    hint: "HA director; local Comfy LTX drafts",
    simple: true,
    lanes: {
      pipeline_director: "home_agent",
      pipeline_stills: "comfy",
      pipeline_video: "comfy_local",
      video_model: "ltx23",
    },
  },
  {
    id: "hybrid_fast",
    label: "Hybrid Fast",
    hint: "PERF on H3 Ref2VA; other scenes FastH3",
    simple: true,
    lanes: {
      pipeline_director: "home_agent",
      pipeline_stills: "comfy",
      pipeline_video: "comfy_local",
      video_model: "hybrid_fast",
    },
  },
  {
    id: "fast_draft",
    label: "Fast Draft",
    hint: "All scenes FastH3 8-step — iteration only",
    simple: true,
    lanes: {
      pipeline_director: "cloud_llm",
      pipeline_stills: "comfy",
      pipeline_video: "comfy_local",
      video_model: "fasth3",
    },
  },
  {
    id: "playground_agent",
    label: "Playground Agent",
    hint: "Home Agent for LLM, stills, and H3",
    lanes: {
      pipeline_director: "home_agent",
      pipeline_stills: "home_agent",
      pipeline_video: "home_agent",
      video_model: "minimax-h3",
    },
  },
  {
    id: "hybrid",
    label: "Hybrid",
    hint: "HA plans + stills; Playground Comfy H3",
    lanes: {
      pipeline_director: "home_agent",
      pipeline_stills: "home_agent",
      pipeline_video: "comfy_local",
      video_model: "minimax-h3",
    },
  },
  {
    id: "custom",
    label: "Custom",
    hint: "Pick each lane + engine",
    simple: true,
  },
];

export function needsHomeAgent(lanes: PipelineLanes): boolean {
  return (
    lanes.pipeline_director === "home_agent" ||
    lanes.pipeline_stills === "home_agent" ||
    lanes.pipeline_video === "home_agent"
  );
}

export function needsComfyUrl(lanes: PipelineLanes): boolean {
  return (
    lanes.pipeline_video === "comfy_local" ||
    (lanes.pipeline_stills === "comfy" &&
      lanes.pipeline_video !== "comfy_cloud" &&
      lanes.pipeline_video !== "fal")
  );
}

export function needsFalKey(lanes: PipelineLanes): boolean {
  return lanes.pipeline_video === "fal" || lanes.pipeline_stills === "fal";
}

export function comfyBackendFromVideo(
  video: PipelineVideo
): "cloud" | "local" {
  return video === "comfy_cloud" ? "cloud" : "local";
}
