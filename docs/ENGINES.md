# SceneMux video engines

SceneMux always owns **audio slicing** and **final stitch + master MP3 mux**. Engines only produce per-scene picture (and sometimes temporary audio that we replace).

## Engine matrix

| `video_model` | Comfy path | Lip sync | Best for |
|---------------|------------|----------|----------|
| `minimax-h3` | MiniMax H3 **Ref2VA** | Phoneme / true Ref2VA | PERF scenes, quality finals |
| `fasth3` | FastVideo FastH3 8-step | **No Ref2VA** — I2V/T2V only | Drafts, iteration |
| `ltx23` | LTX-2.3 IA2V | Audio-conditioned mouth motion (not phoneme Ref2VA) | Faster local drafts |
| `hybrid_fast` | PERF → H3 Ref2VA; else FastH3 | Mixed | Playground / Arc when VRAM is tight |
| `hybrid_ltx` | PERF → H3 Ref2VA; else LTX | Mixed | Local speed with lip-sync on vocals |

Do **not** claim FastH3 or LTX equals Ref2VA lip-sync quality.

## Simple presets (UI)

| Preset | Host | Engine |
|--------|------|--------|
| Cloud | Comfy Cloud | H3 Ref2VA |
| Playground | Local Comfy (`:8188`) | LTX-2.3 |
| Hybrid Fast | Local Comfy | hybrid_fast |
| Fast Draft | Local Comfy | FastH3 |
| Custom | Advanced lanes | Your choice |

Advanced mode exposes Director / Stills / Video host / Video engine, Comfy URL, Home Agent, Hermes, and engine health chips from `/api/health` → `services.comfyui.video_models`.

## FastH3 limits

- ComfyUI **≥ 0.36**
- Checkpoint: `fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors`
- Shared H3 text encoder + video/audio VAEs
- **T2V and first/last-frame I2V only** — Ref2VA was not distilled
- Keep scheduler at **8 steps**
- SceneMux muxes your song slice over the clip (not model-native audio)

Env overrides: `COMFY_FASTH3_UNET`, `COMFY_FASTH3_VIDEO_VAE`, `COMFY_FASTH3_STEPS` (default 8).

## LTX-2.3 IA2V

- Aligns with Comfy `video_ltx2_3_ia2v`
- Needs a still (storyboard / identity); scenes without a still fall back to FastH3
- User audio is always muxed in CreateVideo

## Intel Lunar Lake / AI Playground (Arc 140V)

Recommended test matrix:

1. **Playground** — HA director + local Comfy URL for LTX or FastH3 drafts  
2. **Hybrid Fast** — PERF on base H3 if weights fit; else FastH3 draft only  
3. Keep **Cloud** H3 for quality lip sync when local VRAM/time is tight  
4. Firewall: Home Agent local-web (default `:8765`) + Comfy `:8188` — see [HOME_AGENT.md](HOME_AGENT.md)

Prefer fp8 / FastH3 int8 / distilled LTX weights on Arc. Documented in health `video_models` probes.

## FAL drafts (optional)

[FAL](https://fal.ai) is a third-party cloud that runs image/video models over an API. SceneMux can use it for **short draft stills or clips** so you can try ideas without local GPU weight installs. It is **not** required for normal Cloud or Playground presets, and it does **not** replace Comfy Cloud / Playground as the production path. SceneMux still slices your song and muxes the final MP4.

**Credits:** FAL uses prepaid / trial account balance — not a reliable “N free gens per day” entitlement. When credits run out, requests fail (often as 403/402). Check [fal.ai/dashboard](https://fal.ai/dashboard).

**Host vs engine:** Video host = FAL is **draft-only for every scene**. Hybrid Fast / H3 Ref2VA need Comfy Cloud, Playground Comfy, or Home Agent. The studio coerces Hybrid/H3 → Fast Draft labeling when FAL is selected so Preview does not pretend to run Ref2VA.

### Get a key (first time)

1. Sign up / sign in at [fal.ai](https://fal.ai)
2. Open [API keys](https://fal.ai/dashboard/keys) and create a key
3. Copy it into your project `.env` (never commit the key):

```bash
FAL_KEY=your_key_here
# optional:
# FAL_MAX_CONCURRENT=1
# FAL_VIDEO_MODEL=fal-ai/minimax/video-01-live
# FAL_IMAGE_MODEL=fal-ai/flux/schnell
```

4. Restart the SceneMux API so `/api/health` → `services.fal.ready` is true
5. In studio **Advanced**, set Video host and/or Stills to **FAL (draft)**

The UI warns when the key is missing or credits are exhausted. Concurrent jobs are capped (`FAL_MAX_CONCURRENT`, default 1).

## WanGP

[WanGP](https://github.com/deepbeepmeep/WanGP) is a useful Gradio suite for ideas (queues, per-model profiles, galleries). SceneMux does **not** vendor or replace itself with WanGP; we adopt product patterns only (queue clarity, engine presets, media-bin reuse).

## Related

- [PIPELINE.md](PIPELINE.md) — director / stills / video lanes  
- [HOME_AGENT.md](HOME_AGENT.md) — LAN Playground agent  
