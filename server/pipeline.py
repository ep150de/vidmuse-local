"""Studio pipeline lane helpers: director / stills / video routing."""

from __future__ import annotations

from typing import Any, Dict, Optional

DIRECTOR_CHOICES = ("cloud_llm", "home_agent", "hermes")
STILLS_CHOICES = ("comfy", "home_agent", "fal")
VIDEO_CHOICES = ("comfy_cloud", "comfy_local", "home_agent", "fal")
VIDEO_MODEL_CHOICES = (
    "minimax-h3",
    "ltx23",
    "fasth3",
    "hybrid_ltx",
    "hybrid_fast",
)

PRESETS: Dict[str, Dict[str, str]] = {
    "cloud": {
        "pipeline_director": "cloud_llm",
        "pipeline_stills": "comfy",
        "pipeline_video": "comfy_cloud",
        "video_model": "minimax-h3",
    },
    "playground": {
        "pipeline_director": "home_agent",
        "pipeline_stills": "comfy",
        "pipeline_video": "comfy_local",
        "video_model": "ltx23",
    },
    "playground_agent": {
        "pipeline_director": "home_agent",
        "pipeline_stills": "home_agent",
        "pipeline_video": "home_agent",
        "video_model": "minimax-h3",
    },
    "hybrid": {
        "pipeline_director": "home_agent",
        "pipeline_stills": "home_agent",
        "pipeline_video": "comfy_local",
        "video_model": "minimax-h3",
    },
    "hybrid_fast": {
        "pipeline_director": "home_agent",
        "pipeline_stills": "comfy",
        "pipeline_video": "comfy_local",
        "video_model": "hybrid_fast",
    },
    "fast_draft": {
        "pipeline_director": "cloud_llm",
        "pipeline_stills": "comfy",
        "pipeline_video": "comfy_local",
        "video_model": "fasth3",
    },
}


def normalize_video_model(raw: Any) -> str:
    vm = str(raw or "minimax-h3").strip().lower().replace(" ", "_")
    aliases = {
        "minimax-h3": "minimax-h3",
        "minimax_h3": "minimax-h3",
        "minimaxh3": "minimax-h3",
        "h3": "minimax-h3",
        "ref2va": "minimax-h3",
        "ltx23": "ltx23",
        "ltx-23": "ltx23",
        "ltx_23": "ltx23",
        "ltx": "ltx23",
        "fasth3": "fasth3",
        "fast-h3": "fasth3",
        "fast_h3": "fasth3",
        "fastvideo": "fasth3",
        "hybrid_ltx": "hybrid_ltx",
        "hybrid-ltx": "hybrid_ltx",
        "hybridltx": "hybrid_ltx",
        "hybrid_fast": "hybrid_fast",
        "hybrid-fast": "hybrid_fast",
        "hybridfast": "hybrid_fast",
    }
    mapped = aliases.get(vm, vm)
    if mapped not in VIDEO_MODEL_CHOICES:
        return "minimax-h3"
    return mapped


def is_perf_scene(scene_type: Any) -> bool:
    stype = str(scene_type or "").strip().upper()
    return stype in ("PERF", "PERFORMANCE") or stype.startswith("PERF")


def resolve_scene_engine(
    video_model: str,
    scene_type: Any = None,
    *,
    has_image: bool = True,
) -> str:
    """Pick concrete engine for one scene (no hybrid wrappers)."""
    vm = normalize_video_model(video_model)
    if vm == "hybrid_ltx":
        engine = "minimax-h3" if is_perf_scene(scene_type) else "ltx23"
    elif vm == "hybrid_fast":
        engine = "minimax-h3" if is_perf_scene(scene_type) else "fasth3"
    else:
        engine = vm

    # LTX IA2V needs a still; fall back to FastH3 T2V / I2V
    if engine == "ltx23" and not has_image:
        return "fasth3"
    return engine


def normalize_pipeline(session: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, str]:
    """Resolve pipeline lanes from request, session, legacy booleans, or comfy_backend."""
    # Explicit lanes win
    director = str(
        request.get("pipeline_director")
        or session.get("pipeline_director")
        or ""
    ).strip().lower()
    stills = str(
        request.get("pipeline_stills")
        or session.get("pipeline_stills")
        or ""
    ).strip().lower()
    video = str(
        request.get("pipeline_video")
        or session.get("pipeline_video")
        or ""
    ).strip().lower()

    # Legacy Home Agent flags
    if not director:
        if request.get("use_home_agent_llm") is True or session.get("use_home_agent_llm"):
            director = "home_agent"
        else:
            director = "cloud_llm"
    if not stills:
        if request.get("use_home_agent_stills") is True or session.get(
            "use_home_agent_stills"
        ):
            stills = "home_agent"
        else:
            stills = "comfy"
    if not video:
        comfy = str(
            request.get("comfy_backend")
            or session.get("comfy_backend")
            or "cloud"
        ).strip().lower()
        if comfy == "cloud":
            video = "comfy_cloud"
        else:
            video = "comfy_local"

    if director not in DIRECTOR_CHOICES:
        director = "cloud_llm"
    if stills not in STILLS_CHOICES:
        stills = "comfy"
    if video not in VIDEO_CHOICES:
        video = "comfy_cloud"

    video_model = normalize_video_model(
        request.get("video_model")
        or session.get("video_model")
        or request.get("video_backend")
        or "minimax-h3"
    )

    return {
        "pipeline_director": director,
        "pipeline_stills": stills,
        "pipeline_video": video,
        "video_model": video_model,
    }


def comfy_backend_for_video(pipeline_video: str) -> str:
    if pipeline_video == "comfy_cloud":
        return "cloud"
    if pipeline_video == "fal":
        return "cloud"  # FAL is separate; Comfy unused for video
    return "local"


def needs_home_agent(lanes: Dict[str, str]) -> bool:
    return (
        lanes.get("pipeline_director") == "home_agent"
        or lanes.get("pipeline_stills") == "home_agent"
        or lanes.get("pipeline_video") == "home_agent"
    )


def needs_comfy_url(lanes: Dict[str, str]) -> bool:
    if lanes.get("pipeline_video") == "comfy_local":
        return True
    if lanes.get("pipeline_stills") == "comfy" and lanes.get("pipeline_video") not in (
        "comfy_cloud",
        "fal",
    ):
        return True
    return False


def needs_fal_key(lanes: Dict[str, str]) -> bool:
    return (
        lanes.get("pipeline_video") == "fal"
        or lanes.get("pipeline_stills") == "fal"
    )


def apply_preset(name: str) -> Optional[Dict[str, str]]:
    key = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return PRESETS.get(key)


def engine_label(engine: str) -> str:
    return {
        "minimax-h3": "MiniMax H3 Ref2VA",
        "ltx23": "LTX-2.3 IA2V",
        "fasth3": "FastH3 8-step",
    }.get(engine, engine)
