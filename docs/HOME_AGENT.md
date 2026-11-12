# Home Agent LAN bridge (SceneMux)

SceneMux uses Intel AI Playground **Home Agent → Local web** over the LAN for
director chat, stills, and optional agentic media. Protocol matches
[TNG/AI-Playground](https://github.com/TNG/AI-Playground) `home-agent/channels/local_web.py`:

`POST /api/login` → cookie `aipg_local_web` → `GET /api/events` (SSE) → `POST /api/chat`.

This is **additive** to Comfy Cloud / Playground Comfy. Prefer studio presets
([PIPELINE.md](PIPELINE.md)). SceneMux stays a thin local-web client — it does
not embed Playground’s Pi/MCP agent runtime.

## Host setup (Playground PC)

1. Enable **Home Agent → Local web** (port, password, **Allow LAN**).
2. Copy the URL from the setup screen (default port **8765**).
3. For H3 / FastH3 / LTX weights on the same PC, also expose **ComfyUI**
   (often `:8188`) and use preset **Playground**, **Hybrid Fast**, or **Hybrid**.

### Firewall (Windows PowerShell Admin)

```powershell
New-NetFirewallRule -DisplayName "AI Playground Home Agent" `
  -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
New-NetFirewallRule -DisplayName "AI Playground Comfy" `
  -Direction Inbound -Protocol TCP -LocalPort 8188 -Action Allow -Profile Private
```

Adjust `8765` if Local web uses another port.

## Env

```bash
HOME_AGENT_URL=http://192.168.1.10:8765
HOME_AGENT_PASSWORD=
HOME_AGENT_TIMEOUT=300
# Optional Hermes director:
# HERMES_URL=http://127.0.0.1:8642
# HERMES_TOKEN=
# LLM_PROVIDER=hermes
```

`HOME_AGENT_URL` port ≠ Comfy port. Default local-web is **8765**; Comfy is **8188**.

## Diagnose LAN (studio)

Advanced (or Simple when HA is needed) → **Diagnose LAN**:

| Step | What it checks |
|------|----------------|
| parse | Host/port; warns if URL is loopback from a remote SceneMux |
| tcp | Port accept |
| http | Local-web HTTP responds |
| login | Password → session cookie (needs password field) |
| sse | Authenticated `GET /api/events` |
| comfy | Same-host `:8188` `/system_stats` (or your Comfy URL) |

API: `POST /api/home-agent/diagnose` with `{ url, password?, probe_comfy?, comfy_url? }`.

### Common failures

| Symptom | Likely fix |
|---------|------------|
| TCP fail, ping works | Allow LAN + firewall on local-web port |
| Wrong port | Use Playground’s Local web URL (not `:8188`) |
| Login fail | Password with spaces kept; only trim ends; wait after 429 lockout |
| Loopback from Chromebook | Tailscale or `ssh -N -L 8765:127.0.0.1:8765 user@playground` |
| Comfy fail, HA ok | Open Comfy LAN/firewall separately for Hybrid Fast / H3 |

## API

- `POST /api/home-agent/diagnose` — multi-step LAN diagnose
- `POST /api/home-agent/test` — login-only smoke
- `POST /api/home-agent/job` — agentic media ask (natural-language prompt)

## Agentic jobs

Media asks are plain creative prompts so Playground’s agent can pick image /
LTX / H3 tools. Structured JSON is only appended when extras need it.
