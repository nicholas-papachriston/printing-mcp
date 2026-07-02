from __future__ import annotations

import os

from ..config import load_local_env, local_env_value

IMAGE_COST_USD = 0.0
FAST_MESH_COST_USD = 0.0
WARMUP_COST_USD = 0.0
DEFAULT_MAX_IMAGE_COST_USD = 0.03
DEFAULT_MAX_FAST_MESH_COST_USD = 0.08
DEFAULT_MAX_QUALITY_MESH_COST_USD = 0.22
DEFAULT_MAX_WARMUP_COST_USD = 0.50


def estimate_image_cost_usd() -> float:
    return IMAGE_COST_USD


def estimate_fast_mesh_cost_usd() -> float:
    return FAST_MESH_COST_USD


def estimate_warmup_cost_usd() -> float:
    return WARMUP_COST_USD


def max_image_cost_usd() -> float:
    return _env_float("PRINTING_GENERATION_MAX_IMAGE_COST_USD", DEFAULT_MAX_IMAGE_COST_USD)


def max_fast_mesh_cost_usd() -> float:
    return _env_float("PRINTING_GENERATION_MAX_FAST_MESH_COST_USD", DEFAULT_MAX_FAST_MESH_COST_USD)


def max_quality_mesh_cost_usd() -> float:
    return _env_float(
        "PRINTING_GENERATION_MAX_QUALITY_MESH_COST_USD", DEFAULT_MAX_QUALITY_MESH_COST_USD
    )


def max_warmup_cost_usd() -> float:
    return _env_float("PRINTING_GENERATION_MAX_WARMUP_COST_USD", DEFAULT_MAX_WARMUP_COST_USD)


def _env_float(name: str, fallback: float) -> float:
    load_local_env()
    raw_value = os.environ.get(name) or local_env_value(name)
    if raw_value is None or not raw_value.strip():
        return fallback
    try:
        value = float(raw_value)
    except ValueError:
        return fallback
    return value if value > 0 else fallback
