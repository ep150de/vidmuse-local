# Home Agent LAN bridge (SceneMux)

SceneMux can use Intel AI Playground **Home Agent → Local web** for director chat, stills, and optionally H3 jobs.

This is **additive** to Comfy Cloud / Playground Comfy. Prefer **pipeline presets** in the studio (see [PIPELINE.md](PIPELINE.md)).

## Host setup (Playground PC)

1. Enable Home Agent → Local web (port, password, **Allow LAN**).
2. Note LAN URL (e.g. `http://192.168.4.34:8777`).
3. For video weights on the same PC, also expose **ComfyUI** (often `:8188`) and use preset **Playground**, **Hybrid Fast**, or **Hybrid** (`pipeline_video=comfy_local`). Engines: H3 Ref2VA, FastH3, LTX — see [ENGINES.md](ENGINES.md).

### Firewall (Windows PowerShell Admin)

```powershell
New-NetFirewallRule -DisplayName "AI Playground Home Agent" `
  -Direction Inbound -Protocol TCP -LocalPort 8777 -Action Allow -Profile Private
New-NetFirewallRule -DisplayName "AI Playground Comfy" `
  -Direction Inbound -Protocol TCP -LocalPort 8188 -Action Allow -Profile Private
```

## Env

```bash
HOME_AGENT_URL=http://192.168.4.34:8777
HOME_AGENT_PASSWORD=
HOME_AGENT_TIMEOUT=300
# Optional Hermes director:
# HERMES_URL=http://127.0.0.1:8642
# HERMES_TOKEN=
# LLM_PROVIDER=hermes
# Optional FAL free-tier drafts (Advanced UI — not required for Cloud/Playground):
# Get a key: https://fal.ai/dashboard/keys
# FAL_KEY=
```
See [ENGINES.md](ENGINES.md) for FAL setup.

## API

- `POST /api/home-agent/diagnose` — TCP probe
- `POST /api/home-agent/test` — login probe
- `POST /api/home-agent/job` — agentic media job
