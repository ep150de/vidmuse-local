"""SceneMux plugin for Hermes Agent.

Exposes the SceneMux music-video studio as an eight-tool toolset, a
spend-gate ``pre_tool_call`` hook, and the bundled ``scenemux:studio``
operator skill.

    __init__.py  entrypoint: registers the tools, the spend-gate hook, and the skill
    plugin.yaml  manifest: name, version, config schema, requires_env
    schemas.py   tool JSON schemas
    tools.py     handler factories (make_* -> handler(args, **kw) -> str)
    client.py    stdlib-only HTTP client for the SceneMux Studio API
    skills/studio/SKILL.md  operator workflow loaded via skill_view()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

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


def _render_spend_gate(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    """Spend gate for ``scenemux_render`` (Hermes approval system, rule 12).

    A render call with ``confirm=true`` is escalated to Hermes' human
    approval gate via the ``approve`` directive: the render — which spends
    API credits — does not start until a person approves it at that prompt.
    Denial, timeout, or gate error fails closed (the call is not executed).
    The prompt quotes the last estimate the tool fetched for the session.
    """
    if tool_name != "scenemux_render" or not isinstance(args, dict):
        return None
    if not bool(args.get("confirm", False)):
        return None
    sid = str(args.get("session_id") or "")
    est = tools.get_render_estimate(sid)
    cost = str(est.get("cost_hint") or "").strip() or "unknown cost"
    time_hint = ""
    try:
        hi = int(est.get("est_minutes_high") or 0)
        lo = int(est.get("est_minutes_low") or 0)
        if hi > 0:
            time_hint = f", est. {lo}-{hi} min"
    except (TypeError, ValueError):
        pass
    return {
        "action": "approve",
        "message": (
            f"SceneMux render for session {sid or '?'} will SPEND API credits "
            f"({cost}{time_hint}). Approve to start the render — it runs "
            "asynchronously and can be stopped with scenemux_cancel."
        ),
    }


def register(ctx) -> None:
    """Register the scenemux toolset, its spend-gate hook, and its studio skill.

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
        ctx.register_hook("pre_tool_call", _render_spend_gate)
    except Exception as exc:  # noqa: BLE001 - defensive
        logger.debug("scenemux: could not register the spend-gate hook: %s", exc)

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
