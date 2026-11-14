# SceneMux for Hermes

Turn any Hermes agent into a music-video director: drop an MP3 into the chat,
review a storyboard, and get back a beat-aligned, lip-synced music video —
the finished MP4, muxed with the master audio.

SceneMux owns audio slicing and the final stitch. Video engines (Comfy Cloud,
local ComfyUI on a GPU host, Intel AI Playground Home Agent, optional FAL
drafts) only produce per-scene picture.

## What the plugin provides

- **Tools** (toolset `scenemux`): `scenemux_doctor` (health check),
  `scenemux_setup` (configure + start the server), and the studio tools
  (project / plan / render / status / export).
- **Skill** `scenemux:studio` — the operating knowledge (pipeline lanes,
  engines, happy path, spend rules).
- **Zero Python dependencies** — the plugin client is stdlib only.

## Install

```bash
# From the catalog (once listed)
hermes plugins install scenemux

# From the repo (development / before catalog listing)
hermes plugins install ep150de/scenemux/hermes-plugin
```

The plugin installs as a standalone client (no server code is copied with
it). For `scenemux_setup` to launch the studio server for you, set the
plugin's **`repo_path`** setting to a full clone of
[the repo](https://github.com/ep150de/scenemux) — the directory that
contains `docker-compose.yml` + `server/` — or start the server yourself and
point `base_url` at it (see Setup).

## Setup

1. **Keys** — cloud mode (works on any machine) needs:
   - `COMFY_CLOUD_API_KEY` — https://platform.comfy.org/profile/api-keys (Standard+ plan)
   - `OLLAMA_API_KEY` (https://ollama.com/settings/keys) or `OPENCODE_API_KEY` (https://opencode.ai/)
   - optional `FAL_KEY` (https://fal.ai/dashboard/keys) — drafts only
   - Local mode (GPU host) needs no cloud keys, just ComfyUI models + a local LLM.
   Keys live in `~/.hermes/.env` (or the Desktop plugin settings). The plugin
   reads them only through Hermes' secret scope — never from files directly.
2. **Server** — Ask your agent: *"set up SceneMux in cloud mode."* It runs
   `scenemux_setup`, which launches the server via Docker (detached; log at
   `<HERMES_HOME>/plugin-data/scenemux/server-launch.log`) and waits for it
   to become healthy. First run builds the image (a few minutes). `scenemux_setup`
   finds the server code via the `repo_path` setting (point it at a full repo
   clone) — or, if the server is already running, it just checks health.
3. **Optional — make Hermes itself the director** — pass
   `director_backend=hermes` to `scenemux_project` and the storyboard is
   planned by *this* Hermes agent (its full toolset, not an external LLM
   lane). This needs the Hermes API server:
   ```bash
   # add to ~/.hermes/.env, then (re)start the gateway
   API_SERVER_ENABLED=true
   API_SERVER_KEY=<some-long-random-key>
   hermes gateway
   ```
   The plugin reads `API_SERVER_KEY` through the secret scope and sends it to
   the server per session (only when the studio server is on loopback) — no
   extra configuration per project. `scenemux_doctor`
   reports whether the API server is configured and reachable.
4. **Check** — `scenemux_doctor` tells you exactly what's missing, if anything.

## Make a video

Just ask: *"make a music video for ~/songs/demo.mp3 — dark neon city,
synthwave."* The agent will: doctor → start server → create project + upload
→ plan the storyboard (show it to you) → estimate + confirm → render →
deliver the MP4 (and 9:16 on request).

## Security & disclosure

- **Network calls** to: the SceneMux server (loopback by default; tool
  calls to non-loopback hosts are refused), Comfy Cloud, Ollama Cloud,
  OpenCode Go, FAL.ai (only the endpoints the server uses with the keys
  you provide).
- **Spawns a long-running process**: `docker compose up -d` for the SceneMux
  server (detached, logs captured; published on `127.0.0.1` only, no
  wildcard CORS). It keeps running after the session ends.
- **Spend**: rendering consumes Comfy Cloud credits (and optional FAL
  credits). Every render goes through an estimate, and a `confirm=true`
  call is escalated to a Hermes human-approval prompt (the plugin's
  `pre_tool_call` spend gate) — the render cannot start without that
  approval.
- **Secrets** are read only through the Hermes secret scope, sent only to
  loopback / `host.docker.internal` hosts, and written to the server's
  `.env` (chmod 600) only for a 127.0.0.1-bound server. `docker compose`
  is spawned with a minimal explicit environment, not Hermes's whole env.
- **No telemetry, no analytics, no credential storage of its own.**
- This plugin does not override any Hermes core behaviour; it only registers
  its tools, its spend-gate hook, and one skill.

## Troubleshooting

- `scenemux_doctor` says server unreachable → re-run `scenemux_setup`, or
  check the launch log.
- `HTTP 401/403` from a studio call → a key is missing/invalid; doctor lists
  which one.
- `director_backend=hermes` fails → the Hermes API server is not up: doctor
  reports `hermes_director.reachable`. Fix: `API_SERVER_ENABLED=true` +
  `API_SERVER_KEY` in `~/.hermes/.env`, then `hermes gateway`.
- Home Agent port (e.g. `:8765`) ≠ Comfy port (`:8188`).
- The server itself reads its own `.env` when run manually (docker compose
  auto-loads the repo `.env`); the *plugin* always supplies credentials via
  the environment it spawns, and never reads credential files.
