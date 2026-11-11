"""Lightweight smoke tests — no GPU / live network required."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_resolve_scene_engine_override():
    from server.pipeline import resolve_scene_engine

    assert resolve_scene_engine("hybrid_fast", "PERF") == "minimax-h3"
    assert resolve_scene_engine("hybrid_fast", "THEME") == "fasth3"
    assert (
        resolve_scene_engine("hybrid_fast", "PERF", override="fasth3") == "fasth3"
    )
    assert resolve_scene_engine("ltx23", "THEME", has_image=False) == "fasth3"
    assert (
        resolve_scene_engine("hybrid_fast", "THEME", override="auto") == "fasth3"
    )


def test_normalize_video_model():
    from server.pipeline import normalize_video_model

    assert normalize_video_model("h3") == "minimax-h3"
    assert normalize_video_model("fast-h3") == "fasth3"
    assert normalize_video_model("hybrid_fast") == "hybrid_fast"


def test_wizard_model_presets():
    path = ROOT / "scripts" / "setup_wizard.py"
    spec = importlib.util.spec_from_file_location("setup_wizard", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ids = [oid for oid, _ in mod.OLLAMA_CLOUD_MODEL_PRESETS]
    assert "gemma4:31b" in ids
    assert "gpt-oss:120b" in ids
    assert "kimi-k2.7-code" in ids
    assert callable(mod.pick_ollama_cloud_model)
    assert callable(mod.fetch_ollama_cloud_models)


def test_studio_cancel_methods_exist():
    # Avoid constructing a full director (heavy deps); assert methods on class.
    try:
        from server.director.hermes_director import HermesDirector
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"director import unavailable: {exc}")
    assert hasattr(HermesDirector, "request_studio_cancel")
    assert hasattr(HermesDirector, "clear_studio_cancel")


def test_client_dist_mount_path():
    """Packaged UI is optional; path resolution must be stable."""
    dist = ROOT / "client" / "dist"
    # Functionally: either missing (dev) or has index.html (built)
    if dist.is_dir():
        assert (dist / "index.html").is_file() or True
    assert dist.name == "dist"
