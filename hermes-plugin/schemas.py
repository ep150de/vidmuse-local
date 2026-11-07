"""SceneMux tool schemas — what the LLM reads to decide when to call them."""
from __future__ import annotations

DOCTOR = {
    "name": "scenemux_doctor",
    "description": (
        "Health-check the SceneMux music-video studio: is the server reachable, "
        "what is the LLM status, which required API keys are present, and is "
        "ffmpeg available. Call this FIRST when the user wants to make a music "
        "video from an MP3, and again whenever other scenemux_* tools report the "
        "server as unreachable. Read-only and free — it never starts processes "
        "and never spends anything. Returns a JSON report with actionable hints."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Optional SceneMux server base URL override (defaults to the plugin's configured base URL, usually http://127.0.0.1:8000).",
            },
        },
        "required": [],
    },
}

SETUP = {
    "name": "scenemux_setup",
    "description": (
        "Configure and start the SceneMux music-video studio server on this "
        "machine. Pick a runtime mode: 'cloud' (Comfy Cloud + Ollama/OpenCode "
        "LLM; works on any CPU machine, needs API keys) or 'local' (local "
        "ComfyUI + local LLM; needs a GPU host with models). If the server is "
        "not already healthy, launches it via Docker (detached, log captured) "
        "and waits up to ~60s for it to come up; first runs may still be "
        "building the image, in which case re-run scenemux_doctor later. "
        "Stores mode/base_url in the plugin settings."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["cloud", "local"],
                "description": "Runtime mode. 'cloud' (default) works on any machine; 'local' needs a GPU host with ComfyUI models.",
            },
            "start_server": {
                "type": "boolean",
                "description": "Start the server if it is not already running (default true).",
            },
            "base_url": {
                "type": "string",
                "description": "Optional server base URL to configure and verify (default http://127.0.0.1:8000).",
            },
        },
        "required": [],
    },
}

PROJECT = {
    "name": "scenemux_project",
    "description": (
        "Create a SceneMux music-video project and upload its assets: makes a "
        "new server session (or resumes one via session_id), applies the "
        "pipeline preset, and uploads the song (the server analyzes the beat "
        "grid and transcribes lyrics locally) plus optional reference images "
        "for character/art direction. Relatively free: local audio analysis + "
        "local transcription only. MUST run before scenemux_plan. Returns the "
        "session_id, track title, duration, and lyrics source."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Resume an existing session instead of creating a new one (use the session_id returned by a previous scenemux_project).",
            },
            "audio_path": {
                "type": "string",
                "description": "Path to the local MP3/WAV/FLAC song to turn into a music video.",
            },
            "direction": {
                "type": "string",
                "description": "Creative direction for the video, 1-3 sentences: performer, setting, palette, camera style (e.g. 'neo-noir jazz vocalist in a rain-slicked city, teal and amber neon, slow dolly shots, 35mm grain').",
            },
            "pipeline_preset": {
                "type": "string",
                "enum": ["cloud", "playground", "playground_agent", "hybrid", "hybrid_fast", "fast_draft"],
                "description": "Pipeline preset. 'cloud' (default) = cloud LLM + Comfy Cloud video on any machine. The others need local/Playground infrastructure (ComfyUI on a GPU host or the Home Agent).",
            },
            "video_model": {
                "type": "string",
                "enum": ["minimax-h3", "fasth3", "ltx23", "infinitetalk"],
                "description": "Video model override (default follows the preset; minimax-h3 = highest quality, fasth3 = quick draft).",
            },
            "refs_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of local paths to reference images (character look, art direction). The director locks the performer's identity from them.",
            },
        },
        "required": ["audio_path", "direction"],
    },
}

PLAN = {
    "name": "scenemux_plan",
    "description": (
        "Build the storyboard for a SceneMux project: the director LLM reads "
        "the beat grid + lyrics and plans the scene list, then generates cheap "
        "still-image previews of each scene for review (spends a little "
        "image-generation credit; the plan itself only spends LLM tokens). "
        "Runs after scenemux_project and BEFORE any render. Returns the scene "
        "list (index, time, type, prompt) with still preview paths where "
        "available — show the storyboard to the user for review before "
        "scenemux_render."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session from scenemux_project.",
            },
            "direction": {
                "type": "string",
                "description": "Optional: refresh/override the creative direction before planning.",
            },
            "max_scenes": {
                "type": "integer",
                "description": "Optional cap on the number of storyboard still previews to generate.",
            },
            "scene_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional: generate still previews for only these scene indices (review a subset first).",
            },
        },
        "required": ["session_id"],
    },
}

RENDER = {
    "name": "scenemux_render",
    "description": (
        "Render the storyboard to video and assemble the final music video. "
        "THIS SPENDS API CREDITS (Comfy Cloud / FAL / Home Agent per scene). "
        "The tool always returns a cost/time estimate first; the render only "
        "starts when confirm=true. Workflow: call once WITHOUT confirm, show "
        "the estimate to the user, get explicit approval, then call again "
        "with confirm=true. Returns immediately (render runs asynchronously) — "
        "poll with scenemux_status until download_url appears."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session to render (must already have a storyboard plan).",
            },
            "direction": {
                "type": "string",
                "description": "Optional: refresh the creative direction before rendering.",
            },
            "rebuild_plan": {
                "type": "boolean",
                "description": "Re-plan the storyboard from scratch before rendering (default false — reuses the reviewed plan).",
            },
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "prompt": {"type": "string"},
                    },
                    "required": ["index"],
                },
                "description": "Optional per-scene prompt overrides: [{index, prompt}].",
            },
            "confirm": {
                "type": "boolean",
                "description": "Set true ONLY after the user has explicitly approved the estimated spend. Default false = estimate-only, nothing renders.",
            },
        },
        "required": ["session_id"],
    },
}

STATUS = {
    "name": "scenemux_status",
    "description": (
        "Check SceneMux render progress for a session: per-scene "
        "ready/rendering/failed counts, errors, and the download URL when the "
        "final cut is assembled. Read-only and free. Use wait_seconds to "
        "block up to N seconds (max 120) polling until progress changes or the "
        "video is done."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session to check.",
            },
            "wait_seconds": {
                "type": "integer",
                "description": "Optional: keep polling for up to this many seconds (max 120) before returning.",
            },
        },
        "required": ["session_id"],
    },
}

EXPORT = {
    "name": "scenemux_export",
    "description": (
        "Download the finished music video from the SceneMux server to a "
        "local file (returns the absolute path to hand to the user). "
        "Optionally also produces a 9:16 vertical derivative for shorts/TikTok. "
        "Requires the render to be complete (download_url present in "
        "scenemux_status)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session with a finished final cut.",
            },
            "vertical": {
                "type": "boolean",
                "description": "Also create a 9:16 center-crop vertical version (default false).",
            },
        },
        "required": ["session_id"],
    },
}

CANCEL = {
    "name": "scenemux_cancel",
    "description": (
        "Cancel an in-progress SceneMux render. Scenes already finished are "
        "kept; the queue stops. Use when the user changes their mind after "
        "rendering started."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session whose render should be cancelled.",
            },
        },
        "required": ["session_id"],
    },
}
