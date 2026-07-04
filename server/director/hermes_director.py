"""Hermes Director — autonomous music video generation orchestrator.

The director drives the full pipeline:
1. Analyze uploaded audio (tempo, beats, energy, segments)
2. Generate video concepts via LLM (3 distinct ideas)
3. Generate reference images via ComfyUI z-image turbo
4. Generate storyboard prompts via LLM
5. Generate storyboard images via ComfyUI
6. Generate video motion prompts via LLM
7. Generate video clips via ComfyUI Wan i2v
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
        self.concepts: List[Dict[str, Any]] = []
        self.reference_images: List[str] = []
        self.storyboard: List[Dict[str, Any]] = []
        self.selected_concept: Optional[Dict[str, Any]] = None
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

    # ── Phase 4: Storyboard ──────────────────────────────────────────

    async def generate_storyboard(
        self,
        concept: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Generate storyboard frames for the concept."""
        self._emit_progress(self.PHASE_STORYBOARD, 10, "Generating storyboard...")

        if self.llm is None:
            raise RuntimeError("No LLM backend available.")

        analysis = self.audio_analysis
        palette_str = ", ".join(concept["palette"])

        storyboard = []
        segments = analysis["segments"]

        for i, seg in enumerate(segments):
            seg_name = seg["name"]
            progress = int(((i + 1) / len(segments)) * 80) + 10
            self._emit_progress(
                self.PHASE_STORYBOARD,
                progress,
                f"Creating storyboard for {seg_name}",
            )

            # Generate storyboard prompt via LLM
            system_prompt = """You are an expert storyboard artist for music videos.
Create detailed image generation prompts that translate music video concepts into visual descriptions.
Be specific about composition, lighting, color, mood, and camera angle.
Each prompt should be 100-200 words, suitable for an AI image generator.
Respond with ONLY the prompt text, no markdown or code blocks."""

            key_moment = concept["key_moments"].get(seg_name, "Cinematic visual")

            user_message = (
                f"Create a storyboard prompt for the {seg_name} segment.\n\n"
                f"CONCEPT: {concept['title']}\n"
                f"STYLE: {concept['style']}\n"
                f"COLOR PALETTE: {palette_str}\n"
                f"SEGMENT: {seg_name} ({seg['start']:.1f}s - {seg['end']:.1f}s)\n"
                f"KEY MOMENT: {key_moment}\n\n"
                f"Audio context: {analysis['tempo']:.0f} BPM, "
                f"brightness {analysis['brightness']:.1f}"
            )

            prompt_text = await self.llm.generate(
                prompt=user_message,
                system_prompt=system_prompt,
                max_tokens=1024,
                temperature=0.7,
            )

            storyboard.append({
                "id": f"frame_{i:02d}",
                "segment": seg_name,
                "timestamp": seg["start"],
                "duration": seg["end"] - seg["start"],
                "prompt": prompt_text.strip(),
                "key_moment": key_moment,
            })

        self.storyboard = storyboard
        self._emit_progress(self.PHASE_STORYBOARD, 100, "Storyboard created")
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

        for i, frame in enumerate(self.storyboard):
            progress = int(((i + 1) / len(self.storyboard)) * 90) + 10
            self._emit_progress(
                self.PHASE_STORYBOARD,
                progress,
                f"Rendering storyboard frame {i + 1}/{len(self.storyboard)}",
            )

            # Enhance prompt with style context
            enhanced_prompt = (
                f"{frame['prompt']}, {concept['style']}, "
                f"color palette: {palette_str}, cinematic lighting, "
                f"80s retro sci-fi aesthetic"
            )

            try:
                img_path = await self.comfyui.generate_image_turbo(
                    prompt=enhanced_prompt,
                    width=1024,
                    height=576,
                )
            except Exception as e:
                LOG.warning("z-image turbo failed for frame %d: %s", i, e)
                img_path = await self.comfyui.generate_image_flux(
                    prompt=enhanced_prompt,
                    width=1024,
                    height=576,
                )

            if img_path:
                dest = session_dir / f"frame_{i:02d}.png"
                source = Path(img_path)
                if not source.is_absolute():
                    comfyui_outputs = Path(__file__).resolve().parents[2] / "outputs"
                    source = comfyui_outputs / img_path

                if source.exists():
                    shutil.copy(source, dest)
                    frame["image_path"] = str(dest)
                    generated.append(str(dest))
                    LOG.info("Storyboard image saved: %s", dest)

        self._emit_progress(self.PHASE_STORYBOARD, 100, "Storyboard images generated")
        return generated

    # ── Phase 6: Video Clips ─────────────────────────────────────────

    async def generate_video_clips(
        self,
        concept: Dict[str, Any],
    ) -> List[str]:
        """Generate video clips from storyboard images via ComfyUI Wan i2v."""
        self._emit_progress(self.PHASE_VIDEO, 10, "Generating video clips...")

        session_dir = self.output_dir / "videos"
        session_dir.mkdir(parents=True, exist_ok=True)

        generated = []
        palette_str = ", ".join(concept["palette"])

        for i, frame in enumerate(self.storyboard):
            if not frame.get("image_path"):
                LOG.warning("No image for frame %s, skipping video", frame["id"])
                continue

            progress = int(((i + 1) / len(self.storyboard)) * 90) + 10
            self._emit_progress(
                self.PHASE_VIDEO,
                progress,
                f"Animating clip {i + 1}/{len(self.storyboard)}",
            )

            # Generate motion prompt via LLM
            if self.llm:
                motion_prompt = await self.llm.generate(
                    prompt=(
                        f"Convert this storyboard description into a video motion prompt "
                        f"(30-60 words): {frame['prompt']}\n\n"
                        f"Add camera movement, motion description, and temporal details.\n"
                        f"Style: {concept['style']}"
                    ),
                    max_tokens=512,
                    temperature=0.8,
                )
            else:
                motion_prompt = frame["prompt"]

            try:
                video_path = await self.comfyui.generate_video_i2v(
                    image_path=frame["image_path"],
                    prompt=motion_prompt,
                    num_frames=81,  # ~3.3s at 24fps
                )
            except Exception as e:
                LOG.warning("Wan i2v failed for clip %d: %s", i, e)
                continue

            if video_path:
                dest = session_dir / f"clip_{i:02d}.mp4"
                source = Path(video_path)
                if not source.is_absolute():
                    comfyui_outputs = Path(__file__).resolve().parents[2] / "outputs"
                    source = comfyui_outputs / video_path

                if source.exists():
                    shutil.copy(source, dest)
                    frame["video_path"] = str(dest)
                    generated.append(str(dest))
                    LOG.info("Video clip saved: %s", dest)

        self._emit_progress(self.PHASE_VIDEO, 100, "Video clips generated")
        return generated

    # ── Phase 7: Final Assembly ──────────────────────────────────────

    async def assemble_final_video(
        self,
        video_clips: List[str],
        audio_path: str | Path,
    ) -> str:
        """Assemble final MP4 synced to music beats."""
        self._emit_progress(self.PHASE_ASSEMBLE, 10, "Assembling final video...")

        session_id = str(uuid.uuid4())[:8]
        output_path = self.output_dir / f"vidmuse_{session_id}.mp4"

        editor = VideoEditor(audio_path)
        final_path = editor.assemble(video_clips, output_path)

        self.final_video_path = final_path
        self._emit_progress(self.PHASE_ASSEMBLE, 100, "Final video complete!")
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
