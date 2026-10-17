---
name: scenemux
description: >
  Drive SceneMux (local music-video studio) over HTTP: upload a song + optional
  reference stills + creative direction, choose pipeline lanes (Cloud Comfy,
  Playground Comfy, Home Agent, Hermes director), plan storyboard, generate
  stills, render MiniMax H3 scenes, and download the finished muxed MP4.
  SceneMux owns audio slicing and final stitch.
trigger: >
  Use when the user wants a full music video from an MP3 via SceneMux, or when
  configuring Cloud vs AI Playground vs Home Agent vs Hermes for director/stills/video.
---

# SceneMux operator skill (Hermes / agents)

Base URL default: `http://127.0.0.1:8000` (override with `SCENEMUX_URL`).

## Pipeline lanes

Set on `POST /api/session/config`:

| Field | Values |
|-------|--------|
| `pipeline_director` | `cloud_llm` \| `home_agent` \| `hermes` |
| `pipeline_stills` | `comfy` \| `home_agent` |
| `pipeline_video` | `comfy_cloud` \| `comfy_local` \| `home_agent` |
| `pipeline_preset` | `cloud` \| `playground` \| `playground_agent` \| `hybrid` |

Also send when needed: `comfy_url`, `home_agent_url`, `home_agent_password`, `hermes_url`, `hermes_token`, `creative_direction`, `lyrics`, `singer_profile`.

## Happy path

1. `POST /api/session/create` → `session_id`
2. `POST /api/session/config` with `session_id` + pipeline + direction
3. `POST /api/upload/audio` multipart (`session_id`, `file`)
4. Optional `POST /api/upload/images` for performer refs
5. `POST /api/studio/plan` `{ session_id, direction }`
6. `POST /api/studio/storyboard-stills` `{ session_id }`
7. Review stills via `/api/session/{id}/asset/storyboard/...`
8. `POST /api/studio/estimate` then `POST /api/studio/render` `{ session_id, direction, rebuild_plan: false }`
9. Poll `GET /api/studio/{session_id}/state` until `download_url` / final ready
10. Download final; optional `POST /api/studio/export-vertical` for 9:16

## Notes

- Home Agent port (e.g. `:8777`) is **not** Comfy (`:8188`). Playground H3 weights use `pipeline_video=comfy_local` + `comfy_url`.
- Prefer Playground Comfy for reliable H3; Home Agent video jobs need the agent to return an MP4 attachment/URL.
- Do not reimplement ffmpeg concat — SceneMux assembles and muxes the master MP3.
