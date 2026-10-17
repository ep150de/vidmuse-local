/** Studio pipeline lane presets and types. */

export type PipelineDirector = "cloud_llm" | "home_agent" | "hermes";
export type PipelineStills = "comfy" | "home_agent";
export type PipelineVideo = "comfy_cloud" | "comfy_local" | "home_agent";
export type PipelinePresetId =
  | "cloud"
  | "playground"
  | "playground_agent"
  | "hybrid"
  | "custom";

export type PipelineLanes = {
  pipeline_director: PipelineDirector;
  pipeline_stills: PipelineStills;
  pipeline_video: PipelineVideo;
};

export const PIPELINE_PRESETS: Array<{
  id: PipelinePresetId;
  label: string;
  hint: string;
  lanes?: PipelineLanes;
}> = [
  {
    id: "cloud",
    label: "Cloud",
    hint: "Comfy Cloud stills + H3; cloud LLM",
    lanes: {
      pipeline_director: "cloud_llm",
      pipeline_stills: "comfy",
      pipeline_video: "comfy_cloud",
    },
  },
  {
    id: "playground",
    label: "Playground",
    hint: "HA director; Playground Comfy stills + H3",
    lanes: {
      pipeline_director: "home_agent",
      pipeline_stills: "comfy",
      pipeline_video: "comfy_local",
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
    },
  },
  {
    id: "custom",
    label: "Custom",
    hint: "Pick each lane",
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
    (lanes.pipeline_stills === "comfy" && lanes.pipeline_video !== "comfy_cloud")
  );
}

export function comfyBackendFromVideo(
  video: PipelineVideo
): "cloud" | "local" {
  return video === "comfy_cloud" ? "cloud" : "local";
}
