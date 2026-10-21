"""Hermes Director — autonomous music video generation orchestrator.

The director drives the full pipeline:
1. Analyze uploaded audio (tempo, beats, energy, segments)
2. Generate video concepts via LLM (3 distinct ideas)
3. Generate reference images via ComfyUI z-image turbo
4. Generate storyboard prompts via LLM
5. Generate storyboard images via ComfyUI
6. Generate video motion prompts via LLM
7. Generate video clips via ComfyUI MiniMax H3 / InfiniteTalk / LTX-2.3 IA2V / HuMo
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
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from server.llm.base import LLMBackend
from server.audio_analyzer import AudioAnalyzer
from server.image_generator import ComfyUIClient, _h3_align_frames
from server.video_editor import VideoEditor

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/vidmuse/outputs"))
LOG = logging.getLogger("vidmuse.director")

# Progress callback signature
ProgressCallback = Callable[[str, int, str], None]
FrameReadyCallback = Callable[[int, Dict[str, Any], int], None]


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
        self.identity_sheet_path: Optional[str] = None
        self.performance_mix: str = (
            os.getenv("VIDMUSE_PERFORMANCE_MIX") or "hybrid"
        ).strip().lower()
        self.project_mode: str = "mtv"
        self.comfy_backend: str = self.comfyui.backend
        self.comfy_url: str = self.comfyui.url
        self.storyboard: List[Dict[str, Any]] = []
        self.selected_concept: Optional[Dict[str, Any]] = None
        self.character_lock: str = ""
        self.singer_profile: Dict[str, Any] = {}
        self.video_backend: str = (
            os.getenv("COMFY_VIDEO_BACKEND") or "minimax-h3"
        ).strip().lower()
        self._video_seed_base: Optional[int] = None
        self.final_video_path: Optional[str] = None
        self.timeline_plan: Optional[Dict[str, Any]] = None

        # Optional Home Agent (Playground local-web) — additive to Comfy
        self.home_agent_url: str = ""
        self.home_agent_password: str = ""
        self.use_home_agent_stills: bool = False
        self.use_home_agent_video: bool = False
        self.pipeline_director: str = "cloud_llm"
        self.pipeline_stills: str = "comfy"
        self.pipeline_video: str = "comfy_cloud"
        self.video_model: str = "minimax-h3"

        # Progress tracking
        self._progress_callback: Optional[ProgressCallback] = None
        self._frame_ready_callback: Optional[FrameReadyCallback] = None
        self._storyboard_cancel = False
        self._studio_cancel = False
        self._current_phase = "idle"
        self._current_progress = 0
        self._status = "idle"

    def configure_comfy(self, backend: str, url: Optional[str] = None) -> None:
        """Switch this session between cloud, local, and AI Playground Comfy."""
        normalized = (backend or "cloud").strip().lower()
        if normalized not in ("cloud", "local", "ai-playground"):
            normalized = "cloud"
        self.comfyui = ComfyUIClient(backend=normalized, url=url)
        self.comfy_backend = normalized
        self.comfy_url = self.comfyui.url
        LOG.info("Session Comfy backend: %s (%s)", normalized, self.comfy_url)

    def configure_home_agent(
        self,
        url: Optional[str],
        password: Optional[str] = None,
        *,
        use_stills: bool = False,
        use_video: bool = False,
    ) -> None:
        """Attach Home Agent LAN settings for optional still / H3 generation."""
        self.home_agent_url = (url or "").strip().rstrip("/")
        self.home_agent_password = (password or "").strip()
        self.use_home_agent_stills = bool(use_stills) and bool(self.home_agent_url)
        self.use_home_agent_video = bool(use_video) and bool(self.home_agent_url)
        LOG.info(
            "Home Agent stills=%s video=%s url=%s",
            self.use_home_agent_stills,
            self.use_home_agent_video,
            self.home_agent_url or "(none)",
        )

    def configure_pipeline(
        self,
        *,
        director: str = "cloud_llm",
        stills: str = "comfy",
        video: str = "comfy_cloud",
        video_model: str = "minimax-h3",
    ) -> None:
        self.pipeline_director = director
        self.pipeline_stills = stills
        self.pipeline_video = video
        from server.pipeline import normalize_video_model

        self.video_model = normalize_video_model(video_model)
        self.use_home_agent_stills = stills == "home_agent" and bool(self.home_agent_url)
        self.use_home_agent_video = video == "home_agent" and bool(self.home_agent_url)

    async def _generate_image_via_home_agent(
        self,
        prompt: str,
        *,
        job_type: str = "storyboard_still",
        dest_dir: Optional[Path] = None,
        reference_image_paths: Optional[List[str]] = None,
        extras: Optional[Dict[str, Any]] = None,
        width: int = 768,
        height: int = 448,
    ) -> Optional[str]:
        """Ask Playground Home Agent to generate one still; return local path."""
        if not self.home_agent_url:
            raise RuntimeError("Home Agent URL not configured")
        if not self.home_agent_password:
            raise RuntimeError("Home Agent password not configured")

        from server.home_agent.client import HomeAgentClient
        from server.home_agent.jobs import encode_local_images, run_media_job

        out_dir = dest_dir or (self.output_dir / "home_agent_stills")
        out_dir.mkdir(parents=True, exist_ok=True)
        client = HomeAgentClient(
            self.home_agent_url,
            self.home_agent_password,
            timeout=float(os.getenv("HOME_AGENT_TIMEOUT", "300")),
        )
        job_extras: Dict[str, Any] = {
            "width": width,
            "height": height,
            "no_onscreen_text": True,
        }
        if extras:
            job_extras.update(extras)
        images = encode_local_images(reference_image_paths)
        result = await run_media_job(
            client,
            job_type=job_type,
            prompt=prompt,
            dest_dir=out_dir,
            extras=job_extras,
            images=images or None,
            wait_timeout=float(os.getenv("HOME_AGENT_TIMEOUT", "300")),
        )
        saved = result.get("saved_paths") or []
        if not saved:
            LOG.warning(
                "Home Agent still job produced no image (reply=%r)",
                (result.get("reply_text") or "")[:200],
            )
            return None
        return str(saved[0])

    def on_frame_ready(self, callback: FrameReadyCallback) -> None:
        """Register callback when a storyboard frame image finishes rendering."""
        self._frame_ready_callback = callback

    def clear_storyboard_cancel(self) -> None:
        self._storyboard_cancel = False

    def request_storyboard_cancel(self) -> None:
        self._storyboard_cancel = True
        LOG.info("Storyboard render cancel requested")

    def clear_studio_cancel(self) -> None:
        self._studio_cancel = False

    def request_studio_cancel(self) -> None:
        self._studio_cancel = True
        LOG.info("Studio render cancel requested")

    def _emit_frame_ready(self, index: int, frame: Dict[str, Any], total: int) -> None:
        if self._frame_ready_callback:
            self._frame_ready_callback(index, frame, total)

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

    async def _extract_lyric_brief(self) -> Dict[str, Any]:
        """Structured lyric theme brief used to ground concepts."""
        analysis = self.audio_analysis or {}
        cached = analysis.get("lyric_brief")
        if isinstance(cached, dict) and cached.get("themes"):
            return cached

        from server.lyrics import compact_lyrics

        lyrics = compact_lyrics(analysis.get("lyrics") or "", max_chars=3500)
        title = analysis.get("track_title") or ""
        if not lyrics and not title:
            return {}

        brief: Dict[str, Any] = {}
        if self.llm and lyrics:
            try:
                raw = await self.llm.generate(
                    prompt=(
                        f"Track title: {title or '(unknown)'}\n\n"
                        f"LYRICS:\n{lyrics}\n\n"
                        "Extract the song's meaning for a music video. "
                        "Respond with ONLY JSON:\n"
                        "{\n"
                        '  "themes": ["...", "...", "..."],\n'
                        '  "setting": "...",\n'
                        '  "characters": ["..."],\n'
                        '  "emotions": ["..."],\n'
                        '  "key_phrases": ["quote 1", "quote 2", "quote 3", "quote 4"],\n'
                        '  "visual_motifs": ["...", "...", "..."],\n'
                        '  "do_not": ["unrelated tropes to avoid"]\n'
                        "}"
                    ),
                    system_prompt=(
                        "You extract literal thematic structure from song lyrics. "
                        "Stay faithful to the words. No markdown."
                    ),
                    max_tokens=900,
                    temperature=0.2,
                )
                text = (raw or "").strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    parsed = json.loads(text[start:end])
                    if isinstance(parsed, dict):
                        brief = parsed
            except Exception as e:
                LOG.warning("Lyric brief extraction failed: %s", e)

        if not brief:
            # Lightweight keyword fallback from lyrics/title
            words = re.findall(r"[A-Za-z']{4,}", f"{title} {lyrics}".lower())
            stop = {
                "that", "this", "with", "from", "your", "have", "will", "just",
                "when", "what", "they", "them", "were", "been", "into", "about",
                "there", "their", "would", "could", "should", "like", "know",
            }
            motifs = []
            seen = set()
            for w in words:
                if w in stop or w in seen:
                    continue
                seen.add(w)
                motifs.append(w)
                if len(motifs) >= 8:
                    break
            brief = {
                "themes": motifs[:4] or [title or "performance"],
                "setting": title or "performance stage",
                "characters": ["lead singer"],
                "emotions": ["intensity"],
                "key_phrases": [],
                "visual_motifs": motifs[:6],
                "do_not": ["unrelated neon cyberpunk", "generic sci-fi"],
            }

        analysis["lyric_brief"] = brief
        self.audio_analysis = analysis
        return brief

    async def generate_concepts(
        self,
        text_direction: str,
        reference_images: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate 3 video concepts based on lyrics + text direction."""
        self._emit_progress(self.PHASE_CONCEPTS, 10, "Reading lyric themes…")

        if self.llm is None:
            raise RuntimeError("No LLM backend available. Install llama.cpp or start Ollama.")

        if reference_images:
            self.reference_images = list(reference_images)

        # Build context
        analysis = self.audio_analysis
        analysis_str = (
            f"Duration: {analysis.get('duration', 0):.1f}s | Tempo: {analysis.get('tempo', 0):.0f} BPM "
            f"| Beats: {analysis.get('beat_count', 0)} | Brightness: {analysis.get('brightness', 0):.1f}"
        )
        segments_str = ", ".join(f"{s['name']} ({s['start']:.1f}s-{s['end']:.1f}s)"
                                  for s in analysis.get("segments") or [])
        track_title = analysis.get("track_title") or ""
        from server.lyrics import compact_lyrics

        lyrics = compact_lyrics(analysis.get("lyrics") or "", max_chars=3200)
        lyric_brief = await self._extract_lyric_brief()
        self._emit_progress(self.PHASE_CONCEPTS, 35, "Drafting lyric-grounded concepts…")

        brief_block = ""
        if lyric_brief:
            brief_block = (
                "\n\nLYRIC THEME BRIEF (mandatory — concepts must map to this):\n"
                f"{json.dumps(lyric_brief, ensure_ascii=False)}\n"
            )

        lyrics_block = (
            f"\n\nFULL LYRICS:\n{lyrics}\n"
            if lyrics
            else (
                "\n\nLYRICS: unavailable. Use track title + direction only; "
                "do NOT invent unrelated sci-fi/fantasy plots.\n"
            )
        )

        # Reference images context
        ref_context = ""
        if self.reference_images and self.project_mode != "typography":
            ref_context = (
                "\n\nIDENTITY LOCK: User uploaded reference photo(s) of the singer. "
                "Every concept must feature THIS same person as the lead performer "
                f"({'; '.join(Path(r).name for r in self.reference_images[:4])})."
            )

        singer = self._singer_brief()
        singer_context = (
            f"\n\nSINGER PROFILE: {singer}"
            if singer and self.project_mode != "typography"
            else ""
        )
        framing = (self.singer_profile or {}).get("framing") or "close-up on face and head"
        video_mode = self.video_backend
        if self.project_mode == "typography":
            mode_hint = (
                "This is a TYPOGRAPHY LYRIC VIDEO: lyrics are the protagonist. "
                "Design kinetic word-object metaphors, exact-text quiet zones, graphic "
                "materials, recurring motifs, and directed instrumental arcs. Do not make "
                "the singer or portrait performance the default subject."
            )
        else:
            mode_hint = (
                "Favor intimate face/head performance framing with the same singer in every concept."
                if video_mode
                in (
                    "infinitetalk",
                    "infinite-talk",
                    "wan-infinitetalk",
                    "talk",
                    "humo",
                    "minimax-h3",
                    "minimax_h3",
                    "h3",
                    "minimax",
                )
                else "Favor cinematic music-video framing while keeping the same singer identity."
            )

        system_prompt = """You are SceneMux, an expert AI music video director.
Your #1 job is lyric fidelity. Concepts must clearly come from the lyric theme brief and lyrics.
If a concept could fit a different song after removing the title, it is WRONG.
User creative direction only adjusts tone/camera/style — never replaces lyric meaning.
When reference photos exist, the lead singer is that person in every concept.

Each concept must include:
- title (from lyric themes/phrases)
- style
- palette (3 hex colors)
- narrative (explicitly references lyric story/metaphors)
- lyric_hooks (2-4 short lyric phrases this concept visualizes)
- key_moments (intro/verse/chorus/outro each tied to lyric ideas)
- complexity (simple|medium|complex)
- visual_bible (motion grammar, camera grammar, recurring motifs, prohibited choices)

Respond with ONLY this JSON:
{
  "concepts": [
    {
      "title": "...",
      "style": "...",
      "palette": ["#xxxxxx", "#xxxxxx", "#xxxxxx"],
      "narrative": "...",
      "lyric_hooks": ["...", "..."],
      "key_moments": {
        "intro": "...",
        "verse": "...",
        "chorus": "...",
        "outro": "..."
      },
      "complexity": "simple|medium|complex",
      "visual_bible": {
        "motion_grammar": "...",
        "camera_grammar": "...",
        "recurring_motifs": ["...", "..."],
        "prohibited": ["...", "..."]
      }
    }
  ]
}"""

        user_message = (
            f"Create 3 video concepts for this track.\n\n"
            f"TRACK TITLE: {track_title or '(unknown)'}\n"
            f"PROJECT MODE: {self.project_mode}\n"
            f"DIRECTION (style only): {text_direction}\n\n"
            f"{analysis_str}\n"
            f"Segments: {segments_str}\n"
            f"Preferred framing: {framing}\n"
            f"{mode_hint}"
            f"{brief_block}"
            f"{lyrics_block}"
            f"{singer_context}"
            f"{ref_context}\n\n"
            "Return 3 DISTINCT visual treatments of the SAME lyric themes "
            "(literal / metaphorical / performance-forward). "
            "Reject unrelated neon-city or generic sci-fi unless the lyrics demand it."
        )

        response = await self.llm.generate(
            prompt=user_message,
            system_prompt=system_prompt,
            max_tokens=4096,
            temperature=0.4 if lyrics else 0.7,
        )

        # Parse JSON from response
        self.concepts = self._parse_concepts(response, lyric_brief=lyric_brief, track_title=track_title)
        self.concepts = self._enrich_concepts_with_lyrics(self.concepts, lyric_brief)
        LOG.info("Generated %d concepts", len(self.concepts))

        self._emit_progress(self.PHASE_CONCEPTS, 100, "Concepts generated")
        return self.concepts

    def _enrich_concepts_with_lyrics(
        self,
        concepts: List[Dict[str, Any]],
        lyric_brief: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Ensure every concept carries lyric hooks/themes even if the LLM omitted them."""
        if not concepts:
            return concepts
        brief = lyric_brief or {}
        themes = [str(t) for t in (brief.get("themes") or []) if t][:4]
        hooks = [str(h) for h in (brief.get("key_phrases") or []) if h][:4]
        motifs = [str(m) for m in (brief.get("visual_motifs") or []) if m][:4]
        setting = str(brief.get("setting") or "").strip()
        do_not = [str(d).lower() for d in (brief.get("do_not") or []) if d]

        enriched: List[Dict[str, Any]] = []
        for i, c in enumerate(concepts):
            if not isinstance(c, dict):
                continue
            concept = dict(c)
            existing_hooks = [str(h) for h in (concept.get("lyric_hooks") or []) if h]
            if not existing_hooks and hooks:
                # Rotate hooks so concepts stay distinct
                rotated = hooks[i:] + hooks[:i]
                concept["lyric_hooks"] = rotated[:3]
            elif not existing_hooks and themes:
                concept["lyric_hooks"] = themes[:3]

            narrative = str(concept.get("narrative") or "")
            title = str(concept.get("title") or "")
            blob = f"{title} {narrative}".lower()
            # Soft repair: if narrative ignores themes, prepend a lyric anchor
            if themes and not any(t.lower() in blob for t in themes if len(t) > 3):
                theme_str = ", ".join(themes[:3])
                concept["narrative"] = (
                    f"Visualizes the lyric themes of {theme_str}"
                    + (f" in {setting}" if setting else "")
                    + (f". {narrative}" if narrative else ".")
                ).strip()
            # Flag obviously banned tropes in title for logging
            if do_not and any(b in blob for b in do_not if len(b) > 5):
                LOG.warning("Concept may violate lyric do_not list: %s", title)
            if motifs and not concept.get("visual_motifs"):
                concept["visual_motifs"] = motifs
            concept["visual_bible"] = self.ensure_visual_bible(concept)
            enriched.append(concept)
        return enriched or concepts

    def ensure_visual_bible(
        self,
        concept: Dict[str, Any],
        *,
        project_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Normalize / fill a mode-aware visual bible on a concept."""
        mode = (project_mode or self.project_mode or "mtv").strip().lower()
        existing = concept.get("visual_bible")
        bible: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}

        palette = [str(c) for c in (concept.get("palette") or []) if c][:5]
        motifs = [str(m) for m in (concept.get("visual_motifs") or concept.get("lyric_hooks") or []) if m][:6]
        style = str(concept.get("style") or "cinematic")
        narrative = str(concept.get("narrative") or concept.get("title") or "lyric-grounded music video")

        bible.setdefault("logline", narrative[:220])
        bible.setdefault("palette", palette or ["#0b1629", "#06b6d4", "#f59e0b"])
        bible.setdefault("forbidden_colors", ["#00ff00", "#ff00ff"])
        bible.setdefault("recurring_motifs", motifs or ["lead motif from lyrics"])
        bible.setdefault(
            "prohibited",
            ["unrelated neon cyberpunk", "generic sci-fi city", "random text glyphs"],
        )

        if mode == "typography":
            bible.setdefault("type_personality", "bold modern display with restrained kinetic energy")
            bible.setdefault("case_policy", "as-written")
            bible.setdefault("maximum_lyric_lines", 2)
            bible.setdefault("safe_margin_percent", 8)
            bible.setdefault("materials", ["graphic paper", "foil", "projected light", "ink"])
            bible.setdefault("word_choreography", "phrase-reveal with stable final hold")
            bible.setdefault("motion_grammar", "type and objects transform; backgrounds stay readable")
            bible.setdefault("camera_grammar", "locked graphic frames with occasional push-ins")
            bible.setdefault("text_safe_fallback", "deterministic ASS lyric layer")
            bible["mode"] = "typography"
        else:
            framing = (self.singer_profile or {}).get("framing") or "close-up on face and head"
            bible.setdefault("performer_framing", framing)
            bible.setdefault("motion_grammar", f"{style}; performance energy follows the vocal")
            bible.setdefault(
                "camera_grammar",
                f"{framing}; mix intimate singing with wider cinematic B-roll",
            )
            bible.setdefault("locations", [str((concept.get("lyric_brief") or {}).get("setting") or "lyric world")])
            bible.setdefault(
                "shot_mix_rules",
                "avatar on lyric peaks; cinematic on intros, outros, and instrumental gaps",
            )
            bible["mode"] = "mtv"

        concept["visual_bible"] = bible
        return bible

    async def expand_visual_bible(
        self,
        concept: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Ensure the selected concept has a complete visual bible (LLM polish when available)."""
        bible = self.ensure_visual_bible(concept)
        if not self.llm:
            return bible

        mode = self.project_mode or "mtv"
        try:
            raw = await self.llm.generate(
                prompt=(
                    f"PROJECT MODE: {mode}\n"
                    f"CONCEPT: {json.dumps({k: concept.get(k) for k in ('title','style','palette','narrative','lyric_hooks','key_moments')}, ensure_ascii=False)}\n"
                    f"DRAFT BIBLE: {json.dumps(bible, ensure_ascii=False)}\n\n"
                    "Expand into a locked visual bible. Respond with ONLY JSON for visual_bible."
                ),
                system_prompt=(
                    "You lock art direction for a music video before bulk generation. "
                    "Keep lyric fidelity. For typography mode emphasize type personality, "
                    "safe margins, and text-safe fallback. For MTV mode emphasize performer "
                    "framing, camera grammar, and shot mix. No markdown."
                ),
                max_tokens=900,
                temperature=0.35,
            )
            text = (raw or "").strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(text[start:end])
                if isinstance(parsed, dict):
                    if "visual_bible" in parsed and isinstance(parsed["visual_bible"], dict):
                        parsed = parsed["visual_bible"]
                    bible.update(parsed)
        except Exception as exc:
            LOG.warning("Visual bible expansion failed: %s", exc)

        concept["visual_bible"] = self.ensure_visual_bible(concept)
        return concept["visual_bible"]

    def _visual_bible_prompt_block(self, concept: Dict[str, Any]) -> str:
        bible = concept.get("visual_bible") or self.ensure_visual_bible(concept)
        return (
            "VISUAL BIBLE (lock style — do not invent a new look):\n"
            + json.dumps(bible, ensure_ascii=False)
        )

    def _parse_concepts(
        self,
        response: str,
        lyric_brief: Optional[Dict[str, Any]] = None,
        track_title: str = "",
    ) -> List[Dict[str, Any]]:
        """Extract JSON concepts from LLM response."""
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
                concepts = data.get("concepts", [])
                if concepts:
                    return concepts
        except json.JSONDecodeError:
            pass

        LOG.warning("Failed to parse concepts from LLM response — using lyric-grounded fallback")
        themes = (lyric_brief or {}).get("themes") or [track_title or "the song"]
        setting = (lyric_brief or {}).get("setting") or "intimate performance space"
        motifs = (lyric_brief or {}).get("visual_motifs") or themes
        hooks = (lyric_brief or {}).get("key_phrases") or themes[:3]
        theme_str = ", ".join(str(t) for t in themes[:4])
        concept = {
            "title": f"{track_title or 'Lyric Vision'}: {themes[0]}",
            "style": f"Cinematic performance film focused on {theme_str}",
            "palette": ["#0b1629", "#f59e0b", "#06b6d4"],
            "narrative": (
                f"A faithful visualization of the song's themes ({theme_str}) "
                f"set in {setting}, following the lyric story beat by beat."
            ),
            "lyric_hooks": hooks[:4],
            "key_moments": {
                "intro": f"Establish {motifs[0] if motifs else 'the lyric world'}",
                "verse": f"Develop lyric images: {', '.join(str(m) for m in motifs[:3])}",
                "chorus": f"Peak emotion around: {hooks[0] if hooks else theme_str}",
                "outro": "Resolve the lyric story with the same singer",
            },
            "complexity": "medium",
        }
        self.ensure_visual_bible(concept)
        return [concept]

    # ── Phase 3: Reference Images ────────────────────────────────────

    async def generate_reference_images(
        self,
        concept: Dict[str, Any],
        image_prompt: Optional[str] = None,
    ) -> List[str]:
        """Generate style reference images — never overwrite user identity photos."""
        self._emit_progress(self.PHASE_REFERENCES, 10, "Preparing reference images...")

        existing = [p for p in (self.reference_images or []) if p and Path(p).is_file()]
        if existing:
            self.reference_images = existing
            self._emit_progress(
                self.PHASE_REFERENCES,
                100,
                f"Using {len(existing)} uploaded identity reference(s)",
            )
            LOG.info("Preserving user reference images: %s", existing)
            return self.reference_images

        palette_str = ", ".join(concept["palette"])
        ref_prompt = (
            image_prompt or (
                f"{concept['style']}, {concept['narrative']}, "
                f"color palette: {palette_str}, cinematic lighting, "
                f"detailed, high quality, music video still"
            )
        )

        seeds = list(range(1000, 1003))
        generated: List[str] = []

        for i, seed in enumerate(seeds):
            progress = int(((i + 1) / len(seeds)) * 90) + 10
            self._emit_progress(
                self.PHASE_REFERENCES,
                progress,
                f"Generating reference image {i + 1}/{len(seeds)}",
            )

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
                session_dir = self.output_dir / "references"
                session_dir.mkdir(parents=True, exist_ok=True)
                dest = session_dir / f"ref_{i}.png"

                if not Path(img_path).is_absolute():
                    comfyui_outputs = Path(__file__).resolve().parents[2] / "outputs"
                    source = comfyui_outputs / img_path
                else:
                    source = Path(img_path)

                if source.exists():
                    shutil.copy(source, dest)
                    generated.append(str(dest))
                    LOG.info("Reference image saved: %s", dest)

        self.reference_images = generated
        self._emit_progress(self.PHASE_REFERENCES, 100, "Reference images generated")
        return self.reference_images

    def _singer_brief(self) -> str:
        """Compact singer identity string from the user-specified profile."""
        p = self.singer_profile or {}
        parts: List[str] = []
        if p.get("name"):
            parts.append(f"named {p['name']}")
        if p.get("presentation"):
            parts.append(str(p["presentation"]))
        if p.get("age_range"):
            parts.append(str(p["age_range"]))
        if p.get("look"):
            parts.append(str(p["look"]))
        framing = p.get("framing") or "close-up on face and head"
        parts.append(f"camera framing: {framing}")
        return ", ".join(parts) if parts else ""

    async def _ensure_character_lock(self, concept: Dict[str, Any]) -> str:
        """Stable character/style bible reused across every frame for identity consistency."""
        if self.character_lock:
            return self.character_lock

        ref_note = ""
        if self.reference_images:
            names = ", ".join(Path(r).name for r in self.reference_images[:4])
            ref_note = (
                f" Match the uploaded reference photo(s) exactly for face identity ({names})."
            )

        singer = self._singer_brief()
        style = concept.get("style") or "cinematic"
        title = concept.get("title") or "the lead performer"
        narrative = (concept.get("narrative") or "")[:280]

        if self.llm:
            try:
                if self.reference_images:
                    raw = await self.llm.generate(
                        prompt=(
                            f"Write a CHARACTER LOCK (40-70 words) for AI image generation.\n"
                            f"A PHOTO REFERENCE of the singer was uploaded — identity MUST match that photo.\n"
                            f"Do NOT invent a different face, ethnicity, age, or hair.\n"
                            f"Say the lead is 'the exact person from the reference photograph'.\n"
                            f"You MAY add clothing/styling from: {singer or 'performance wardrobe'}.\n"
                            f"Concept mood only (not a new person): {title} / {style}\n"
                            f"Plain text only."
                        ),
                        system_prompt=(
                            "You write identity locks that defer to a reference photograph. "
                            "Never invent facial features that conflict with a photo. No markdown."
                        ),
                        max_tokens=220,
                        temperature=0.2,
                    )
                else:
                    raw = await self.llm.generate(
                        prompt=(
                            f"Write a CHARACTER LOCK paragraph (45-80 words) for a music video.\n"
                            f"Concept: {title}\nStyle: {style}\nNarrative: {narrative}\n"
                            f"SINGER PROFILE: {singer or 'lead vocalist'}\n"
                            f"Describe ONE recurring lead singer that must look identical in every shot. "
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
            if self.reference_images:
                lock = (
                    "Lead singer is the exact person from the uploaded reference photograph — "
                    "same face, hair, skin tone, and age"
                    + (f"; wardrobe notes: {singer}" if singer else "")
                    + f"; style {style}; never change identity between shots."
                )
            else:
                lock = (
                    f"Same lead singer throughout"
                    f"{(' — ' + singer) if singer else ''}: consistent face, hair, outfit; "
                    f"style {style}; never change identity between shots."
                )
        elif singer and singer.lower() not in lock.lower():
            lock = f"{lock} Singer: {singer}."
        if ref_note:
            lock = f"{lock}{ref_note}"

        self.character_lock = lock
        LOG.info("Character lock: %s", lock[:160])
        return lock

    def _identity_ref_paths(self) -> List[str]:
        """Refs for Omni: identity sheet first, then user uploads."""
        paths: List[str] = []
        if self.identity_sheet_path and Path(self.identity_sheet_path).is_file():
            paths.append(self.identity_sheet_path)
        for p in self.reference_images or []:
            if p and Path(p).is_file() and p not in paths:
                paths.append(p)
        return paths[:3]

    async def ensure_identity_sheet(
        self,
        concept: Optional[Dict[str, Any]] = None,
        *,
        allow_text_only: bool = False,
    ) -> Optional[str]:
        """Build one locked performer still (from photo, or text lock when allowed)."""
        if self.identity_sheet_path and Path(self.identity_sheet_path).is_file():
            return self.identity_sheet_path

        refs = [p for p in (self.reference_images or []) if p and Path(p).is_file()]
        if not refs and not allow_text_only:
            LOG.info("No reference photos — skipping identity sheet")
            return None

        self._emit_progress(
            self.PHASE_REFERENCES,
            20,
            "Locking performer identity…"
            if refs
            else "Inventing a locked performer look from direction…",
        )
        concept = concept or self.selected_concept or {}
        singer = self._singer_brief()
        framing = (self.singer_profile or {}).get("framing") or "close-up on face and head"
        style = concept.get("style") or concept.get("direction") or "cinematic music video"
        lock = (self.character_lock or "").strip()
        if refs:
            prompt = (
                f"Music video identity sheet of the lead singer, {framing}, "
                f"clear face looking toward camera, singing pose, soft studio backdrop, "
                f"high detail face, same person as the reference photograph, "
                f"{style}"
                + (f", {singer}" if singer else "")
                + ". Clean background, no crowd, one person only, photorealistic."
            )
        else:
            prompt = (
                f"Music video identity sheet, {framing}, clear face looking toward camera, "
                f"singing pose, soft studio backdrop, high detail face, one person only, "
                f"photorealistic. {lock or singer or 'distinctive lead performer'}. "
                f"Style: {style}. Clean background, no crowd, no text."
            )

        sheet_dir = self.output_dir / "identity"
        sheet_dir.mkdir(parents=True, exist_ok=True)
        dest = sheet_dir / "performer_sheet.png"
        if self._video_seed_base is None:
            self._video_seed_base = int(time.time()) % (2**31)

        img_path = None
        try:
            if self.use_home_agent_stills:
                self._emit_progress(
                    self.PHASE_REFERENCES,
                    40,
                    "Identity sheet via Home Agent…",
                )
                img_path = await self._generate_image_via_home_agent(
                    prompt,
                    job_type="identity_sheet",
                    dest_dir=sheet_dir,
                    reference_image_paths=refs[:2] or None,
                    extras={"framing": framing},
                    width=768,
                    height=1024,
                )
            elif self.pipeline_stills == "fal":
                from server.fal_client import FalClient

                self._emit_progress(
                    self.PHASE_REFERENCES,
                    40,
                    "Identity sheet via FAL draft…",
                )
                fal_dest = sheet_dir / "fal_identity.png"
                img_path = await FalClient().generate_image(
                    prompt,
                    dest=fal_dest,
                    width=768,
                    height=1024,
                )
            else:
                img_path = await self.comfyui.generate_image_turbo(
                    prompt=prompt,
                    width=768,
                    height=1024,
                    seed=self._video_seed_base,
                    reference_image_paths=refs[:2] or None,
                )
        except Exception as e:
            LOG.warning("Identity sheet generation failed: %s", e)
            img_path = None

        if img_path and Path(img_path).is_file():
            if Path(img_path).resolve() != dest.resolve():
                shutil.copy(img_path, dest)
            else:
                dest = Path(img_path)
            self.identity_sheet_path = str(dest)
            # Portrait crop keeps face large for H3 / InfiniteTalk lip-sync
            try:
                portrait = self.comfyui._prepare_performance_portrait(dest)
                if portrait and Path(portrait).is_file():
                    portrait_dest = sheet_dir / "performer_portrait.jpg"
                    shutil.copy(portrait, portrait_dest)
                    self.identity_sheet_path = str(portrait_dest)
            except Exception as e:
                LOG.warning("Performance portrait prep failed: %s", e)
            LOG.info("Identity sheet locked: %s", self.identity_sheet_path)
            self._emit_progress(self.PHASE_REFERENCES, 100, "Performer identity locked")
            return self.identity_sheet_path

        # Fallback: copy first user ref into identity/ so /asset/identity/ URLs work
        if refs:
            suffix = Path(refs[0]).suffix.lower() or ".png"
            if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
                suffix = ".png"
            fallback_dest = sheet_dir / f"performer_sheet{suffix}"
            shutil.copy(refs[0], fallback_dest)
            self.identity_sheet_path = str(fallback_dest)
            LOG.info(
                "Using copied reference as identity sheet: %s → %s",
                refs[0],
                fallback_dest,
            )
            return self.identity_sheet_path
        return None

    def _infer_shot_type(
        self,
        segment_name: str,
        lyric_window: str,
        llm_type: Optional[str] = None,
    ) -> str:
        """Decide avatar vs cinematic for a scene."""
        mix = (self.performance_mix or "hybrid").strip().lower()
        if mix in ("performance", "performance-first", "performance_first", "avatar"):
            return "avatar"

        if llm_type in ("avatar", "cinematic"):
            return llm_type

        name = (segment_name or "").lower()
        has_lyrics = bool((lyric_window or "").strip()) and len(lyric_window.strip()) > 8
        if any(k in name for k in ("intro", "outro", "instrumental", "break", "solo")):
            return "cinematic" if not has_lyrics else "avatar"
        if any(k in name for k in ("chorus", "verse", "hook", "bridge", "prechorus", "pre-chorus")):
            return "avatar"
        return "avatar" if has_lyrics else "cinematic"

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
        typography_mode = self.project_mode == "typography"
        character_lock = "" if typography_mode else await self._ensure_character_lock(concept)
        self.ensure_visual_bible(concept)
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
            from server.lyrics import lyrics_in_window

            cues = analysis.get("lyrics_cues") or []
            for s in batch:
                key = concept.get("key_moments") or {}
                moment = key.get(s["name"], f"{s['name']} energy")
                lyric_slice = lyrics_in_window(
                    cues, float(s["start"]), float(s["end"])
                )
                lyric_note = f' lyrics="{lyric_slice}"' if lyric_slice else ""
                scene_lines.append(
                    f"- index={s.get('index', batch_start)} "
                    f"name={s['name']} "
                    f"time={s['start']:.1f}-{s['end']:.1f}s "
                    f"moment={moment}{lyric_note}"
                )

            if typography_mode:
                system_prompt = (
                    "You are an expert kinetic-typography lyric-video director. "
                    "For EACH scene, write one vivid background/reference image prompt "
                    "(70-130 words) with composition, materials, palette, depth, and a "
                    "clear quiet zone for exact text. Do NOT ask the image model to draw "
                    "letters or words; canonical lyrics are composited later. Make lyric "
                    "imagery physically express the words through objects and environment. "
                    "Instrumental gaps need a directed visual arc, not filler. Assign "
                    'shot_type as "lyric", "instrumental", or "transition". Respond ONLY '
                    'with JSON: [{"index":0,"prompt":"...","shot_type":"lyric"}, ...].'
                )
            else:
                system_prompt = (
                    "You are an expert storyboard artist for full-length music videos. "
                    "For EACH scene, write one vivid image-generation prompt (80-150 words) "
                    "with composition, lighting, palette, and camera feel. "
                    "If lyrics are provided for a scene, the visual MUST illustrate those lyric "
                    "ideas (literal or metaphorical). "
                    "EVERY prompt must include the same CHARACTER LOCK details verbatim so the "
                    "performer's face, hair, and outfit stay identical across shots. "
                    "Also assign shot_type: "
                    '"avatar" for singing / lip-sync close-ups on the performer, '
                    '"cinematic" for B-roll, atmosphere, or wide shots without mouth sync. '
                    "Respond with ONLY a JSON array of objects: "
                    '[{"index":0,"prompt":"...","shot_type":"avatar|cinematic"}, ...] '
                    "matching the given indexes."
                )
            full_lyrics = (analysis.get("lyrics") or "")[:2000]
            mix_note = (
                "Prefer mostly avatar singing shots."
                if (self.performance_mix or "").startswith("performance")
                else "Mix avatar singing shots on lyric peaks with cinematic B-roll elsewhere."
            )
            identity_line = (
                f"CHARACTER LOCK (include in every prompt): {character_lock}\n"
                if character_lock
                else "TEXT POLICY: leave a quiet zone; do not generate visible words.\n"
            )
            user_message = (
                f"Song duration: {song_dur:.1f}s | {analysis.get('tempo', 0):.0f} BPM\n"
                f"TRACK: {analysis.get('track_title') or ''}\n"
                f"CONCEPT: {concept.get('title')}\n"
                f"STYLE: {concept.get('style')}\n"
                f"PALETTE: {palette_str}\n"
                f"PROJECT MODE: {self.project_mode}\n"
                f"PERFORMANCE MIX: {mix_note if not typography_mode else 'kinetic typography'}\n"
                f"{identity_line}"
                f"{self._visual_bible_prompt_block(concept)}\n"
                f"SONG LYRICS (theme reference):\n{full_lyrics}\n"
                f"SCENES:\n{chr(10).join(scene_lines)}"
            )

            raw = await self.llm.generate(
                prompt=user_message,
                system_prompt=system_prompt,
                max_tokens=4096,
                temperature=0.7,
            )
            prompts_by_idx: Dict[int, str] = {}
            shot_types_by_idx: Dict[int, str] = {}
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
                            idx_key = int(item.get("index", len(prompts_by_idx)))
                            prompts_by_idx[idx_key] = str(item["prompt"]).strip()
                            st = str(item.get("shot_type") or "").strip().lower()
                            if st in (
                                "avatar",
                                "cinematic",
                                "lyric",
                                "instrumental",
                                "transition",
                            ):
                                shot_types_by_idx[idx_key] = st
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

                lyric_slice = lyrics_in_window(
                    cues, float(s["start"]), float(s["end"])
                )
                if typography_mode:
                    llm_shot = shot_types_by_idx.get(idx)
                    shot_type = (
                        llm_shot
                        if llm_shot in ("lyric", "instrumental", "transition")
                        else ("lyric" if lyric_slice else "instrumental")
                    )
                else:
                    shot_type = self._infer_shot_type(
                        s["name"], lyric_slice, shot_types_by_idx.get(idx)
                    )

                storyboard.append(
                    {
                        "id": f"frame_{len(storyboard):02d}",
                        "segment": s["name"],
                        "timestamp": float(s["start"]),
                        "duration": float(s.get("duration") or (s["end"] - s["start"])),
                        "end": float(s["end"]),
                        "energy": float(s.get("energy") or 0.5),
                        "prompt": prompt_text,
                        "shot_type": shot_type,
                        "project_mode": self.project_mode,
                        "text_canonical": lyric_slice if typography_mode else "",
                        "text_display": lyric_slice.replace(" / ", "\n")
                        if typography_mode and lyric_slice
                        else "",
                        "typography_mode": "hybrid" if typography_mode else "none",
                        "reveal_grammar": "phrase-reveal" if typography_mode else "",
                        "lyric_window": lyric_slice[:240] if lyric_slice else "",
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

    async def render_storyboard_frame(
        self,
        concept: Dict[str, Any],
        frame_index: int,
    ) -> Optional[Dict[str, Any]]:
        """Render a single storyboard still by index."""
        if frame_index < 0 or frame_index >= len(self.storyboard):
            return None

        if self.project_mode != "typography":
            await self.ensure_identity_sheet(concept)
        session_dir = self.output_dir / "storyboard"
        session_dir.mkdir(parents=True, exist_ok=True)

        frame = self.storyboard[frame_index]
        palette_str = ", ".join(concept["palette"])
        session_id = self.output_dir.name
        identity_refs = (
            self._identity_ref_paths() if self.project_mode != "typography" else []
        )
        total = len(self.storyboard)

        character_lock = (
            await self._ensure_character_lock(concept)
            if self.project_mode != "typography"
            else ""
        )
        framing = (self.singer_profile or {}).get("framing") or "close-up on face and head"
        hooks = concept.get("lyric_hooks") or []
        hooks_str = "; ".join(str(h) for h in hooks[:4])
        shot_type = frame.get("shot_type") or "cinematic"
        segment = frame.get("segment") or frame.get("id", f"scene {frame_index + 1}")

        self._emit_progress(
            self.PHASE_STORYBOARD,
            int(((frame_index + 0.5) / max(total, 1)) * 90) + 5,
            f"Rendering frame {frame_index + 1}/{total} — {segment}",
        )

        if self.project_mode == "typography":
            enhanced_prompt = (
                f"{frame['prompt']}, {concept['style']}, color palette: {palette_str}, "
                "cinematic graphic design, strong compositional hierarchy, generous central "
                "or lower-third negative space for later typography, absolutely no text, "
                "no letters, no logos, no watermark"
            )
        else:
            enhanced_prompt = (
                f"{frame['prompt']}, {concept['style']}, "
                f"color palette: {palette_str}, cinematic lighting, "
                f"{framing}, identical character consistency, {character_lock}"
            )
        if shot_type == "avatar":
            enhanced_prompt = (
                f"{enhanced_prompt}. Close-up singing performance still, "
                f"clear face, mouth slightly open as if singing"
            )
        if hooks_str:
            enhanced_prompt = f"{enhanced_prompt}. Lyric imagery: {hooks_str}"

        if self._video_seed_base is None:
            self._video_seed_base = int(time.time()) % (2**31)
        seed = (self._video_seed_base or 0) + frame_index * 17

        img_path = None
        try:
            img_path = await self.comfyui.generate_image_turbo(
                prompt=enhanced_prompt,
                width=1024,
                height=576,
                seed=seed,
                reference_image_paths=identity_refs or None,
            )
        except Exception as e:
            LOG.warning("z-image turbo failed for frame %d: %s", frame_index, e)

        if not img_path:
            try:
                img_path = await self.comfyui.generate_image_flux(
                    prompt=enhanced_prompt,
                    width=1024,
                    height=576,
                    seed=seed,
                )
            except Exception as e:
                LOG.warning("FLUX fallback failed for frame %d: %s", frame_index, e)

        if img_path:
            dest = session_dir / f"frame_{frame_index:02d}.png"
            source = Path(img_path)
            if source.exists():
                shutil.copy(source, dest)
                frame["image_path"] = str(dest)
                frame["image_url"] = (
                    f"/api/session/{session_id}/asset/storyboard/{dest.name}"
                )
                frame["render_status"] = "done"
                LOG.info("Storyboard image saved: %s", dest)
            else:
                frame["render_status"] = "failed"
                LOG.warning("Comfy output missing on disk for frame %d: %s", frame_index, source)
        else:
            frame["render_status"] = "failed"
            LOG.warning("No image generated for frame %s", frame.get("id", frame_index))

        self._emit_frame_ready(frame_index, frame, total)
        return frame

    async def generate_storyboard_images(
        self,
        concept: Dict[str, Any],
        *,
        start_index: int = 0,
        end_index: Optional[int] = None,
        frame_indices: Optional[List[int]] = None,
    ) -> List[str]:
        """Generate images for storyboard frames (optionally a slice)."""
        self.clear_storyboard_cancel()
        self._emit_progress(self.PHASE_STORYBOARD, 5, "Locking performer identity…")
        if self.project_mode != "typography":
            await self.ensure_identity_sheet(concept)

        total = len(self.storyboard)
        end = total if end_index is None else min(end_index, total)
        start = max(0, start_index)
        generated: List[Dict[str, Any]] = []

        indices = (
            [i for i in frame_indices if 0 <= i < total]
            if frame_indices is not None
            else list(range(start, end))
        )
        for i in indices:
            if self._storyboard_cancel:
                self._emit_progress(
                    self.PHASE_STORYBOARD,
                    int((i / max(total, 1)) * 90),
                    "Storyboard render cancelled",
                )
                break
            frame = await self.render_storyboard_frame(concept, i)
            if frame and frame.get("image_url"):
                generated.append(
                    {
                        "frame_id": frame["id"],
                        "path": frame.get("image_path"),
                        "url": frame.get("image_url"),
                    }
                )

        if not self._storyboard_cancel:
            self._emit_progress(self.PHASE_STORYBOARD, 100, "Storyboard images generated")
        return generated

    # ── Phase 6: Video Clips ─────────────────────────────────────────

    def _avatar_start_image(self, frame: Dict[str, Any]) -> str:
        """Prefer identity sheet for Avatar lip-sync; fall back to storyboard still."""
        if self.identity_sheet_path and Path(self.identity_sheet_path).is_file():
            LOG.info(
                "Avatar start_image=identity sheet (%s)",
                Path(self.identity_sheet_path).name,
            )
            return self.identity_sheet_path
        refs = [p for p in (self.reference_images or []) if p and Path(p).is_file()]
        if refs:
            LOG.info("Avatar start_image=user ref (%s)", Path(refs[0]).name)
            return refs[0]
        return str(frame.get("image_path") or "")

    @staticmethod
    def _concat_video_chunks(chunk_paths: List[Path], dest: Path) -> Optional[Path]:
        """Concatenate MP4 chunks with ffmpeg (re-encode for compatibility)."""
        import subprocess

        if not chunk_paths:
            return None
        if len(chunk_paths) == 1:
            shutil.copy(chunk_paths[0], dest)
            return dest if dest.is_file() else None

        work = dest.parent / f"_concat_{dest.stem}"
        work.mkdir(parents=True, exist_ok=True)
        list_file = work / "list.txt"
        list_file.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in chunk_paths) + "\n"
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-an",
            str(dest),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not dest.is_file():
            LOG.warning("Chunk concat failed: %s", (result.stderr or "")[-400:])
            shutil.copy(chunk_paths[0], dest)
        return dest if dest.is_file() else None

    @staticmethod
    def _extract_last_frame(video_path: Path, dest: Path) -> Optional[Path]:
        """Grab the last frame of a clip for InfiniteTalk continuity."""
        import subprocess

        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-sseof", "-0.15",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(dest),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and dest.is_file():
            return dest
        return None

    async def _generate_avatar_scene(
        self,
        *,
        frame: Dict[str, Any],
        frame_index: int,
        motion_prompt: str,
        audio_src: str | Path,
        session_dir: Path,
        seed: int,
        backend: Optional[str] = None,
    ) -> Optional[str]:
        """Generate audio-aligned avatar takes covering the full scene duration.

        Backends: MiniMax H3 (longer takes), InfiniteTalk (chunked), or HuMo.
        """
        scene_dur = float(frame.get("duration") or 6.0)
        start_t = float(frame.get("timestamp") or 0.0)
        avatar_backend = (
            backend
            or self.video_backend
            or os.getenv("COMFY_VIDEO_BACKEND")
            or "minimax-h3"
        ).strip().lower()
        if avatar_backend in ("minimax-h3", "minimax_h3", "h3", "minimax"):
            avatar_backend = "minimax-h3"
            chunk_secs = float(os.getenv("COMFY_H3_CLIP_SECONDS", "5.0"))
            chunk_secs = min(max(3.0, chunk_secs), 12.0)
            fps = float(os.getenv("COMFY_H3_FPS", "24"))
            max_chunks = int(os.getenv("COMFY_H3_MAX_CHUNKS", "3"))
            sample_rate, channels, normalize = "44100", "2", False
            width, height = 768, 448
            label = "MiniMax H3"
        elif avatar_backend in ("humo", "wan", "wan-humo"):
            avatar_backend = "humo"
            chunk_secs = float(os.getenv("COMFY_HUMO_CLIP_SECONDS", "2.0"))
            chunk_secs = min(max(1.5, chunk_secs), 4.0)
            fps = float(os.getenv("COMFY_HUMO_FPS", "25"))
            max_chunks = int(os.getenv("COMFY_HUMO_MAX_CHUNKS", "4"))
            sample_rate, channels, normalize = "16000", "1", True
            width, height = 512, 512
            label = "HuMo"
        else:
            avatar_backend = "infinitetalk"
            chunk_secs = float(os.getenv("COMFY_INFINITETALK_CLIP_SECONDS", "3.5"))
            chunk_secs = min(max(2.0, chunk_secs), 5.0)
            fps = float(os.getenv("COMFY_INFINITETALK_FPS", "25"))
            max_chunks = int(os.getenv("COMFY_INFINITETALK_MAX_CHUNKS", "4"))
            sample_rate, channels, normalize = "16000", "1", True
            width, height = 480, 832
            label = "InfiniteTalk"

        framing = (self.singer_profile or {}).get("framing") or "close-up on face and head"
        if avatar_backend != "humo" and (
            "full body" in framing.lower() or "waist" in framing.lower()
        ):
            width, height = height, width  # landscape for wider framing
            if avatar_backend == "minimax-h3":
                width, height = 768, 448

        start_image = self._avatar_start_image(frame)
        if not start_image or not Path(start_image).is_file():
            return None

        n_chunks = max(1, int((scene_dur + chunk_secs - 0.05) // chunk_secs))
        n_chunks = min(n_chunks, max_chunks)

        chunk_paths: List[Path] = []
        current_start = start_image
        remaining = scene_dur

        for c in range(n_chunks):
            gen_secs = min(chunk_secs, remaining) if c < n_chunks - 1 else remaining
            gen_secs = max(1.5, gen_secs)
            chunk_start = start_t + c * chunk_secs
            if c == n_chunks - 1:
                gen_secs = max(1.5, scene_dur - c * chunk_secs)
            remaining = max(0.0, scene_dur - (c + 1) * chunk_secs)
            # H3 soft-cap ~15s per take
            if avatar_backend == "minimax-h3":
                gen_secs = min(gen_secs, 14.5)

            self._emit_progress(
                self.PHASE_VIDEO,
                min(95, 15 + frame_index * 3 + c),
                f"Animating {label} take {frame_index + 1} chunk {c + 1}/{n_chunks}…",
            )

            audio_slice = session_dir / f"audio_{frame_index:02d}_{c:02d}.wav"
            try:
                self._slice_audio(
                    audio_src,
                    audio_slice,
                    chunk_start,
                    gen_secs,
                    sample_rate=sample_rate,
                    channels=channels,
                    normalize=normalize,
                )
            except Exception as e:
                LOG.warning("Avatar audio slice failed: %s", e)
                audio_slice = Path(audio_src)

            raw_frames = max(5, int(round(gen_secs * fps)))
            if avatar_backend == "minimax-h3":
                # length ≡ 5 (mod 17)
                raw_frames = raw_frames + (5 - (raw_frames % 17)) % 17
            elif (raw_frames - 1) % 4 != 0:
                raw_frames = ((raw_frames - 1) // 4) * 4 + 1

            singing_prompt = (
                f"{motion_prompt}. The performer sings along to the audio with accurate "
                f"lip sync, expressive face, natural head motion, consistent identity."
            )
            try:
                video_path = await self.comfyui.generate_video_i2v(
                    image_path=current_start,
                    prompt=singing_prompt,
                    num_frames=raw_frames,
                    seed=seed + c * 13,
                    audio_path=str(audio_slice),
                    duration=gen_secs,
                    width=width,
                    height=height,
                    backend=avatar_backend,
                    previous_frames_path=(
                        current_start if c > 0 and current_start != start_image else None
                    ),
                )
            except Exception as e:
                LOG.warning("%s chunk %d failed: %s", label, c, e)
                video_path = None

            if not video_path or not Path(video_path).is_file():
                continue

            chunk_dest = session_dir / f"chunk_{frame_index:02d}_{c:02d}.mp4"
            shutil.copy(video_path, chunk_dest)
            chunk_paths.append(chunk_dest)

            # Continuity: next chunk starts from last frame of this take
            last_frame = session_dir / f"cont_{frame_index:02d}_{c:02d}.jpg"
            extracted = self._extract_last_frame(chunk_dest, last_frame)
            if extracted:
                current_start = str(extracted)

            if remaining <= 0.4:
                break

        if not chunk_paths:
            return None
        dest = session_dir / f"clip_{frame_index:02d}.mp4"
        return str(self._concat_video_chunks(chunk_paths, dest) or "")

    async def generate_video_clips(
        self,
        concept: Dict[str, Any],
    ) -> List[str]:
        """Generate video clips — Avatar (H3/InfiniteTalk/HuMo) vs cinematic (LTX) by shot_type."""
        await self.ensure_identity_sheet(concept)
        user_backend = (
            self.video_backend or os.getenv("COMFY_VIDEO_BACKEND") or "minimax-h3"
        ).strip().lower()
        mix = (self.performance_mix or "hybrid").strip().lower()
        performance_first = mix.startswith("performance")

        self._emit_progress(self.PHASE_VIDEO, 10, "Generating performance + cinematic clips…")

        session_dir = self.output_dir / "videos"
        session_dir.mkdir(parents=True, exist_ok=True)

        generated = []
        session_id = self.output_dir.name
        character_lock = (
            await self._ensure_character_lock(concept)
            if self.project_mode != "typography"
            else ""
        )
        framing = (self.singer_profile or {}).get("framing") or "close-up on face and head"
        if self._video_seed_base is None:
            self._video_seed_base = int(time.time()) % (2**31)

        for i, frame in enumerate(self.storyboard):
            if not frame.get("image_path"):
                LOG.warning("No image for frame %s, skipping video", frame["id"])
                continue

            shot_type = (frame.get("shot_type") or "cinematic").strip().lower()
            if performance_first and self.project_mode != "typography":
                shot_type = "avatar"
            # Avatar path for singing unless user forced pure LTX cinematic
            use_avatar = (
                self.project_mode != "typography"
                and shot_type == "avatar"
                and user_backend != "ltx23"
            )
            if use_avatar:
                if user_backend in ("minimax-h3", "minimax_h3", "h3", "minimax"):
                    label = "MiniMax H3"
                elif user_backend in ("humo", "wan", "wan-humo"):
                    label = "HuMo"
                else:
                    label = "InfiniteTalk"
            elif user_backend in ("humo", "wan", "wan-humo"):
                label = "HuMo"
            else:
                label = "LTX-2.3"

            progress = int(((i + 1) / len(self.storyboard)) * 90) + 10
            self._emit_progress(
                self.PHASE_VIDEO,
                progress,
                f"Animating clip {i + 1}/{len(self.storyboard)} ({label} · {shot_type})",
            )

            if self.llm:
                motion_prompt = await self.llm.generate(
                    prompt=(
                        f"Rewrite this storyboard beat as a short image+audio→video prompt "
                        f"(35-70 words). Keep the SAME singer identity. "
                        f"{'Emphasize singing, lip sync to audio, face close-up.' if use_avatar else 'Emphasize cinematic camera motion and atmosphere.'}\n"
                        f"Storyboard: {frame['prompt']}\n"
                        f"CHARACTER LOCK: {character_lock}\n"
                        f"Style: {concept['style']}"
                    ),
                    max_tokens=512,
                    temperature=0.55,
                )
            else:
                motion_prompt = frame["prompt"]
            if self.project_mode == "typography":
                motion_prompt = (
                    f"{motion_prompt.strip()}. Preserve the clean typography quiet zone. "
                    "No generated text, letters, logos, or watermark."
                )
            else:
                motion_prompt = (
                    f"{motion_prompt.strip()}. {character_lock}. Framing: {framing}."
                )

            audio_src = getattr(self, "audio_path", None)
            if not audio_src or not Path(audio_src).is_file():
                LOG.warning(
                    "No audio for A/V i2v on frame %s — skipping video",
                    frame.get("id", i),
                )
                continue

            scene_dur = float(frame.get("duration") or 6.0)
            start_t = float(frame.get("timestamp") or 0.0)
            seed = (self._video_seed_base or 0) + i
            video_path: Optional[str] = None
            fill_mode = "freeze" if use_avatar else "loop"

            if use_avatar:
                avatar_backend = "infinitetalk"
                if user_backend in ("minimax-h3", "minimax_h3", "h3", "minimax"):
                    avatar_backend = "minimax-h3"
                elif user_backend in ("humo", "wan", "wan-humo"):
                    avatar_backend = "humo"
                video_path = await self._generate_avatar_scene(
                    frame=frame,
                    frame_index=i,
                    motion_prompt=motion_prompt,
                    audio_src=audio_src,
                    session_dir=session_dir,
                    seed=seed,
                    backend=avatar_backend,
                )
            else:
                # Single cinematic take (LTX / HuMo) — shorter clip OK with freeze/loop
                if user_backend in ("humo", "wan", "wan-humo"):
                    clip_secs = float(os.getenv("COMFY_HUMO_CLIP_SECONDS", "2.0"))
                    gen_secs = min(max(1.5, clip_secs), min(scene_dur, 4.0))
                    fps = float(os.getenv("COMFY_HUMO_FPS", "25"))
                    raw_frames = max(9, int(round(gen_secs * fps)))
                    if (raw_frames - 1) % 4 != 0:
                        raw_frames = ((raw_frames - 1) // 4) * 4 + 1
                    width, height = 512, 512
                    cine_backend = "humo"
                else:
                    clip_secs = float(os.getenv("COMFY_LTX_CLIP_SECONDS", "4.0"))
                    gen_secs = min(max(2.0, clip_secs), min(scene_dur, 4.5))
                    fps = float(os.getenv("COMFY_LTX_FPS", "24"))
                    raw_frames = max(9, int(round(gen_secs * fps)))
                    if (raw_frames - 1) % 8 != 0:
                        raw_frames = ((raw_frames - 1) // 8) * 8 + 1
                    width, height = 768, 512
                    cine_backend = "ltx23"

                audio_slice = session_dir / f"audio_{i:02d}.wav"
                try:
                    self._slice_audio(
                        audio_src,
                        audio_slice,
                        start_t,
                        gen_secs,
                        sample_rate="44100" if cine_backend == "ltx23" else "16000",
                        channels="2" if cine_backend == "ltx23" else "1",
                    )
                except Exception as e:
                    LOG.warning("Audio slice failed for frame %d: %s", i, e)
                    audio_slice = Path(audio_src)

                try:
                    video_path = await self.comfyui.generate_video_i2v(
                        image_path=frame["image_path"],
                        prompt=motion_prompt,
                        num_frames=raw_frames,
                        seed=seed,
                        audio_path=str(audio_slice),
                        duration=gen_secs,
                        width=width,
                        height=height,
                        backend=cine_backend,
                    )
                except Exception as e:
                    LOG.warning("%s failed for clip %d: %s", label, i, e)

            if video_path and Path(video_path).is_file():
                dest = session_dir / f"clip_{i:02d}.mp4"
                source = Path(video_path)
                if source.resolve() != dest.resolve():
                    shutil.copy(source, dest)
                frame["video_path"] = str(dest)
                frame["clip_duration"] = scene_dur
                frame["target_duration"] = scene_dur
                frame["fill_mode"] = fill_mode
                clip_url = f"/api/session/{session_id}/asset/video/{dest.name}"
                frame["video_url"] = clip_url
                generated.append(
                    {
                        "frame_id": frame["id"],
                        "path": str(dest),
                        "url": clip_url,
                        "timestamp": start_t,
                        "duration": scene_dur,
                        "clip_duration": scene_dur,
                        "shot_type": shot_type,
                        "fill_mode": fill_mode,
                    }
                )
                LOG.info(
                    "Video clip saved: %s (%s, scene %.1fs)",
                    dest,
                    shot_type,
                    scene_dur,
                )
            else:
                LOG.warning("No video generated for frame %s", frame.get("id", i))

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
        normalize: bool = False,
    ) -> Path:
        """Extract a short WAV slice with ffmpeg for A/V conditioning."""
        import subprocess

        dest.parent.mkdir(parents=True, exist_ok=True)
        af = []
        if normalize:
            af.append("loudnorm=I=-16:TP=-1.5:LRA=11")
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
        ]
        if af:
            cmd.extend(["-af", ",".join(af)])
        cmd.extend([str(dest)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or not dest.is_file():
            raise RuntimeError(f"ffmpeg slice failed: {(result.stderr or '')[-300:]}")
        return dest

    # ── Studio: H3-only lyric scene pipeline ──────────────────────────

    NO_ONSCREEN_TEXT = "NO text, NO captions, NO letters, NO watermarks, NO subtitles"

    def _studio_character_line(self) -> str:
        if self.character_lock:
            return self.character_lock
        singer = self.singer_profile or {}
        bits = [
            singer.get("name"),
            singer.get("presentation"),
            singer.get("age_range"),
            singer.get("look"),
        ]
        line = ", ".join(str(b).strip() for b in bits if b and str(b).strip())
        framing = singer.get("framing") or "close-up on face and head"
        if line:
            return f"{line}; framing: {framing}"
        return f"lead performer; framing: {framing}"

    def _studio_identity_url(self) -> Optional[str]:
        if not self.identity_sheet_path or not Path(self.identity_sheet_path).is_file():
            return None
        path = Path(self.identity_sheet_path).resolve()
        identity_dir = (self.output_dir / "identity").resolve()
        refs_dir = (self.output_dir / "references").resolve()
        session_id = self.output_dir.name
        try:
            path.relative_to(identity_dir)
            return f"/api/session/{session_id}/asset/identity/{path.name}"
        except ValueError:
            pass
        try:
            path.relative_to(refs_dir)
            return f"/api/session/{session_id}/asset/references/{path.name}"
        except ValueError:
            pass
        # Path outside session folders — do not emit a broken identity URL
        LOG.warning("Identity path not under identity/ or references/: %s", path)
        return None

    def _studio_lyric_windows(
        self, duration: float, target_secs: float
    ) -> List[Dict[str, Any]]:
        """Partition the song into contiguous scene windows from lyric cues."""
        analysis = self.audio_analysis or {}
        cues = list(analysis.get("lyrics_cues") or [])
        windows: List[Dict[str, Any]] = []
        if cues:
            # Group cues into ~target_secs blocks
            i = 0
            while i < len(cues):
                start = float(cues[i].get("start") or 0.0)
                texts: List[str] = []
                end = start
                j = i
                while j < len(cues):
                    c = cues[j]
                    c_start = float(c.get("start") or end)
                    c_end = float(c.get("end") or c_start)
                    # Don't pull a cue across a long instrumental gap
                    if texts and c_start - end > max(4.0, target_secs * 0.6):
                        break
                    texts.append(str(c.get("text") or "").strip())
                    end = max(end, c_end)
                    j += 1
                    if end - start >= target_secs * 0.85 and j < len(cues):
                        nxt = float(cues[j].get("end") or cues[j].get("start") or end)
                        if nxt - start > target_secs * 1.35:
                            break
                end = min(duration, max(end, start + 2.0))
                windows.append(
                    {
                        "start": start,
                        "end": end,
                        "lyric": " ".join(t for t in texts if t).strip(),
                    }
                )
                i = max(j, i + 1)

            # Insert instrumental coverage for gaps, lead-in, and outro
            filled: List[Dict[str, Any]] = []
            cursor = 0.0
            for w in windows:
                ws = float(w["start"])
                we = float(w["end"])
                if ws > cursor + 0.35:
                    filled.append({"start": cursor, "end": ws, "lyric": ""})
                filled.append(w)
                cursor = we
            if cursor < duration - 0.35:
                filled.append({"start": cursor, "end": duration, "lyric": ""})
            windows = filled or [
                {"start": 0.0, "end": duration, "lyric": ""}
            ]
            # Snap edges for true contiguity
            windows[0]["start"] = 0.0
            for k in range(1, len(windows)):
                windows[k]["start"] = windows[k - 1]["end"]
            windows[-1]["end"] = duration
        else:
            t = 0.0
            while t < duration - 0.25:
                end = min(duration, t + target_secs)
                windows.append({"start": t, "end": end, "lyric": ""})
                t = end

        # Merge undersized tails into previous
        merged: List[Dict[str, Any]] = []
        for w in windows:
            dur = float(w["end"]) - float(w["start"])
            if merged and dur < 3.0:
                merged[-1]["end"] = w["end"]
                if w.get("lyric"):
                    merged[-1]["lyric"] = (
                        f"{merged[-1].get('lyric') or ''} {w['lyric']}"
                    ).strip()
            else:
                merged.append(dict(w))
        if merged:
            merged[-1]["end"] = duration

        # Split oversized windows so each H3 take stays under ~12s
        max_take = min(12.0, max(6.0, target_secs * 1.2))
        split: List[Dict[str, Any]] = []
        for w in merged:
            start = float(w["start"])
            end = float(w["end"])
            lyric = str(w.get("lyric") or "")
            while end - start > max_take + 0.5:
                mid = start + max_take
                split.append({"start": start, "end": mid, "lyric": lyric})
                # lyric only on first chunk of a split
                lyric = ""
                start = mid
            split.append({"start": start, "end": end, "lyric": lyric})
        return split

    def _studio_scene_type(self, index: int, total: int, lyric: str) -> str:
        if index == 0 and not lyric:
            return "INTRO_BROLL"
        if index >= total - 1 and not lyric:
            return "OUTRO"
        # Prefer performance close-ups for lyric windows so lip-sync has a face.
        # Occasional THEME beat for variety (every 4th lyric scene).
        if lyric:
            lyric_ord = index  # approximate; fine for cadence
            return "THEME" if lyric_ord % 4 == 3 else "PERF"
        return "THEME"

    def _studio_default_prompt(
        self,
        *,
        direction: str,
        scene_type: str,
        lyric: str,
        character: str,
    ) -> str:
        look = direction.strip() or "cinematic music video, vivid lighting"
        lyric_bit = f' Lyric moment: "{lyric}".' if lyric else ""
        identity = (
            f"Same locked performer throughout: {character}. "
            "Do not change face, hair, skin tone, age, or wardrobe identity."
        )
        if scene_type == "PERF":
            return (
                f"{identity} Medium close-up singing to camera, mouth clearly visible, "
                f"expressive lips matching the vocal audio, natural head motion. "
                f"{look}.{lyric_bit} {self.NO_ONSCREEN_TEXT}."
            )
        if scene_type == "INTRO_BROLL":
            return (
                f"Instrumental intro atmosphere for the song. {look}. "
                f"No performer close-up. {self.NO_ONSCREEN_TEXT}."
            )
        if scene_type == "OUTRO":
            return (
                f"{identity} Final closing pose, face readable. {look}. "
                f"Soft ending energy. {self.NO_ONSCREEN_TEXT}."
            )
        return (
            f"{identity} Thematic world shot featuring the same performer. "
            f"{look}.{lyric_bit} {self.NO_ONSCREEN_TEXT}."
        )

    async def build_studio_timeline_plan(
        self,
        direction: str,
        target_secs: Optional[float] = None,
        *,
        lock_identity: bool = True,
    ) -> Dict[str, Any]:
        """Build a contiguous lyric scene plan for H3 studio renders."""
        analysis = self.audio_analysis or {}
        duration = float(analysis.get("duration") or 0)
        if duration <= 0 and self.audio_path:
            await self.analyze_audio(self.audio_path)
            analysis = self.audio_analysis or {}
            duration = float(analysis.get("duration") or 0)
        if duration <= 0:
            raise ValueError("Song duration unknown — upload audio first")

        concept = {
            "title": "studio lead performer",
            "style": direction[:180] or "cinematic music video",
            "narrative": direction[:400],
            "direction": direction,
        }
        await self._ensure_character_lock(concept)

        if lock_identity:
            await self.ensure_identity_sheet(concept, allow_text_only=True)

        clip_secs = float(
            target_secs
            if target_secs is not None
            else os.getenv("COMFY_H3_CLIP_SECONDS", "10.0")
        )
        clip_secs = min(max(6.0, clip_secs), 12.0)
        windows = self._studio_lyric_windows(duration, clip_secs)
        character = self._studio_character_line()
        scenes: List[Dict[str, Any]] = []

        for i, w in enumerate(windows):
            start = float(w["start"])
            end = float(w["end"])
            lyric = str(w.get("lyric") or "").strip()
            stype = self._studio_scene_type(i, len(windows), lyric)
            prompt = self._studio_default_prompt(
                direction=direction,
                scene_type=stype,
                lyric=lyric,
                character=character,
            )
            # Optional LLM polish for prompts when available
            if self.llm and lyric:
                try:
                    polished = await self.llm.generate(
                        prompt=(
                            "Write one MiniMax H3 music-video shot prompt (40-70 words). "
                            "No on-screen text. Keep the SAME performer identity in every shot.\n"
                            f"Direction: {direction}\n"
                            f"Character lock (must reuse verbatim cues): {character}\n"
                            f"Shot type: {stype}\n"
                            f"Lyric: {lyric}\n"
                            f"Draft: {prompt}"
                        ),
                        max_tokens=220,
                        temperature=0.45,
                    )
                    if polished and len(polished.strip()) > 20:
                        prompt = (
                            f"{polished.strip()} Same performer: {character}. "
                            f"{self.NO_ONSCREEN_TEXT}."
                        )
                except Exception as exc:
                    LOG.warning("Studio prompt polish failed: %s", exc)

            scenes.append(
                {
                    "index": i,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(max(1.5, end - start), 3),
                    "type": stype,
                    "lyric": lyric,
                    "prompt": prompt,
                    "status": "queued",
                    "video_path": None,
                    "video_url": None,
                    "still_url": None,
                    "still_path": None,
                }
            )

        plan = {
            "version": 2,
            "duration": duration,
            "direction": direction,
            "character": character,
            "character_lock": self.character_lock,
            "identity_sheet_url": self._studio_identity_url(),
            "backend": "minimax-h3",
            "review_status": "pending",
            "scenes": scenes,
        }
        plan_path = self.output_dir / "timeline_plan.json"
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        self.timeline_plan = plan
        self._emit_progress(
            self.PHASE_STORYBOARD,
            40,
            f"Storyboard plan ready — {len(scenes)} scenes (review before full render)",
        )
        return plan

    async def generate_studio_storyboard_stills(
        self,
        *,
        max_scenes: Optional[int] = None,
        scene_indices: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Cheap still previews for storyboard review (not full H3 video)."""
        plan = getattr(self, "timeline_plan", None)
        if not plan or not plan.get("scenes"):
            raise ValueError("No timeline plan — plan the storyboard first")

        concept = {
            "title": "studio lead performer",
            "style": (plan.get("direction") or "")[:180],
            "narrative": plan.get("direction") or "",
            "direction": plan.get("direction") or "",
        }
        await self._ensure_character_lock(concept)
        await self.ensure_identity_sheet(concept, allow_text_only=True)
        plan["character"] = self._studio_character_line()
        plan["character_lock"] = self.character_lock
        plan["identity_sheet_url"] = self._studio_identity_url()

        scenes = plan["scenes"]
        if scene_indices is not None:
            indices = [i for i in scene_indices if 0 <= i < len(scenes)]
        else:
            limit = max_scenes if max_scenes is not None else len(scenes)
            indices = list(range(min(limit, len(scenes))))

        board_dir = self.output_dir / "storyboard"
        board_dir.mkdir(parents=True, exist_ok=True)
        refs = self._identity_ref_paths()
        session_id = self.output_dir.name
        seed_base = self._video_seed_base or int(time.time()) % (2**31)
        self._video_seed_base = seed_base

        for n, idx in enumerate(indices):
            scene = scenes[idx]
            self._emit_progress(
                self.PHASE_STORYBOARD,
                int(45 + (n / max(1, len(indices))) * 50),
                f"Storyboard still {n + 1}/{len(indices)}",
            )
            prompt = (
                f"Music video storyboard frame, still photograph, "
                f"{scene.get('prompt') or ''}. "
                f"Same locked performer. {self.NO_ONSCREEN_TEXT}."
            )
            # INTRO_BROLL: avoid forcing face identity
            use_refs = None if scene.get("type") == "INTRO_BROLL" else (refs[:2] or None)
            still_error: Optional[str] = None
            try:
                if self.use_home_agent_stills:
                    img_path = await self._generate_image_via_home_agent(
                        prompt,
                        job_type="storyboard_still",
                        dest_dir=board_dir / "ha_raw",
                        reference_image_paths=use_refs,
                        extras={
                            "scene_index": idx,
                            "scene_type": scene.get("type"),
                            "seed": seed_base + idx * 3,
                        },
                        width=768,
                        height=448,
                    )
                elif self.pipeline_stills == "fal":
                    from server.fal_client import FalClient

                    fal_dest = board_dir / "fal_raw" / f"still_{idx:02d}.png"
                    img_path = await FalClient().generate_image(
                        prompt,
                        dest=fal_dest,
                        width=768,
                        height=448,
                    )
                else:
                    img_path = await self.comfyui.generate_image_turbo(
                        prompt=prompt,
                        width=768,
                        height=448,
                        seed=seed_base + idx * 3,
                        reference_image_paths=use_refs,
                    )
            except Exception as exc:
                LOG.warning("Storyboard still %d failed: %s", idx, exc)
                img_path = None
                still_error = str(exc)

            if img_path and Path(img_path).is_file():
                dest = board_dir / f"studio_still_{idx:02d}.png"
                if Path(img_path).resolve() != dest.resolve():
                    shutil.copy(img_path, dest)
                else:
                    dest = Path(img_path)
                scene["still_path"] = str(dest)
                scene["still_url"] = (
                    f"/api/session/{session_id}/asset/storyboard/{dest.name}"
                )
                scene.pop("error", None)
                if scene.get("status") in (None, "queued", "still_failed"):
                    scene["status"] = "queued"
                if self.use_home_agent_stills:
                    scene["still_source"] = "home_agent"
                elif self.pipeline_stills == "fal":
                    scene["still_source"] = "fal"
                else:
                    scene["still_source"] = self.comfy_backend
            else:
                scene["status"] = "still_failed"
                scene["error"] = still_error or "Still generation returned no image"
                scene.pop("still_url", None)
            plan_path = self.output_dir / "timeline_plan.json"
            plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
            self.timeline_plan = plan

        succeeded = sum(
            1
            for i in indices
            if scenes[i].get("still_url") and scenes[i].get("still_path")
        )
        plan["identity_sheet_url"] = self._studio_identity_url()
        if succeeded == 0 and indices:
            plan["review_status"] = "stills_failed"
            plan_path = self.output_dir / "timeline_plan.json"
            plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
            self.timeline_plan = plan
            # Prefer first scene error (often quota) for the API message
            first_err = next(
                (
                    str(scenes[i].get("error") or "")
                    for i in indices
                    if scenes[i].get("error")
                ),
                "All storyboard stills failed",
            )
            raise RuntimeError(first_err or "All storyboard stills failed")

        plan["review_status"] = "stills_ready"
        plan_path = self.output_dir / "timeline_plan.json"
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        self.timeline_plan = plan
        self._emit_progress(
            self.PHASE_STORYBOARD,
            100,
            "Storyboard stills ready — review before spending H3 credits",
        )
        return plan

    async def render_studio_scene(
        self,
        scene_index: int,
        *,
        plan: Optional[Dict[str, Any]] = None,
        direction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Render a single timeline scene with MiniMax H3."""
        plan = plan or getattr(self, "timeline_plan", None)
        if not plan:
            plan_path = self.output_dir / "timeline_plan.json"
            if plan_path.is_file():
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                self.timeline_plan = plan
        if not plan or not plan.get("scenes"):
            raise ValueError("No timeline plan — call build_studio_timeline_plan first")

        scenes = plan["scenes"]
        if scene_index < 0 or scene_index >= len(scenes):
            raise ValueError(f"Scene index {scene_index} out of range")

        # Always lock a face for R2V so identity + lip-sync stay stable
        concept = {
            "title": "studio lead performer",
            "style": (direction or plan.get("direction") or "")[:180],
            "narrative": direction or plan.get("direction") or "",
            "direction": direction or plan.get("direction") or "",
        }
        if not self.character_lock:
            await self._ensure_character_lock(concept)
        await self.ensure_identity_sheet(concept, allow_text_only=True)
        plan["character"] = self._studio_character_line()
        plan["character_lock"] = self.character_lock
        plan["identity_sheet_url"] = self._studio_identity_url()

        scene = scenes[scene_index]
        audio_src = self.audio_path
        if not audio_src or not Path(audio_src).is_file():
            raise ValueError("No audio path on director")

        from server.pipeline import engine_label, resolve_scene_engine

        session_dir = self.output_dir / "videos"
        session_dir.mkdir(parents=True, exist_ok=True)
        scene["status"] = "rendering"

        still: Optional[str] = None
        stype = str(scene.get("type") or "")
        # Prefer per-scene storyboard still, then identity / refs
        still_candidate = scene.get("still_path") or scene.get("still_local")
        if still_candidate and Path(str(still_candidate)).is_file():
            still = str(still_candidate)
        elif stype != "INTRO_BROLL":
            if self.identity_sheet_path and Path(self.identity_sheet_path).is_file():
                still = self.identity_sheet_path
            elif self.reference_images:
                for ref in self.reference_images:
                    if ref and Path(ref).is_file():
                        still = ref
                        break

        engine = resolve_scene_engine(
            self.video_model or self.video_backend or "minimax-h3",
            stype,
            has_image=bool(still),
            override=scene.get("video_engine") or scene.get("engine_override"),
        )
        use_fal_host = self.pipeline_video == "fal"
        progress_engine = "FAL draft" if use_fal_host else engine_label(engine)
        self._emit_progress(
            self.PHASE_VIDEO,
            int(10 + (scene_index / max(1, len(scenes))) * 80),
            f"Rendering {progress_engine} scene "
            f"{scene_index + 1}/{len(scenes)} ({scene.get('type')})",
        )

        # Align frames to the selected engine, then slice audio to match.
        gen_secs = min(14.5, max(3.0, float(scene.get("duration") or 8.0)))
        if engine == "ltx23":
            from server.image_generator import _ltx_align_frames

            fps = float(os.getenv("COMFY_LTX_FPS", "24"))
            frames = _ltx_align_frames(max(9, int(round(gen_secs * fps))))
            frames = min(frames, int(os.getenv("COMFY_LTX_MAX_FRAMES", "97")))
            frames = _ltx_align_frames(frames)
            width = int(os.getenv("COMFY_LTX_WIDTH", os.getenv("COMFY_H3_WIDTH", "768")))
            height = int(
                os.getenv("COMFY_LTX_HEIGHT", os.getenv("COMFY_H3_HEIGHT", "512"))
            )
        else:
            fps = float(
                os.getenv(
                    "COMFY_FASTH3_FPS" if engine == "fasth3" else "COMFY_H3_FPS",
                    "24",
                )
            )
            frames = _h3_align_frames(max(5, int(round(gen_secs * fps))))
            max_fr = int(
                os.getenv(
                    "COMFY_FASTH3_MAX_FRAMES"
                    if engine == "fasth3"
                    else "COMFY_H3_MAX_FRAMES",
                    "362",
                )
            )
            frames = min(frames, max_fr)
            frames = _h3_align_frames(frames)
            width = int(os.getenv("COMFY_H3_WIDTH", "768"))
            height = int(os.getenv("COMFY_H3_HEIGHT", "448"))
        aligned_secs = frames / fps

        start_t = float(scene.get("start") or 0.0)
        audio_slice = session_dir / f"studio_audio_{scene_index:02d}.wav"
        self._slice_audio(
            audio_src,
            audio_slice,
            start_t,
            aligned_secs,
            sample_rate="44100",
            channels="2",
            normalize=False,
        )

        prompt = str(scene.get("prompt") or "").strip()
        character = plan.get("character_lock") or plan.get("character") or ""
        if character and character.lower() not in prompt.lower():
            prompt = f"{character}. {prompt}"
        if direction and direction.strip():
            if direction.strip().lower() not in prompt.lower():
                prompt = f"{direction.strip()}. {prompt}"
        if stype == "PERF" and engine == "minimax-h3":
            prompt = (
                f"{prompt} Mouth clearly visible; lips precisely match <Audio 1> phonemes."
            )
        elif engine in ("ltx23", "fasth3"):
            prompt = (
                f"{prompt} Natural mouth motion matching the song energy; "
                f"cinematic music video, consistent identity. "
                f"NO text, NO captions, NO letters."
            )

        if self._video_seed_base is None:
            self._video_seed_base = int(time.time()) % (2**31)
        seed = self._video_seed_base + scene_index

        use_ha_h3 = (
            engine == "minimax-h3"
            and (self.use_home_agent_video or self.pipeline_video == "home_agent")
        )
        use_fal = use_fal_host

        if use_fal:
            video_path = await self._render_via_fal(
                prompt=prompt,
                audio_path=str(audio_slice),
                image_path=still,
                num_frames=frames,
                seed=seed,
                duration=aligned_secs,
                scene_index=scene_index,
            )
        elif use_ha_h3:
            video_path = await self._render_h3_via_home_agent(
                prompt=prompt,
                audio_path=str(audio_slice),
                image_path=still,
                num_frames=frames,
                seed=seed,
                duration=aligned_secs,
                width=width,
                height=height,
                scene_index=scene_index,
            )
        elif engine == "ltx23":
            if not still:
                raise RuntimeError(
                    f"LTX IA2V needs a still for scene {scene_index}; "
                    "generate storyboard stills or upload a ref"
                )
            video_path = await self.comfyui.generate_video_i2v(
                image_path=still,
                prompt=prompt,
                num_frames=frames,
                seed=seed,
                audio_path=str(audio_slice),
                duration=aligned_secs,
                width=width,
                height=height,
                backend="ltx23",
            )
        elif engine == "fasth3":
            video_path = await self.comfyui.generate_video_fasth3(
                prompt=prompt,
                audio_path=str(audio_slice),
                image_path=still,
                num_frames=frames,
                seed=seed,
                duration=aligned_secs,
                width=width,
                height=height,
            )
        else:
            video_path = await self.comfyui.generate_video_h3(
                prompt=prompt,
                audio_path=str(audio_slice),
                image_path=still,
                num_frames=frames,
                seed=seed,
                duration=aligned_secs,
                width=width,
                height=height,
            )

        if not video_path or not Path(video_path).is_file():
            scene["status"] = "failed"
            plan_path = self.output_dir / "timeline_plan.json"
            plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
            raise RuntimeError(
                f"{'FAL draft' if use_fal else engine_label(engine)} "
                f"render failed for scene {scene_index}"
            )

        dest = session_dir / f"studio_{scene_index:02d}.mp4"
        shutil.copy(video_path, dest)
        session_id = self.output_dir.name
        scene["status"] = "ready"
        scene["video_path"] = str(dest)
        scene["video_url"] = f"/api/session/{session_id}/asset/video/{dest.name}"
        scene["audio_aligned_secs"] = round(aligned_secs, 3)
        scene["rendered_at"] = time.time()
        scene["video_engine"] = "fal" if use_fal else engine
        scene["video_model"] = self.video_model
        scene["video_source"] = (
            "fal"
            if use_fal
            else (
                "home_agent"
                if use_ha_h3
                else self.comfy_backend
            )
        )
        plan_path = self.output_dir / "timeline_plan.json"
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        self.timeline_plan = plan
        return scene

    async def _render_h3_via_home_agent(
        self,
        *,
        prompt: str,
        audio_path: str,
        image_path: Optional[str],
        num_frames: int,
        seed: int,
        duration: float,
        width: int,
        height: int,
        scene_index: int,
    ) -> Optional[str]:
        """Ask Playground Home Agent to run MiniMax H3 and return a clip."""
        if not self.home_agent_url or not self.home_agent_password:
            raise RuntimeError("Home Agent URL/password required for H3 via Home Agent")

        from server.home_agent.client import HomeAgentClient
        from server.home_agent.jobs import encode_local_images, run_media_job

        out_dir = self.output_dir / "videos" / "ha_raw"
        out_dir.mkdir(parents=True, exist_ok=True)
        client = HomeAgentClient(
            self.home_agent_url,
            self.home_agent_password,
            timeout=float(os.getenv("HOME_AGENT_TIMEOUT", "600")),
        )
        refs = [image_path] if image_path else None
        images = encode_local_images(refs, limit=1)
        # Attach short audio as a second "image-like" payload when protocol allows;
        # also pass path hint in extras for agents that can read shared storage.
        extras: Dict[str, Any] = {
            "workflow": "minimax_h3_r2v",
            "num_frames": num_frames,
            "duration_secs": duration,
            "width": width,
            "height": height,
            "seed": seed,
            "scene_index": scene_index,
            "audio_path_hint": audio_path,
            "require_video_attachment": True,
        }
        audio_file = Path(audio_path)
        if audio_file.is_file() and audio_file.stat().st_size < 4_000_000:
            import base64

            images = list(images or [])
            images.append(
                {
                    "mime": "audio/wav",
                    "data_base64": base64.b64encode(audio_file.read_bytes()).decode(
                        "ascii"
                    ),
                    "filename": audio_file.name,
                }
            )

        result = await run_media_job(
            client,
            job_type="h3_r2v",
            prompt=prompt,
            dest_dir=out_dir,
            extras=extras,
            images=images or None,
            wait_timeout=float(os.getenv("HOME_AGENT_TIMEOUT", "600")),
        )
        saved = result.get("saved_paths") or []
        for path in saved:
            if str(path).lower().endswith((".mp4", ".webm", ".mov")):
                return str(path)
        if saved:
            return str(saved[0])
        LOG.warning(
            "Home Agent H3 job produced no video (reply=%r)",
            (result.get("reply_text") or "")[:240],
        )
        return None

    async def _render_via_fal(
        self,
        *,
        prompt: str,
        audio_path: str,
        image_path: Optional[str],
        num_frames: int,
        seed: int,
        duration: float,
        scene_index: int,
    ) -> Optional[str]:
        """Draft clip via FAL free-tier cloud; remux SceneMux audio slice."""
        from server.fal_client import FalClient, FalError

        out_dir = self.output_dir / "videos" / "fal_raw"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"fal_{scene_index:02d}.mp4"
        try:
            client = FalClient()
            path = await client.generate_video(
                prompt=prompt,
                dest=dest,
                image_path=image_path,
                duration=min(6.0, float(duration)),
                seed=seed,
            )
        except FalError as exc:
            raise RuntimeError(str(exc)) from exc
        if not path or not Path(path).is_file():
            return None
        # Replace FAL soundtrack with our aligned song slice
        muxed = out_dir / f"fal_{scene_index:02d}_mux.mp4"
        try:
            self._mux_video_audio(path, audio_path, muxed)
            return str(muxed)
        except Exception as exc:
            LOG.warning("FAL audio remux failed (%s); using FAL audio", exc)
            return path

    def _mux_video_audio(self, video_path: str, audio_path: str, dest: Path) -> None:
        """Replace clip audio with a SceneMux-aligned slice (shortest)."""
        import subprocess

        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(dest),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not dest.is_file():
            raise RuntimeError(
                (result.stderr or result.stdout or "ffmpeg mux failed")[:400]
            )

    async def render_studio_all(
        self,
        direction: str,
        *,
        rebuild_plan: bool = True,
    ) -> Dict[str, Any]:
        """Plan (optional) + render every scene, then assemble final MP4."""
        self.clear_studio_cancel()
        if rebuild_plan or not getattr(self, "timeline_plan", None):
            await self.build_studio_timeline_plan(direction, lock_identity=True)
        else:
            concept = {
                "title": "studio lead performer",
                "style": direction[:180],
                "narrative": direction,
                "direction": direction,
            }
            if not self.character_lock:
                await self._ensure_character_lock(concept)
            await self.ensure_identity_sheet(concept, allow_text_only=True)

        plan = self.timeline_plan
        if plan is not None:
            plan["review_status"] = "rendering"
        scenes = plan.get("scenes") or []
        for i in range(len(scenes)):
            if self._studio_cancel:
                LOG.info("Studio render cancelled before scene %d", i)
                if plan is not None:
                    plan["review_status"] = "cancelled"
                break
            if scenes[i].get("status") == "ready" and scenes[i].get("video_path"):
                continue
            try:
                await self.render_studio_scene(i, plan=plan, direction=direction)
            except Exception as exc:
                LOG.warning("Studio scene %d failed: %s", i, exc)
                scenes[i]["status"] = "failed"
                scenes[i]["error"] = str(exc)
            if self._studio_cancel:
                if plan is not None:
                    plan["review_status"] = "cancelled"
                break

        ready = [s for s in scenes if s.get("status") == "ready" and s.get("video_path")]
        cancelled = bool(self._studio_cancel) or (
            plan is not None and plan.get("review_status") == "cancelled"
        )
        if not ready:
            if cancelled:
                return {
                    "timeline_plan": plan,
                    "final_video_path": None,
                    "ready_count": 0,
                    "scene_count": len(scenes),
                    "cancelled": True,
                }
            raise RuntimeError("No scenes rendered successfully")

        final_path = await self.assemble_studio_final()
        if plan is not None and not cancelled:
            plan["review_status"] = "complete"
        return {
            "timeline_plan": plan,
            "final_video_path": final_path,
            "ready_count": len(ready),
            "scene_count": len(scenes),
            "cancelled": cancelled,
        }

    async def assemble_studio_final(self) -> str:
        """Concat ready studio scenes and mux the original master MP3."""
        plan = getattr(self, "timeline_plan", None)
        if not plan:
            plan_path = self.output_dir / "timeline_plan.json"
            if plan_path.is_file():
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                self.timeline_plan = plan
        if not plan:
            raise ValueError("No timeline plan to assemble")

        audio_src = self.audio_path
        if not audio_src or not Path(audio_src).is_file():
            raise ValueError("No audio for final mux")

        specs: List[Dict[str, Any]] = []
        for scene in plan.get("scenes") or []:
            path = scene.get("video_path")
            if not path or not Path(path).is_file():
                continue
            specs.append(
                {
                    "path": path,
                    "duration": float(scene.get("duration") or 0),
                    "start": float(scene.get("start") or 0),
                    # Trim/pad to exact lyric window — never whole-song loop
                    "fill_mode": "freeze",
                }
            )
        if not specs:
            raise RuntimeError("No rendered studio clips to assemble")

        # Mirror into storyboard so assemble_final_video can use rich specs
        self.storyboard = [
            {
                "id": f"studio_{i:02d}",
                "timestamp": s["start"],
                "duration": s["duration"],
                "target_duration": s["duration"],
                "video_path": s["path"],
                "shot_type": "avatar",
                "fill_mode": "freeze",
            }
            for i, s in enumerate(specs)
        ]
        return await self.assemble_final_video(specs, audio_src)

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
                        "fill_mode": frame.get("fill_mode")
                        or (
                            "freeze"
                            if (frame.get("shot_type") or "") == "avatar"
                            else "loop"
                        ),
                    }
                )
        if not specs:
            for item in video_clips:
                if isinstance(item, dict) and item.get("path"):
                    specs.append(
                        {
                            "path": item["path"],
                            "duration": float(item.get("duration") or 0),
                            "fill_mode": item.get("fill_mode") or "freeze",
                        }
                    )
                elif isinstance(item, str):
                    specs.append({"path": item, "duration": 0, "fill_mode": "freeze"})

        song_dur = float((self.audio_analysis or {}).get("duration") or 0) or None
        editor = VideoEditor(audio_path)
        final_path = editor.assemble(
            specs,
            output_path,
            song_duration=song_dur,
            fill_mode=os.getenv("VIDMUSE_CLIP_FILL", "freeze"),
        )
        if self.project_mode == "typography":
            typography_path = self.output_dir / f"vidmuse_{session_id}_typography.mp4"
            self._emit_progress(
                self.PHASE_ASSEMBLE, 85, "Compositing exact lyric typography…"
            )
            final_path = editor.apply_typography(
                final_path,
                self.storyboard,
                typography_path,
            )

        self._emit_progress(self.PHASE_ASSEMBLE, 94, "Running final media checks…")
        qc = editor.technical_qc(final_path, expected_duration=song_dur)
        qc_path = self.output_dir / "technical_qc.json"
        qc_path.write_text(json.dumps(qc, indent=2), encoding="utf-8")

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

        # Phase 4: Generate reference images (skipped when user uploads identity stills)
        if reference_images:
            self.reference_images = [str(p) for p in reference_images if Path(p).is_file()]
        if reference_image_prompt and not self.reference_images:
            await self.generate_reference_images(self.selected_concept, reference_image_prompt)
        elif not self.reference_images:
            await self.generate_reference_images(self.selected_concept)
        else:
            LOG.info("Skipping AI refs — using %d user uploads", len(self.reference_images))

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
