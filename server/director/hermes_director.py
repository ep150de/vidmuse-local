"""Hermes Director — autonomous music video generation orchestrator.

The director drives the full pipeline:
1. Analyze uploaded audio (tempo, beats, energy, segments)
2. Generate video concepts via LLM (3 distinct ideas)
3. Generate reference images via ComfyUI z-image turbo
4. Generate storyboard prompts via LLM
5. Generate storyboard images via ComfyUI
6. Generate video motion prompts via LLM
7. Generate video clips via ComfyUI LTX-2.3 (image+audio) or HuMo
8. Assemble final MP4 synced to music beats via FFmpeg

User input required:
- Text direction / creative brief
- Optional: reference images OR text prompt to generate references
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from server.llm.base import LLMBackend
from server.audio_analyzer import AudioAnalyzer
from server.image_generator import ComfyUIClient
from server.video_editor import VideoEditor

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/vidmuse/outputs"))
LOG = logging.getLogger("vidmuse.director")

# Progress callback signature
ProgressCallback = Callable[[str, int, str], None]


class HermesDirector:
    """Autonomous director that orchestrates music video generation."""

    # Phases for progress tracking
    PHASE_AUDIO = "audio_analysis"
    PHASE_CONCEPTS = "concepts"
    PHASE_REFERENCES = "reference_images"
    PHASE_STORYBOARD = "storyboard"
    PHASE_VIDEO = "video_clips"
    PHASE_ASSEMBLE = "final_video"

    def __init__(
        self,
        llm_backend: Optional[LLMBackend] = None,
        comfyui_client: Optional[ComfyUIClient] = None,
        output_dir: Optional[Path] = None,
    ):
        self.llm = llm_backend
        self.comfyui = comfyui_client or ComfyUIClient()
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # State accumulated during pipeline
        self.audio_analysis: Dict[str, Any] = {}
        self.audio_path: Optional[str] = None
        self.concepts: List[Dict[str, Any]] = []
        self.reference_images: List[str] = []
        self.storyboard: List[Dict[str, Any]] = []
        self.selected_concept: Optional[Dict[str, Any]] = None
        self.character_lock: str = ""
        self._video_seed_base: Optional[int] = None
        self.final_video_path: Optional[str] = None

        # Progress tracking
        self._progress_callback: Optional[ProgressCallback] = None
        self._current_phase = "idle"
        self._current_progress = 0
        self._status = "idle"

    def on_progress(self, callback: ProgressCallback) -> None:
        """Register a progress callback for real-time updates."""
        self._progress_callback = callback

    def _emit_progress(self, phase: str, progress: int, status: str) -> None:
        """Emit progress update."""
        self._current_phase = phase
        self._current_progress = progress
        self._status = status
        LOG.info("Progress: [%d%%] %s — %s", progress, phase, status)
        if self._progress_callback:
            self._progress_callback(phase, progress, status)

    # ── Phase 1: Audio Analysis ───────────────────────────────────────

    async def analyze_audio(self, audio_path: str | Path) -> Dict[str, Any]:
        """Analyze uploaded audio track."""
        self._emit_progress(self.PHASE_AUDIO, 10, "Analyzing audio...")

        self.audio_analysis = AudioAnalyzer.analyze(audio_path)
        LOG.info("Audio analysis complete: %s (%.1fs, %.0f BPM)",
                 audio_path, self.audio_analysis["duration"], self.audio_analysis["tempo"])

        self._emit_progress(self.PHASE_AUDIO, 100, "Audio analyzed")
        return self.audio_analysis

    # ── Phase 2: Concept Generation ───────────────────────────────────

    async def generate_concepts(
        self,
        text_direction: str,
        reference_images: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate 3 video concepts based on text direction + audio analysis."""
        self._emit_progress(self.PHASE_CONCEPTS, 10, "Generating concepts...")

        if self.llm is None:
            raise RuntimeError("No LLM backend available. Install llama.cpp or start Ollama.")

        # Build context
        analysis = self.audio_analysis
        analysis_str = (
            f"Duration: {analysis['duration']:.1f}s | Tempo: {analysis['tempo']:.0f} BPM "
            f"| Beats: {analysis['beat_count']} | Brightness: {analysis['brightness']:.1f}"
        )
        segments_str = ", ".join(f"{s['name']} ({s['start']:.1f}s-{s['end']:.1f}s)"
                                  for s in analysis["segments"])

        # Reference images context
        ref_context = ""
        if reference_images:
            ref_context = "\n\nREFERENCE IMAGES: " + "; ".join(Path(r).name for r in reference_images)

        system_prompt = """You are VidMuse, an expert AI music video director.
You propose creative, cinematic music video concepts with specific visual direction.

When proposing concepts, provide:
- title (evocative name)
- style (visual aesthetic)
- palette (3 hex colors)
- narrative (story arc from start to finish)
- key_moments (specific visuals for intro, verse, chorus, outro)
- complexity (simple | medium | complex)

Respond with EXACTLY this JSON format:
{
  "concepts": [
    {
      "title": "...",
      "style": "...",
      "palette": ["#xxxxxx", "#xxxxxx", "#xxxxxx"],
      "narrative": "...",
      "key_moments": {
        "intro": "...",
        "verse": "...",
        "chorus": "...",
        "outro": "..."
      },
      "complexity": "simple|medium|complex"
    }
  ]
}

Be creative, cinematic, and specific. Match the music's energy and mood."""

        user_message = (
            f"Create 3 video concepts for this track with the following direction:\n\n"
            f"DIRECTION: {text_direction}\n\n"
            f"{analysis_str}\n"
            f"Segments: {segments_str}\n"
            f"{ref_context}"
        )

        response = await self.llm.generate(
            prompt=user_message,
            system_prompt=system_prompt,
            max_tokens=4096,
            temperature=0.8,
        )

        # Parse JSON from response
        self.concepts = self._parse_concepts(response)
        LOG.info("Generated %d concepts", len(self.concepts))

        self._emit_progress(self.PHASE_CONCEPTS, 100, "Concepts generated")
        return self.concepts

    def _parse_concepts(self, response: str) -> List[Dict[str, Any]]:
        """Extract JSON concepts from LLM response."""
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
                return data.get("concepts", [])
        except json.JSONDecodeError:
            pass

        # Fallback: generate default concept
        LOG.warning("Failed to parse concepts from LLM response")
        return [{
            "title": "Neon Dreams",
            "style": "Synthwave with neon grids and cosmic visuals",
            "palette": ["#0b1629", "#f59e0b", "#06b6d4"],
            "narrative": "Journey through a neon-lit futuristic landscape",
            "key_moments": {
                "intro": "Dark cityscape with neon signs flickering to life",
                "verse": "Camera glides through neon corridors",
                "chorus": "Explosion of color and light",
                "outro": "Fading into the distance"
            },
            "complexity": "medium"
        }]

    # ── Phase 3: Reference Images ────────────────────────────────────

    async def generate_reference_images(
        self,
        concept: Dict[str, Any],
        image_prompt: Optional[str] = None,
    ) -> List[str]:
        """Generate reference images for the selected concept.

        Args:
            concept: Selected concept dict.
            image_prompt: Optional override prompt for image generation.
        """
        self._emit_progress(self.PHASE_REFERENCES, 10, "Generating reference images...")

        palette_str = ", ".join(concept["palette"])
        ref_prompt = (
            image_prompt or (
                f"{concept['style']}, {concept['narrative']}, "
                f"color palette: {palette_str}, cinematic lighting, "
                f"80s retro sci-fi aesthetic, detailed, high quality"
            )
        )

        # Generate 2-3 reference images with different seeds
        seeds = list(range(1000, 1003))
        self.reference_images = []

        for i, seed in enumerate(seeds):
            progress = int(((i + 1) / len(seeds)) * 90) + 10
            self._emit_progress(
                self.PHASE_REFERENCES,
                progress,
                f"Generating reference image {i + 1}/{len(seeds)}",
            )

            # Try z-image turbo first (fast), fall back to FLUX
            try:
                img_path = await self.comfyui.generate_image_turbo(
                    prompt=ref_prompt,
                    width=1024,
                    height=576,
                    seed=seed,
                )
            except Exception as e:
                LOG.warning("z-image turbo failed: %s, falling back to FLUX", e)
                img_path = await self.comfyui.generate_image_flux(
                    prompt=ref_prompt,
                    width=1024,
                    height=576,
                    seed=seed,
                )

            if img_path:
                # Copy to session dir for persistence
                session_dir = self.output_dir / "references"
                session_dir.mkdir(parents=True, exist_ok=True)
                dest = session_dir / f"ref_{i}.png"

                # Handle relative paths from ComfyUI
                if not Path(img_path).is_absolute():
                    comfyui_outputs = Path(__file__).resolve().parents[2] / "outputs"
                    source = comfyui_outputs / img_path
                else:
                    source = Path(img_path)

                if source.exists():
                    shutil.copy(source, dest)
                    self.reference_images.append(str(dest))
                    LOG.info("Reference image saved: %s", dest)

        self._emit_progress(self.PHASE_REFERENCES, 100, "Reference images generated")
        return self.reference_images

    async def _ensure_character_lock(self, concept: Dict[str, Any]) -> str:
        """Stable character/style bible reused across every frame for identity consistency."""
        if self.character_lock:
            return self.character_lock

        ref_note = ""
        if self.reference_images:
            names = ", ".join(Path(r).name for r in self.reference_images[:4])
            ref_note = f" Match uploaded reference stills exactly ({names})."

        style = concept.get("style") or "cinematic"
        title = concept.get("title") or "the lead performer"
        narrative = (concept.get("narrative") or "")[:280]

        if self.llm:
            try:
                raw = await self.llm.generate(
                    prompt=(
                        f"Write a CHARACTER LOCK paragraph (45-70 words) for a music video.\n"
                        f"Concept: {title}\nStyle: {style}\nNarrative: {narrative}\n"
                        f"{'User provided reference images — lock face, hair, outfit, age, and body type.' if ref_note else ''}\n"
                        f"Describe ONE recurring lead character (or band) that must look identical in every shot. "
                        f"No scene-specific action. Plain text only."
                    ),
                    system_prompt=(
                        "You write tight character-consistency locks for AI image/video pipelines. "
                        "Freeze identity details. No markdown."
                    ),
                    max_tokens=256,
                    temperature=0.35,
                )
                lock = (raw or "").strip().split("\n")[0].strip()
            except Exception as e:
                LOG.warning("Character lock LLM failed: %s", e)
                lock = ""
        else:
            lock = ""

        if not lock:
            lock = (
                f"Same lead character throughout: consistent face, hair, outfit, and body type; "
                f"style {style}; never change identity or morph features between shots."
            )
        if ref_note:
            lock = f"{lock}{ref_note}"

        self.character_lock = lock
        LOG.info("Character lock: %s", lock[:160])
        return lock

    # ── Phase 4: Storyboard ──────────────────────────────────────────

    async def generate_storyboard(
        self,
        concept: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Generate storyboard frames covering the FULL song timeline."""
        self._emit_progress(self.PHASE_STORYBOARD, 10, "Generating full-song storyboard...")

        if self.llm is None:
            raise RuntimeError("No LLM backend available.")

        analysis = self.audio_analysis
        palette_str = ", ".join(concept["palette"])
        character_lock = await self._ensure_character_lock(concept)
        scenes = analysis.get("scenes") or analysis.get("segments") or []
        song_dur = float(analysis.get("duration") or 0)
        if not scenes:
            scenes = [
                {
                    "index": 0,
                    "name": "full",
                    "start": 0.0,
                    "end": song_dur or 30.0,
                    "duration": song_dur or 30.0,
                }
            ]

        # Batch prompt generation for many scenes (cloud cost + latency)
        storyboard: List[Dict[str, Any]] = []
        batch_size = int(os.getenv("VIDMUSE_STORYBOARD_BATCH", "6"))

        for batch_start in range(0, len(scenes), batch_size):
            batch = scenes[batch_start : batch_start + batch_size]
            progress = int(((batch_start + len(batch)) / len(scenes)) * 80) + 10
            self._emit_progress(
                self.PHASE_STORYBOARD,
                progress,
                f"Storyboarding scenes {batch_start + 1}-{batch_start + len(batch)}/{len(scenes)}",
            )

            scene_lines = []
            for s in batch:
                key = concept.get("key_moments") or {}
                moment = key.get(s["name"], f"{s['name']} energy")
                scene_lines.append(
                    f"- index={s.get('index', batch_start)} "
                    f"name={s['name']} "
                    f"time={s['start']:.1f}-{s['end']:.1f}s "
                    f"moment={moment}"
                )

            system_prompt = (
                "You are an expert storyboard artist for full-length music videos. "
                "For EACH scene, write one vivid image-generation prompt (80-150 words) "
                "with composition, lighting, palette, and camera feel. "
                "EVERY prompt must include the same CHARACTER LOCK details verbatim so the "
                "performer's face, hair, and outfit stay identical across shots. "
                "Respond with ONLY a JSON array of objects: "
                '[{"index":0,"prompt":"..."}, ...] matching the given indexes.'
            )
            user_message = (
                f"Song duration: {song_dur:.1f}s | {analysis.get('tempo', 0):.0f} BPM\n"
                f"CONCEPT: {concept.get('title')}\n"
                f"STYLE: {concept.get('style')}\n"
                f"PALETTE: {palette_str}\n"
                f"CHARACTER LOCK (include in every prompt): {character_lock}\n"
                f"SCENES:\n" + "\n".join(scene_lines)
            )

            raw = await self.llm.generate(
                prompt=user_message,
                system_prompt=system_prompt,
                max_tokens=4096,
                temperature=0.7,
            )
            prompts_by_idx: Dict[int, str] = {}
            try:
                text = raw.strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and "prompt" in item:
                            prompts_by_idx[int(item.get("index", len(prompts_by_idx)))] = (
                                str(item["prompt"]).strip()
                            )
            except Exception as e:
                LOG.warning("Storyboard batch JSON parse failed: %s", e)

            for offset, s in enumerate(batch):
                idx = int(s.get("index", batch_start + offset))
                prompt_text = prompts_by_idx.get(idx)
                if not prompt_text:
                    # Per-scene fallback if batch parse fails
                    key_moment = (concept.get("key_moments") or {}).get(
                        s["name"], "Cinematic visual"
                    )
                    prompt_text = await self.llm.generate(
                        prompt=(
                            f"Storyboard prompt for {s['name']} at {s['start']:.1f}s. "
                            f"Concept {concept.get('title')}, style {concept.get('style')}, "
                            f"palette {palette_str}, moment: {key_moment}. "
                            f"CHARACTER LOCK: {character_lock}. "
                            f"Respond with ONLY the prompt."
                        ),
                        system_prompt=(
                            "You write image prompts for AI storyboards. 80-150 words. "
                            "Always include the character lock. No markdown."
                        ),
                        max_tokens=512,
                        temperature=0.7,
                    )
                    prompt_text = prompt_text.strip()

                if character_lock and character_lock.lower() not in prompt_text.lower():
                    prompt_text = f"{prompt_text.rstrip('. ')}. {character_lock}"

                storyboard.append(
                    {
                        "id": f"frame_{len(storyboard):02d}",
                        "segment": s["name"],
                        "timestamp": float(s["start"]),
                        "duration": float(s.get("duration") or (s["end"] - s["start"])),
                        "end": float(s["end"]),
                        "prompt": prompt_text,
                        "key_moment": (concept.get("key_moments") or {}).get(
                            s["name"], s["name"]
                        ),
                    }
                )

        self.storyboard = storyboard
        self._emit_progress(
            self.PHASE_STORYBOARD,
            100,
            f"Storyboard ready: {len(storyboard)} scenes over {song_dur:.0f}s",
        )
        return storyboard

    # ── Phase 5: Storyboard Images ───────────────────────────────────

    async def generate_storyboard_images(
        self,
        concept: Dict[str, Any],
    ) -> List[str]:
        """Generate images for each storyboard frame via ComfyUI."""
        self._emit_progress(self.PHASE_STORYBOARD, 10, "Generating storyboard images...")

        session_dir = self.output_dir / "storyboard"
        session_dir.mkdir(parents=True, exist_ok=True)

        generated = []
        palette_str = ", ".join(concept["palette"])
        session_id = self.output_dir.name

        for i, frame in enumerate(self.storyboard):
            progress = int(((i + 1) / len(self.storyboard)) * 90) + 10
            self._emit_progress(
                self.PHASE_STORYBOARD,
                progress,
                f"Rendering storyboard frame {i + 1}/{len(self.storyboard)}",
            )

            character_lock = await self._ensure_character_lock(concept)
            # Enhance prompt with style + identity lock
            enhanced_prompt = (
                f"{frame['prompt']}, {concept['style']}, "
                f"color palette: {palette_str}, cinematic lighting, "
                f"identical character consistency, {character_lock}"
            )

            img_path = None
            try:
                img_path = await self.comfyui.generate_image_turbo(
                    prompt=enhanced_prompt,
                    width=1024,
                    height=576,
                )
            except Exception as e:
                LOG.warning("z-image turbo failed for frame %d: %s", i, e)

            if not img_path:
                try:
                    img_path = await self.comfyui.generate_image_flux(
                        prompt=enhanced_prompt,
                        width=1024,
                        height=576,
                    )
                except Exception as e:
                    LOG.warning("FLUX fallback failed for frame %d: %s", i, e)

            if img_path:
                dest = session_dir / f"frame_{i:02d}.png"
                source = Path(img_path)
                if source.exists():
                    shutil.copy(source, dest)
                    frame["image_path"] = str(dest)
                    frame["image_url"] = (
                        f"/api/session/{session_id}/asset/storyboard/{dest.name}"
                    )
                    generated.append(
                        {
                            "frame_id": frame["id"],
                            "path": str(dest),
                            "url": frame["image_url"],
                        }
                    )
                    LOG.info("Storyboard image saved: %s", dest)
                else:
                    LOG.warning("Comfy output missing on disk for frame %d: %s", i, source)
            else:
                LOG.warning("No image generated for frame %s", frame.get("id", i))

        self._emit_progress(self.PHASE_STORYBOARD, 100, "Storyboard images generated")
        return generated

    # ── Phase 6: Video Clips ─────────────────────────────────────────

    async def generate_video_clips(
        self,
        concept: Dict[str, Any],
    ) -> List[str]:
        """Generate video clips from storyboard images via ComfyUI (LTX-2.3 or HuMo)."""
        backend = (os.getenv("COMFY_VIDEO_BACKEND") or "ltx23").strip().lower()
        using_ltx = backend not in ("humo", "wan", "wan-humo")
        label = "LTX-2.3 A/V" if using_ltx else "HuMo lip-sync"
        self._emit_progress(self.PHASE_VIDEO, 10, f"Generating {label} video clips...")

        session_dir = self.output_dir / "videos"
        session_dir.mkdir(parents=True, exist_ok=True)

        generated = []
        session_id = self.output_dir.name
        character_lock = await self._ensure_character_lock(concept)
        if self._video_seed_base is None:
            self._video_seed_base = int(time.time()) % (2**31)

        for i, frame in enumerate(self.storyboard):
            if not frame.get("image_path"):
                LOG.warning("No image for frame %s, skipping video", frame["id"])
                continue

            progress = int(((i + 1) / len(self.storyboard)) * 90) + 10
            self._emit_progress(
                self.PHASE_VIDEO,
                progress,
                f"Animating clip {i + 1}/{len(self.storyboard)} ({label})",
            )

            if self.llm:
                motion_prompt = await self.llm.generate(
                    prompt=(
                        f"Rewrite this storyboard beat as a short image+audio→video prompt "
                        f"(35-70 words). Keep the SAME character identity. "
                        f"Emphasize natural motion, face consistency, and audio-reactive "
                        f"performance (singing / groove / energy — not random talking heads).\n"
                        f"Storyboard: {frame['prompt']}\n"
                        f"CHARACTER LOCK: {character_lock}\n"
                        f"Style: {concept['style']}"
                    ),
                    max_tokens=512,
                    temperature=0.65,
                )
            else:
                motion_prompt = frame["prompt"]
            motion_prompt = f"{motion_prompt.strip()}. {character_lock}"

            audio_src = getattr(self, "audio_path", None)
            if not audio_src or not Path(audio_src).is_file():
                LOG.warning(
                    "No audio for A/V i2v on frame %s — skipping video",
                    frame.get("id", i),
                )
                continue

            scene_dur = float(frame.get("duration") or 6.0)
            if using_ltx:
                clip_secs = float(os.getenv("COMFY_LTX_CLIP_SECONDS", "4.0"))
                gen_secs = min(max(2.0, clip_secs), min(scene_dur, 4.5))
                fps = float(os.getenv("COMFY_LTX_FPS", "24"))
                raw_frames = max(9, int(round(gen_secs * fps)))
                if (raw_frames - 1) % 8 != 0:
                    raw_frames = ((raw_frames - 1) // 8) * 8 + 1
                width, height = 768, 512
            else:
                clip_secs = float(os.getenv("COMFY_HUMO_CLIP_SECONDS", "2.0"))
                gen_secs = min(max(1.5, clip_secs), min(scene_dur, 4.0))
                fps = float(os.getenv("COMFY_HUMO_FPS", "25"))
                raw_frames = max(9, int(round(gen_secs * fps)))
                if (raw_frames - 1) % 4 != 0:
                    raw_frames = ((raw_frames - 1) // 4) * 4 + 1
                width, height = 512, 512

            start_t = float(frame.get("timestamp") or 0.0)
            audio_slice = session_dir / f"audio_{i:02d}.wav"
            try:
                # LTX prefers higher-quality audio; HuMo Whisper historically used 16k mono
                sample_rate = "44100" if using_ltx else "16000"
                channels = "2" if using_ltx else "1"
                self._slice_audio(
                    audio_src,
                    audio_slice,
                    start_t,
                    gen_secs,
                    sample_rate=sample_rate,
                    channels=channels,
                )
            except Exception as e:
                LOG.warning("Audio slice failed for frame %d: %s — using full track", i, e)
                audio_slice = Path(audio_src)

            try:
                video_path = await self.comfyui.generate_video_i2v(
                    image_path=frame["image_path"],
                    prompt=motion_prompt,
                    num_frames=raw_frames,
                    seed=(self._video_seed_base or 0) + i,
                    audio_path=str(audio_slice),
                    duration=gen_secs,
                    width=width,
                    height=height,
                )
            except Exception as e:
                LOG.warning("%s failed for clip %d: %s", label, i, e)
                continue

            if video_path:
                dest = session_dir / f"clip_{i:02d}.mp4"
                source = Path(video_path)

                if source.exists():
                    shutil.copy(source, dest)
                    frame["video_path"] = str(dest)
                    frame["clip_duration"] = gen_secs
                    # Editor fills/loops this clip across the full scene slot
                    frame["target_duration"] = scene_dur
                    clip_url = f"/api/session/{session_id}/asset/video/{dest.name}"
                    frame["video_url"] = clip_url
                    generated.append(
                        {
                            "frame_id": frame["id"],
                            "path": str(dest),
                            "url": clip_url,
                            "timestamp": start_t,
                            "duration": scene_dur,
                            "clip_duration": gen_secs,
                        }
                    )
                    LOG.info(
                        "Video clip saved: %s (scene %.1fs, gen %.1fs)",
                        dest,
                        scene_dur,
                        gen_secs,
                    )
                else:
                    LOG.warning("Comfy video output missing on disk for clip %d: %s", i, source)

        self._emit_progress(self.PHASE_VIDEO, 100, "Video clips generated")
        return generated

    @staticmethod
    def _slice_audio(
        audio_path: str | Path,
        dest: Path,
        start_sec: float,
        duration_sec: float,
        sample_rate: str = "16000",
        channels: str = "1",
    ) -> Path:
        """Extract a short WAV slice with ffmpeg for A/V conditioning."""
        import subprocess

        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, start_sec):.3f}",
            "-t",
            f"{max(0.5, duration_sec):.3f}",
            "-i",
            str(audio_path),
            "-ac",
            channels,
            "-ar",
            sample_rate,
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return dest

    # ── Phase 7: Final Assembly ──────────────────────────────────────

    async def assemble_final_video(
        self,
        video_clips: List[Any],
        audio_path: str | Path,
    ) -> str:
        """Assemble final MP4 spanning the full song duration."""
        self._emit_progress(self.PHASE_ASSEMBLE, 10, "Assembling full-song video...")

        session_id = str(uuid.uuid4())[:8]
        output_path = self.output_dir / f"vidmuse_{session_id}.mp4"

        # Prefer rich clip specs from storyboard (path + scene duration)
        specs: List[Dict[str, Any]] = []
        for frame in self.storyboard:
            if isinstance(frame, dict) and frame.get("video_path"):
                specs.append(
                    {
                        "path": frame["video_path"],
                        "duration": float(
                            frame.get("target_duration")
                            or frame.get("duration")
                            or 2.0
                        ),
                        "start": float(frame.get("timestamp") or 0.0),
                    }
                )
        if not specs:
            for item in video_clips:
                if isinstance(item, dict) and item.get("path"):
                    specs.append(
                        {
                            "path": item["path"],
                            "duration": float(item.get("duration") or 0),
                        }
                    )
                elif isinstance(item, str):
                    specs.append({"path": item, "duration": 0})

        song_dur = float((self.audio_analysis or {}).get("duration") or 0) or None
        editor = VideoEditor(audio_path)
        final_path = editor.assemble(
            specs,
            output_path,
            song_duration=song_dur,
        )

        self.final_video_path = final_path
        self._emit_progress(self.PHASE_ASSEMBLE, 100, "Full-song video complete!")
        LOG.info("Final video: %s", final_path)
        return final_path

    # ── Master Pipeline ──────────────────────────────────────────────

    async def generate(
        self,
        audio_path: str | Path,
        text_direction: str,
        reference_images: Optional[List[str]] = None,
        reference_image_prompt: Optional[str] = None,
        concept_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run the full autonomous pipeline.

        Args:
            audio_path: Path to uploaded MP3/audio file.
            text_direction: Creative direction / brief from user.
            reference_images: Optional paths to reference images.
            reference_image_prompt: Optional prompt to generate reference images.
            concept_index: Optional index to select a specific concept (0-2).

        Returns:
            Dict with session_id, final_video_path, and pipeline status.
        """
        session_id = str(uuid.uuid4())[:8]
        self.output_dir.mkdir(parents=True, exist_ok=True)

        LOG.info("=== Starting pipeline session %s ===", session_id)

        # Validate LLM backend
        if self.llm is None:
            return {
                "error": "No LLM backend available. Install llama-cpp-python with a GGUF model, "
                         "or start Ollama with a model.",
                "status": "failed",
            }

        # Phase 1: Audio analysis
        await self.analyze_audio(audio_path)

        # Phase 2: Concept generation
        concepts = await self.generate_concepts(text_direction, reference_images)

        # Phase 3: Select concept
        if concept_index is not None and 0 <= concept_index < len(concepts):
            self.selected_concept = concepts[concept_index]
        else:
            # Auto-select: pick the first concept
            self.selected_concept = concepts[0]

        # Phase 4: Generate reference images
        if reference_image_prompt:
            await self.generate_reference_images(self.selected_concept, reference_image_prompt)
        else:
            # Generate default reference images
            await self.generate_reference_images(self.selected_concept)

        # Phase 5: Storyboard
        await self.generate_storyboard(self.selected_concept)

        # Phase 6: Storyboard images
        await self.generate_storyboard_images(self.selected_concept)

        # Phase 7: Video clips
        video_clips = await self.generate_video_clips(self.selected_concept)

        # Phase 8: Final assembly
        if video_clips:
            final = await self.assemble_final_video(video_clips, audio_path)
            return {
                "session_id": session_id,
                "status": "done",
                "final_video_path": final,
                "concepts": concepts,
                "selected_concept_index": concept_index if concept_index is not None else 0,
                "storyboard_count": len(self.storyboard),
                "video_clips_count": len(video_clips),
            }
        else:
            return {
                "session_id": session_id,
                "status": "failed",
                "error": "No video clips generated",
                "concepts": concepts,
                "selected_concept_index": concept_index if concept_index is not None else 0,
            }
