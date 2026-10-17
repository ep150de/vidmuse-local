# SceneMux pipeline routing

SceneMux splits work into three lanes. It always owns **audio slicing** and **final stitch + MP3 mux**.

| Lane | Options |
|------|---------|
| **Director** | Cloud LLM · Home Agent · Hermes Agent |
| **Stills** | Comfy · Home Agent |
| **Video (H3)** | Comfy Cloud · Playground/LAN Comfy · Home Agent |

## Presets

| Preset | Director | Stills | Video |
|--------|----------|--------|-------|
| Cloud | cloud_llm | comfy | comfy_cloud |
| Playground | home_agent | comfy | comfy_local |
| Playground Agent | home_agent | home_agent | home_agent |
| Hybrid | home_agent | home_agent | comfy_local |

Playground H3 weights: set **Video** to Playground/LAN Comfy and `comfy_url` to the Playground Comfy port (often `:8188`). Home Agent local-web is a separate URL (often `:8777`).

## Hermes

- In-app: Director → Hermes + `HERMES_URL` / token
- External: install [`scenemux-skill/SKILL.md`](../scenemux-skill/SKILL.md) so Hermes drives SceneMux HTTP APIs

See also [HOME_AGENT.md](HOME_AGENT.md).
