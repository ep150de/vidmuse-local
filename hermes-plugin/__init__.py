"""SceneMux plugin for Hermes Agent.

Exposes the SceneMux music-video studio as an eight-tool toolset plus the
bundled ``scenemux:studio`` operator skill.

    __init__.py  entrypoint: registers the tools and the skill
    plugin.yaml  manifest: name, version, config schema, requires_env
    schemas.py   tool JSON schemas
    tools.py     handler factories (make_* -> handler(args, **kw) -> str)
    client.py    stdlib-only HTTP client for the SceneMux Studio API
    skills/studio/SKILL.md  operator workflow loaded via skill_view()
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import schemas, tools

logger = logging.getLogger(__name__)

TOOLSET = "scenemux"

# (factory, schema, emoji); the tool name comes from the schema, so the
# manifest's provides_tools and these entries cannot drift apart silently.
_TOOLS = (
    (tools.make_doctor, schemas.DOCTOR, "🩺"),
    (tools.make_setup, schemas.SETUP, "⚙️"),
    (tools.make_project, schemas.PROJECT, "🎬"),
    (tools.make_plan, schemas.PLAN, "📋"),
    (tools.make_render, schemas.RENDER, "🎞️"),
    (tools.make_status, schemas.STATUS, "📊"),
    (tools.make_export, schemas.EXPORT, "📦"),
    (tools.make_cancel, schemas.CANCEL, "🛑"),
)

_SKILL_FILE = Path(__file__).resolve().parent / "skills" / "studio" / "SKILL.md"
_SKILL_DESCRIPTION = (
    "Drive the SceneMux music-video studio: turn an MP3 into a beat-aligned, "
    "lip-synced music video - plan the storyboard, review stills, render "
    "scenes and deliver a muxed MP4."
)


def register(ctx) -> None:
    """Register the scenemux toolset and its studio skill.

    Called once by the plugin loader while the plugin is enabled. Never
    raises: a failed registration is logged and skipped, so one bad tool
    cannot take the whole plugin down.
    """
    for factory, schema, emoji in _TOOLS:
        name = str(schema.get("name") or "").strip()
        if not name:
            continue
        try:
            ctx.register_tool(
                name=name,
                toolset=TOOLSET,
                schema=schema,
                handler=factory(ctx),
                emoji=emoji,
            )
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.debug("scenemux: could not register tool %s: %s", name, exc)

    try:
        if _SKILL_FILE.is_file():
            ctx.register_skill(
                name="studio",
                path=_SKILL_FILE,
                description=_SKILL_DESCRIPTION,
            )
        else:
            logger.debug("scenemux: studio skill file missing at %s", _SKILL_FILE)
    except Exception as exc:  # noqa: BLE001 - defensive
        logger.debug("scenemux: could not register studio skill: %s", exc)
