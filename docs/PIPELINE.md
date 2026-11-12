# SceneMux pipeline routing

SceneMux splits work into three lanes. It always owns **audio slicing** and **final stitch + MP3 mux**.

| Lane | Options |
|------|---------|
| **Director** | Cloud LLM · Home Agent · Hermes Agent |
| **Stills** | Comfy · Home Agent · FAL (draft) |
| **Video host** | Comfy Cloud · Playground/LAN Comfy · Home Agent · FAL (draft) |
| **Video engine** | H3 Ref2VA · FastH3 · LTX-2.3 · Hybrid Fast · Hybrid LTX |

See [ENGINES.md](ENGINES.md) for engine limits and the Arc / Playground matrix.

## Presets

| Preset | Director | Stills | Video host | Engine |
|--------|----------|--------|------------|--------|
| Cloud | cloud_llm | comfy | comfy_cloud | minimax-h3 |
| Playground | home_agent | comfy | comfy_local | ltx23 |
| Hybrid Fast | home_agent | comfy | comfy_local | hybrid_fast |
| Fast Draft | cloud_llm | comfy | comfy_local | fasth3 |
| Playground Agent | home_agent | home_agent | home_agent | minimax-h3 |
| Hybrid | home_agent | home_agent | comfy_local | minimax-h3 |

**Simple UI** shows Cloud · Playground · Hybrid Fast · Fast Draft · Custom.  
**Advanced** unlocks full lanes, Comfy/HA/Hermes URLs, engine picker, and health chips.

Playground weights: set **Video host** to Playground/LAN Comfy and `comfy_url` to the Playground Comfy port (often `:8188`). Home Agent local-web is a separate URL (default `:8765`).

## Hermes

- In-app: Director → Hermes + `HERMES_URL` / token
- External: install [`scenemux-skill/SKILL.md`](../scenemux-skill/SKILL.md) so Hermes drives SceneMux HTTP APIs

## FAL drafts

Optional draft cloud (prepaid / trial credits) — **not** required for Cloud / Playground. FAL video host cannot run Hybrid Fast H3 Ref2VA.

1. Get a key: [fal.ai/dashboard/keys](https://fal.ai/dashboard/keys)
2. Set `FAL_KEY=…` in `.env`, restart the API
3. Advanced → Video host / Stills = **FAL (draft)**

SceneMux still owns audio slice + final mux. Full details in [ENGINES.md](ENGINES.md).

See also [HOME_AGENT.md](HOME_AGENT.md).
