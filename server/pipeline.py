"""Studio pipeline lane helpers: director / stills / video routing."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

DIRECTOR_CHOICES = ("cloud_llm", "home_agent", "hermes")
STILLS_CHOICES = ("comfy", "home_agent")
VIDEO_CHOICES = ("comfy_cloud", "comfy_local", "home_agent")

PRESETS: Dict[str, Dict[str, str]] = {
    "cloud": {
        "pipeline_director": "cloud_llm",
        "pipeline_stills": "comfy",
        "pipeline_video": "comfy_cloud",
    },
    "playground": {
        "pipeline_director": "home_agent",
        "pipeline_stills": "comfy",
        "pipeline_video": "comfy_local",
    },
    "playground_agent": {
        "pipeline_director": "home_agent",
        "pipeline_stills": "home_agent",
        "pipeline_video": "home_agent",
    },
    "hybrid": {
        "pipeline_director": "home_agent",
        "pipeline_stills": "home_agent",
        "pipeline_video": "comfy_local",
    },
}


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

    return {
        "pipeline_director": director,
        "pipeline_stills": stills,
        "pipeline_video": video,
    }


def comfy_backend_for_video(pipeline_video: str) -> str:
    if pipeline_video == "comfy_cloud":
        return "cloud"
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
    if lanes.get("pipeline_stills") == "comfy" and lanes.get("pipeline_video") != "comfy_cloud":
        return True
    return False


def apply_preset(name: str) -> Optional[Dict[str, str]]:
    key = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return PRESETS.get(key)
