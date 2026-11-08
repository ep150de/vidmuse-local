---
name: studio
description: >
  Drive the SceneMux music-video studio from Hermes: turn an MP3 into a
  beat-aligned, lip-synced music video. Plan the storyboard, review stills,
  render scenes on Comfy (cloud or local), and get back a muxed MP4 with the
  master audio. Use when the user wants a music video from a song.
---

# SceneMux studio (Hermes plugin)

SceneMux owns audio slicing and the final stitch + master MP3 mux. Video
engines (Comfy Cloud / local ComfyUI / Home Agent / optional FAL) only
produce per-scene picture. Never reimplement ffmpeg concat — let the server
assemble.

## Tools (toolset `scenemux`)

| Tool | What it does |
|------|--------------|
| `scenemux_doctor` | Health-check: server, LLM, API keys, ffmpeg. Call FIRST. Free and read-only. |
| `scenemux_setup` | Configure + start the studio server (Docker). Modes: `cloud` (any machine) / `local` (GPU host). Provisions declared keys from Hermes secret storage into the server's `.env` (repo root) so the launched container can use them. |
| `scenemux_project` | Create a session, upload the MP3 (+ optional reference images), set creative direction + pipeline preset. |
| `scenemux_plan` | Run the director plan + storyboard stills. **Always show the storyboard to the user before rendering.** |
| `scenemux_render` | Estimate cost first; starts only with `confirm=true`. **Never set `confirm` without the user's explicit approval of the estimate.** |
| `scenemux_status` | Poll render progress (per-scene). `wait_seconds` (max 120) blocks until progress changes. |
| `scenemux_export` | Fetch the finished MP4 (+ optional 9:16 vertical) to a local file; hand the path to the user. |
| `scenemux_cancel` | Stop an in-progress render (finished scenes are kept). |

## Happy path

1. `scenemux_doctor` — fix whatever the hints say (keys, server).
2. `scenemux_setup` (if the server is down) — cloud mode for anything without a GPU. First start may take minutes (image build); re-run `scenemux_doctor` when the tool says it's still building.
3. `scenemux_project` — session + song + direction. Upload includes beat-grid analysis + local lyric transcription (can take 1-3 min for a full song).
4. `scenemux_plan` — review stills with the user; iterate on prompts/direction.
5. `scenemux_render` — estimate → confirm → start.
6. `scenemux_status` until done, then `scenemux_export` — deliver the MP4 to the user's chat.

## Rules

- Rendering spends money (Comfy Cloud credits, optional FAL). Always run
  `scenemux_render`'s estimate first and get explicit user confirmation.
- Never render before the user has seen the storyboard.
- Cloud mode: needs `COMFY_CLOUD_API_KEY`. LLM lane: `OLLAMA_API_KEY` or
  `OPENCODE_API_KEY` — **or nothing, if Ollama runs locally** (the plugin
  auto-detects `127.0.0.1:11434` and points the server at it; free + private).
  Local mode: needs a GPU host with ComfyUI models and a local LLM.
- Long tools: `scenemux_plan` can run 5-15 min (LLM + stills), `scenemux_project`
  1-3 min, renders take the time in the estimate. If a plan call times out,
  call `scenemux_status` — the plan often finished server-side.
- Local-Ollama model size: the server's per-LLM-call timeout is 120s, so on a
  CPU-only host pick a local model that answers quickly (<=~14B). 30B+ models
  on CPU time out on every call (character lock, prompt polish) and the plan
  degrades to fallback prompts. `scenemux_doctor` reports the configured model.
- Home Agent port (e.g. `:8777`) is NOT the Comfy port (`:8188`).
- Engine quality: H3 Ref2VA = true phoneme lip sync (finals); FastH3 / LTX =
  fast drafts, not Ref2VA. The `fast_draft` preset renders on LOCAL Comfy (needs
  a GPU host) — on a plain CPU machine use the `cloud` preset with
  `video_model: fasth3` for a cheap first cut.
- Long renders: poll with `scenemux_status`; never hold one blocking call
  for the whole render. If the user changes their mind mid-render:
  `scenemux_cancel`.
- Fail-closed: if an estimate can't be produced or the user is unreachable
  for approval, stop and report — do not guess `confirm=true`.

## API reference (server endpoints, for debugging)

Base URL: plugin setting `base_url` (default `http://127.0.0.1:8000`).

- `GET /api/health`, `GET /api/llm/status`
- `POST /api/session/create` → `session_id`
- `POST /api/session/config` — pipeline lanes:
  `pipeline_director` = cloud_llm | home_agent | hermes;
  `pipeline_stills` = comfy | fal | home_agent;
  `pipeline_video` = comfy_cloud | comfy_local | home_agent | fal;
  `pipeline_preset` = cloud | playground | playground_agent | hybrid |
  hybrid_fast | fast_draft;
  plus `comfy_url`, `home_agent_url`, `home_agent_password`,
  `hermes_url`, `hermes_token`, `creative_direction`, `lyrics`, `singer_profile`.
- `POST /api/upload/audio` (multipart), `POST /api/upload/images` (repeated `files`)
- `POST /api/studio/plan`, `POST /api/studio/storyboard-stills`,
  `POST /api/studio/estimate`, `POST /api/studio/render` (async — returns
  immediately, poll state), `POST /api/studio/cancel`
- `GET /api/studio/{session_id}/state`, `GET /api/download/{session_id}`
- `POST /api/studio/export-vertical`
