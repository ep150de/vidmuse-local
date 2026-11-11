"""SceneMux tool schemas — what the LLM reads to decide when to call them."""
from __future__ import annotations

DOCTOR = {
    "name": "scenemux_doctor",
    "description": (
        "Health-check the SceneMux music-video studio: is the server reachable, "
        "what is the LLM status, which API keys are present, which render "
        "backends are usable (Comfy Cloud key, a reachable standalone "
        "ComfyUI, a reachable AI Playground Home Agent), and is ffmpeg "
        "available. Call this FIRST when the user wants to make a music video "
        "from an MP3, and again whenever other scenemux_* tools report the "
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
        "pipeline/backend selection, and uploads the song (the server analyzes "
        "the beat grid and transcribes lyrics locally) plus optional reference "
        "images for character/art direction. Relatively free: local audio "
        "analysis + local transcription only. MUST run before scenemux_plan. "
        "Returns the session_id, resolved pipeline lanes, track title, "
        "duration, and lyrics source."
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
                "description": "Optional pipeline preset. 'cloud' = cloud LLM + Comfy Cloud video on any machine. The others need local/Playground infrastructure. Explicit backend parameters below override the preset's lanes.",
            },
            "video_backend": {
                "type": "string",
                "enum": ["comfy_cloud", "comfy_local", "home_agent", "fal"],
                "description": "Video generation backend (overrides the preset). comfy_cloud = Comfy Cloud API (needs COMFY_CLOUD_API_KEY); comfy_local = a standalone ComfyUI instance you control (pass comfy_url, e.g. http://192.168.1.50:8188); home_agent = AI Playground Home Agent (pass home_agent_url; password comes from the HOME_AGENT_PASSWORD secret); fal = FAL.ai (needs FAL_KEY).",
            },
            "stills_backend": {
                "type": "string",
                "enum": ["comfy", "home_agent", "fal"],
                "description": "Optional: storyboard still preview backend. Default follows the preset (usually 'comfy', which reuses the comfy_url or Comfy Cloud).",
            },
            "director_backend": {
                "type": "string",
                "enum": ["cloud_llm", "home_agent", "hermes"],
                "description": "Optional: who directs the storyboard. cloud_llm = Ollama/OpenCode LLM (local Ollama is auto-detected, free); home_agent = the AI Playground Home Agent; hermes = this local Hermes agent itself (needs the Hermes API server: API_SERVER_ENABLED + API_SERVER_KEY in ~/.hermes/.env and the gateway running; the key is picked up automatically from Hermes secret storage).",
            },
            "comfy_url": {
                "type": "string",
                "description": "Standalone ComfyUI base URL for video_backend=comfy_local (e.g. http://192.168.1.50:8188). Defaults to the plugin's comfy_url setting, then the server's http://127.0.0.1:8188.",
            },
            "home_agent_url": {
                "type": "string",
                "description": "AI Playground Home Agent base URL for video_backend/stills_backend/director_backend=home_agent (e.g. http://192.168.1.40:8777). Defaults to the plugin's home_agent_url setting.",
            },
            "hermes_url": {
                "type": "string",
                "description": "Optional: base URL of the Hermes API server for director_backend=hermes. Default: auto — http://host.docker.internal:8642 when the SceneMux server runs in Docker on this machine (the Hermes API server on this host), http://127.0.0.1:8642 for a native server.",
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
