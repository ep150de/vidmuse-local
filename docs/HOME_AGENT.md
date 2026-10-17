# Home Agent LAN bridge (SceneMux)

SceneMux can use Intel AI Playground **Home Agent → Local web** for director chat, stills, and optionally H3 jobs.

This is **additive** to Comfy Cloud / Playground Comfy. Prefer **pipeline presets** in the studio (see [PIPELINE.md](PIPELINE.md)).

## Host setup (Playground PC)

1. Enable Home Agent → Local web (port, password, **Allow LAN**).
2. Note LAN URL (e.g. `http://192.168.4.34:8777`).
3. For H3 weights on the same PC, also expose **ComfyUI** (often `:8188`) and use preset **Playground** or **Hybrid** (`pipeline_video=comfy_local`).

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
```

## API

- `POST /api/home-agent/diagnose` — TCP probe
- `POST /api/home-agent/test` — login probe
- `POST /api/home-agent/job` — agentic media job
